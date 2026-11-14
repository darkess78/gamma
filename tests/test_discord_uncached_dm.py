"""Exercise discord.py's actual uncached MESSAGE_CREATE path without logging in."""
import asyncio

import discord

from gamma.coordination.discord import _private_dm, build_discord_client
from test_private_interaction import setup


def user(identifier, *, bot=False):
    return {'id': str(identifier), 'username': 'synthetic-bot' if bot else 'synthetic-owner',
            'discriminator': '0', 'avatar': None, 'bot': bot, 'global_name': None}


def incoming(*, channel_id=2, author_id=1):
    return {'id': '100000000000000010', 'channel_id': str(channel_id),
            'author': user(author_id), 'content': '!shana join',
            'timestamp': '2026-10-31T00:00:00+00:00', 'edited_timestamp': None,
            'tts': False, 'mention_everyone': False, 'mentions': [], 'mention_roles': [],
            'attachments': [], 'embeds': [], 'pinned': False, 'type': 0,
            'flags': 0, 'components': []}


def test_actual_sdk_uncached_owner_dm_dispatch_resolves_only_pinned_owner(tmp_path, monkeypatch):
    core, interaction = setup(tmp_path)
    created, parsed, results, commands = [], [], [], []

    async def run():
        client, adapter = build_discord_client(interaction, owner_user_id='1', dm_channel_id='2',
            voice_guild_id='3', voice_channel_id='4')
        await client._async_setup_hook()
        state = client._connection
        state.user = discord.ClientUser(state=state, data=user(5, bot=True))

        async def create_dm(owner_id):
            # This is the sole HTTP seam. No login or actual Discord API call runs.
            assert owner_id == 1
            created.append(owner_id)
            return {'id': '2', 'type': 1, 'recipients': [user(1)]}
        monkeypatch.setattr(client.http, 'start_private_message', create_dm)
        interaction.connect(adapter.dm_consumer)
        adapter._started = True
        delivered = asyncio.Event()
        original = client.on_message
        original_command = adapter.voice_command

        async def record_command(owner, action):
            result = await original_command(owner, action)
            commands.append((owner, action, result))
            return result
        adapter.voice_command = record_command

        @client.event
        async def on_message(message):
            parsed.append({'channel_type': type(message.channel), 'id': message.channel.id,
                           'recipient_before_admission': message.channel.recipient})
            try:
                results.append(await original(message))
            finally:
                delivered.set()

        try:
            assert state._get_private_channel(2) is None
            # Actual ConnectionState parsing, Client.dispatch and SDK callback task.
            state.parse_message_create(incoming())
            await asyncio.wait_for(delivered.wait(), 2)
            assert parsed[0]['channel_type'] is discord.DMChannel
            assert parsed[0]['id'] == 2
            assert parsed[0]['recipient_before_admission'] is None
            assert created == [1]
            assert adapter.stats['ignored'] == 0
            # Admission reached the requested voice command. No guild is attached
            # to this unconnected SDK client, so joining must fail safely.
            assert results == [{'ok': False, 'reason': 'private_owner_room_required'}]
            assert commands == [('1', 'join', {'ok': False, 'reason': 'private_owner_room_required'})]
        finally:
            await adapter.close()
            await client.close()

    try:
        asyncio.run(run())
    finally:
        interaction.close()
        core.close()


def test_actual_sdk_hydrated_dm_allows_exactly_one_owner_recipient():
    async def run():
        client = discord.Client(intents=discord.Intents.none())
        await client._async_setup_hook()
        state = client._connection
        state.user = discord.ClientUser(state=state, data=user(5, bot=True))
        try:
            owner_dm = discord.DMChannel(me=state.user, state=state,
                data={'id':'2', 'type':1, 'recipients':[user(1)]})
            foreign_dm = discord.DMChannel(me=state.user, state=state,
                data={'id':'2', 'type':1, 'recipients':[user(9)]})
            multiple = discord.DMChannel(me=state.user, state=state,
                data={'id':'2', 'type':1, 'recipients':[user(1), user(9)]})
            assert owner_dm.recipient.id == 1 and len(owner_dm.recipients) == 1
            assert _private_dm(owner_dm, '1')
            assert not _private_dm(foreign_dm, '1')
            assert not _private_dm(multiple, '1')
        finally:
            await client.close()
    asyncio.run(run())


async def parse_message_without_gateway(client, payload):
    messages = []
    client._connection.dispatch = lambda name, value: messages.append(value) if name == 'message' else None
    client._connection.parse_message_create(payload)
    assert len(messages) == 1
    return messages[0]


def test_uncached_dm_without_trusted_resolver_remains_unadmitted(tmp_path):
    from gamma.coordination.discord import PrivateDiscordAdapter
    core, interaction = setup(tmp_path)
    async def run():
        client = discord.Client(intents=discord.Intents.none())
        await client._async_setup_hook()
        state = client._connection
        state.user = discord.ClientUser(state=state, data=user(5, bot=True))
        adapter = PrivateDiscordAdapter(client, interaction, owner_user_id='1', dm_channel_id='2')
        adapter._started = True
        try:
            message = await parse_message_without_gateway(client, incoming())
            assert message.channel.recipient is None
            result = await adapter.on_message(message)
            assert result['ok'] is False and adapter.stats['ignored'] == 1
            assert core.store.read('turn', 'discord-dm-' + str(message.id)) is None
        finally:
            await adapter.close()
            await client.close()
    try:
        asyncio.run(run())
    finally:
        interaction.close()
        core.close()


