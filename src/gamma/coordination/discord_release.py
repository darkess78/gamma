"""Owner-authorized Discord transport bound to one permanent private release."""
import asyncio
import json
import os
from pathlib import Path
import signal
import stat
import time

from .discord import PrivateDiscordAdapter, build_discord_client, _call
from .discord_client import RemotePrivateInteraction
from .private_release import runtime_manifest, release_api_bearer
from .private_release_manifest import digest, validate_discord_binding
from .store import process_lock


def private_voice_permissions(channel, owner_id, bot_id):
    """Require a channel permission boundary as well as current occupancy.

    Discord administrators retain the platform's inherent override.
    """
    boundary = (1 << 10) | (1 << 20)  # VIEW_CHANNEL or CONNECT must stay denied.
    everyone = channel.guild.id
    closed = 0
    rows = []
    for target, overwrite in channel.overwrites.items():
        allow, deny = overwrite.pair()
        rows.append((str(target.id), allow.value, deny.value))
        if target.id == everyone:
            closed = deny.value & boundary & ~allow.value
    for target, allow, _deny in rows:
        if target not in {str(everyone), owner_id, bot_id}:
            closed &= ~allow
    return bool(closed)


def read_credentials(path, binding):
    path = Path(path)
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('private_discord_credentials_path_invalid')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(descriptor, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_size > 8192
                or os.name == 'posix' and (info.st_uid != os.getuid() or info.st_mode & 0o077)):
            raise ValueError('private_discord_credentials_not_owned_private_regular_file')
        value = json.loads(handle.read(8193))
    if (not isinstance(value, dict) or set(value) != {'format', 'bot_token', *binding}
            or type(value['format']) is not int or value['format'] != 1
            or any(value[k] != v for k, v in binding.items())
            or not isinstance(value['bot_token'], str) or not 20 <= len(value['bot_token']) <= 200
            or any(c.isspace() for c in value['bot_token'])):
        raise ValueError('private_discord_credentials_binding_mismatch')
    return value


class ReleaseDiscordAdapter(PrivateDiscordAdapter):
    async def voice_command(self, user_id, action):
        result = await super().voice_command(user_id, action)
        if action in {'join', 'resume'} and result.get('ok') and result.get('state') == 'listening':
            try:
                await _call(self.interaction.control_consumer, self.voice_consumer, 'voice_enabled', True)
                await _call(self.interaction.control_consumer, self.voice_consumer, 'speech_enabled', True)
            except Exception:
                await super().voice_command(user_id, 'leave')
                raise
        return result


def command_feedback(result):
    if result.get('ok'):
        return 'Shana voice: ' + str(result.get('state', 'ready')) + '.'
    if result.get('reason') in {'private_owner_room_required', 'voice_audience_changed'}:
        return 'Join Shana Voice by yourself first. The channel must allow only you and this bot to join.'
    return 'Voice command could not complete. Use !shana join, leave, pause, resume or status.'


def write_status(path, **values):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as handle:
        os.chmod(temporary, 0o600)
        json.dump({'updated_at': time.time(), **values}, handle)
    temporary.replace(path)