import pytest


@pytest.mark.parametrize('rejection', ['foreign-author', 'foreign-channel', 'bot-author',
    'wrong-recipient', 'group', 'wrong-resolved-channel', 'multiple-recipients'])
def test_resolver_never_admits_other_envelopes_or_recipients(tmp_path, rejection):
    from gamma.coordination.discord import PrivateDiscordAdapter
    core, interaction = setup(tmp_path)
    resolved, send_attempts = [], []
    async def run():
        client = discord.Client(intents=discord.Intents.none())
        await client._async_setup_hook()
        state = client._connection
        state.user = discord.ClientUser(state=state, data=user(5, bot=True))
        data = incoming(author_id=9 if rejection == 'foreign-author' else 1,
                        channel_id=9 if rejection == 'foreign-channel' else 2)
        data['content'] = 'Synthetic private text that must never be admitted.'
        if rejection == 'bot-author':
            data['author']['bot'] = True
        async def resolver():
            resolved.append(True)
            if rejection == 'group':
                return discord.GroupChannel(me=state.user, state=state, data={
                    'id':'2', 'type':3, 'owner_id':'1', 'recipients':[user(1), user(9)],
                    'name': None, 'icon': None})
            recipients = [user(9)] if rejection == 'wrong-recipient' else [user(1)]
            if rejection == 'multiple-recipients':
                recipients.append(user(9))
            return discord.DMChannel(me=state.user, state=state, data={
                'id':'9' if rejection == 'wrong-resolved-channel' else '2',
                'type':1, 'recipients':recipients})
        async def forbidden_send(*args, **kwargs):
            send_attempts.append(True)
            raise AssertionError('No test may send a Discord message')
        client.http.send_message = forbidden_send
        adapter = PrivateDiscordAdapter(client, interaction, owner_user_id='1', dm_channel_id='2',
                                        dm_resolver=resolver)
        interaction.connect(adapter.dm_consumer)
        adapter._started = True
        try:
            message = await parse_message_without_gateway(client, data)
            result = await adapter.on_message(message)
            assert result['ok'] is False and adapter.stats['ignored'] == 1
            assert core.store.read('turn', 'discord-dm-' + str(message.id)) is None
            assert not send_attempts
            if rejection in {'foreign-author', 'foreign-channel', 'bot-author'}:
                assert not resolved
            else:
                assert resolved == [True]
        finally:
            await adapter.close()
            await client.close()
    try:
        asyncio.run(run())
    finally:
        interaction.close()
        core.close()


def test_actual_permanent_runner_sdk_callback_produces_one_fixed_command_receipt(tmp_path, monkeypatch):
    from gamma.coordination.discord_release import serve
    from test_discord_release import BINDING, EXPECTED
    core, interaction = setup(tmp_path / 'core')
    interaction.check_scope = lambda: dict(EXPECTED)
    interaction.failed = False
    sends, creates, starts = [], [], []

    async def run():
        stop = asyncio.Event()
        def actual_sdk_factory(actual_interaction, **options):
            assert actual_interaction is interaction
            client, adapter = build_discord_client(actual_interaction, **options)

            async def create_dm(owner_id):
                assert owner_id == 1
                creates.append(owner_id)
                return {'id': '2', 'type': 1, 'recipients': [user(1)]}

            async def send_message(channel_id, *, params):
                assert channel_id == 2
                payload = params.payload
                sends.append({'channel_id': channel_id, 'content': payload['content'],
                              'allowed_mentions': payload['allowed_mentions']})
                reply = incoming()
                reply.update(id='100000000000000020', author=user(5, bot=True),
                             content=payload['content'])
                stop.set()
                return reply

            async def gateway_start(token, *, reconnect):
                assert token == 'synthetic-no-network-token' and reconnect is True
                starts.append(True)
                await client._async_setup_hook()
                state = client._connection
                state.user = discord.ClientUser(state=state, data=user(5, bot=True))
                await client.on_ready()
                payload = incoming()
                payload['content'] = '!shana status'
                # Keep the SDK parser, Client.dispatch, real build callback,
                # permanent wrapper and actual adapter/controller in this path.
                state.parse_message_create(payload)
                await asyncio.Event().wait()

            monkeypatch.setattr(client.http, 'start_private_message', create_dm)
            monkeypatch.setattr(client.http, 'send_message', send_message)
            monkeypatch.setattr(client, 'start', gateway_start)
            return client, adapter

        await asyncio.wait_for(serve(interaction, BINDING,
            {'bot_token':'synthetic-no-network-token'}, tmp_path / 'status.json',
            stop=stop, client_factory=actual_sdk_factory), 3)

    try:
        asyncio.run(run())
        assert creates == [1] and starts == [True]
        assert len(sends) == 1
        assert sends[0]['content'] == 'Shana voice: disconnected.'
        assert sends[0]['allowed_mentions']['parse'] == []
        # This command must not become model-backed conversation input.
        assert core.store.read('turn', 'discord-dm-100000000000000010') is None
        import json
        state = json.loads((tmp_path / 'status.json').read_text())
        assert state['connected'] is False and state['phase'] == 'closed'
        assert 'synthetic-no-network-token' not in json.dumps(state)
    finally:
        interaction.close()
        core.close()