async def serve(interaction, binding, credentials, status_path, *, stop=None, client_factory=build_discord_client):
    scope = {k: v for k, v in binding.items() if k != 'bot_user_id'}
    client, adapter = client_factory(interaction, **scope, adapter_class=ReleaseDiscordAdapter,
                                     room_policy=private_voice_permissions)
    stop = stop or asyncio.Event()
    fault = asyncio.get_running_loop().create_future()
    generation = None
    ready = False

    def status(connected, **extra):
        write_status(status_path, connected=connected, scope='owner_private',
                     voice_state=getattr(adapter._controller, '_state', 'offline'),
                     generation=generation, counters=dict(adapter.stats),
                     voice_timings=getattr(adapter, 'voice_timing_snapshot', lambda: {})(), **extra)

    async def checked_ready():
        nonlocal generation, ready
        try:
            if str(getattr(client.user, 'id', '')) != binding['bot_user_id']:
                raise ValueError('private_discord_bot_identity_mismatch')
            await asyncio.to_thread(interaction.check_scope)
            await adapter.start()
            state = await asyncio.to_thread(interaction.status, adapter.dm_consumer)
            generation = state['generation']
            ready = True
            status(True, bot_name=str(client.user.name))
        except Exception:
            if not fault.done():fault.set_exception(RuntimeError('private_discord_ready_check_failed'))

    # SDK dispatch names, installed explicitly rather than relying on function name.
    client.on_ready = checked_ready
    client.on_resumed = checked_ready
    original_message = client.on_message

    async def on_message(message):
        if not ready:return
        message = await adapter.prepare_message(message)
        if message is None:return
        is_command = isinstance(message.content, str) and message.content.strip().startswith('!shana')
        result = await original_message(message)
        if is_command:
            # A user-issued command gets one fixed receipt. No transcript, model
            # thought, mentions, reconnect retry or startup greeting is sent here.
            await message.channel.send(command_feedback(result), allowed_mentions=adapter._allowed_mentions)
        status(True, bot_name=str(client.user.name))
    client.on_message = on_message

    async def on_guild_channel_update(_before, after):
        if str(after.id) == binding['voice_channel_id'] and not adapter.voice_room_private():
            await adapter.voice_command(binding['owner_user_id'], 'leave')
    client.on_guild_channel_update = on_guild_channel_update

    async def on_error(_event, *_args, **_kwargs):
        if not fault.done():fault.set_exception(RuntimeError('private_discord_callback_failed'))
    client.on_error = on_error

    async def watch():
        while True:
            await asyncio.sleep(5)
            await asyncio.to_thread(interaction.check_scope)
            state = await asyncio.to_thread(interaction.status, adapter.dm_consumer)
            if interaction.failed or ready and (state['generation'] != generation
                    or adapter._started and not state['consumer']['connected']):
                raise RuntimeError('private_discord_owner_or_lease_changed')
            status(bool(ready and adapter._started), bot_name=str(getattr(client.user, 'name', '')))

    tasks = [asyncio.create_task(client.start(credentials['bot_token'], reconnect=True)),
             asyncio.create_task(watch()), asyncio.create_task(stop.wait())]
    status(False, phase='connecting')
    try:
        done, _ = await asyncio.wait([*tasks, fault], return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task is not tasks[2]:
                task.result()
                raise RuntimeError('private_discord_transport_ended')
    finally:
        for task in tasks:task.cancel()
        if not fault.done():fault.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:await adapter.close()
        finally:
            await client.close()
            status(False, phase='closed')


def main():
    manifest, purpose = runtime_manifest(purpose='permanent')
    binding = validate_discord_binding(manifest.get('discord'))
    if manifest['effects']['live_discord'] != 'owner_private':
        raise ValueError('private_discord_not_enabled_in_release')
    from ..config import settings
    credentials = read_credentials(os.environ.get('GAMMA_PRIVATE_DISCORD_CREDENTIALS_FILE', ''), binding)
    expected = {'release_id': manifest['release_id'], 'manifest_sha256': digest(manifest),
                'identity_id': manifest['state']['identity_id'], 'scope': 'private_release_owner', 'purpose': purpose}
    interaction = RemotePrivateInteraction('http://127.0.0.1:8000',
        release_api_bearer(manifest, purpose, settings.api_bearer_token), expected_release=expected)
    active = Path(manifest['state']['active_root'])
    # Held before connecting any consumer or logging in. A duplicate process
    # cannot reset another runner's fixed transport leases.
    with process_lock(active.parent / 'discord-transport.lock'):
        interaction.check_scope()
        async def run():
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            for signum in (signal.SIGTERM, signal.SIGINT):loop.add_signal_handler(signum, stop.set)
            await serve(interaction, binding, credentials, active / 'discord-transport/status.json', stop=stop)
        asyncio.run(run())


if __name__ == '__main__':
    try:main()
    except Exception as exc:
        print(json.dumps({'connected':False,'status':'failed','error_type':type(exc).__name__}),flush=True)
        raise SystemExit(1)
