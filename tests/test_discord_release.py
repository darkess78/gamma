"""Permanent Discord transport checks use synthetic credentials and no Discord login."""
import asyncio
import copy
import io
import json
import os
from types import SimpleNamespace as NS

import pytest

from gamma.coordination.discord_client import RemotePrivateInteraction
from gamma.coordination.discord_release import (
    ReleaseDiscordAdapter, command_feedback, private_voice_permissions,
    read_credentials, serve,
)
from gamma.coordination.discord_release_scope import (
    release_origin, release_owner_matches, validate_release_binding,
)


EXPECTED = {'release_id': 'a' * 32, 'identity_id': 'b' * 32,
            'manifest_sha256': 'c' * 64, 'scope': 'private_release_owner',
            'purpose': 'permanent'}
BINDING = {'owner_user_id': '1', 'dm_channel_id': '2',
           'voice_guild_id': '3', 'voice_channel_id': '4', 'bot_user_id': '5'}


class Opener:
    def __init__(self, value=None, error=None):
        self.value, self.error, self.requests = value, error, []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if self.error:
            raise self.error
        return io.BytesIO(json.dumps(self.value).encode())


def remote(value=None, **kwargs):
    opener = Opener(copy.deepcopy(EXPECTED) if value is None else value)
    client = RemotePrivateInteraction('http://127.0.0.1:8000', 'synthetic-' + 'x' * 40,
                                      opener=opener, expected_release=EXPECTED, **kwargs)
    return client, opener


@pytest.mark.parametrize('origin', ['http://127.0.0.1:8000', 'http://[::1]:8000/'])
def test_permanent_origin_accepts_only_literal_release_role(origin):
    assert release_origin(origin) == origin.rstrip('/')


@pytest.mark.parametrize('origin', [None, 8000, 'http://localhost:8000',
    'http://127.0.0.1:8001', 'http://127.0.0.1:18000', 'https://127.0.0.1:8000',
    'http://127.0.0.1:8000/path', 'http://127.0.0.1:8000?x=1',
    'http://user@127.0.0.1:8000', 'http://127.0.0.1:8000#x',
    'http://127.0.0.1:8000\n', ' http://127.0.0.1:8000',
    'http://127.0.0.1:8000//'])
def test_permanent_origin_rejects_alternatives_before_request(origin):
    with pytest.raises(ValueError):
        RemotePrivateInteraction(origin, 'x' * 40, expected_release=EXPECTED)


@pytest.mark.parametrize('key,bad', [('release_id', 'a' * 32 + '\n'),
    ('identity_id', 'B' * 32), ('manifest_sha256', 'c' * 64 + '\n'),
    ('scope', 'isolated_owner_private'), ('purpose', 'test')])
def test_expected_owner_binding_is_strict(key, bad):
    changed = dict(EXPECTED, **{key: bad})
    with pytest.raises(ValueError):
        validate_release_binding(changed)
    assert not release_owner_matches(EXPECTED, changed)


def test_exact_owner_metadata_checked_before_consumer_work():
    client, opener = remote(dict(EXPECTED, noncooperating_legacy_writers_fenced=False))
    assert client.check_scope() == EXPECTED
    request, timeout = opener.requests[0]
    assert request.full_url == 'http://127.0.0.1:8000/v1/coordination/private-release'
    assert request.get_header('Authorization').startswith('Bearer synthetic-')
    assert request.get_method() == 'GET' and timeout == 15
    assert len(opener.requests) == 1


@pytest.mark.parametrize('key', list(EXPECTED))
def test_different_release_identity_purpose_or_manifest_is_rejected(key):
    value = dict(EXPECTED)
    value[key] = 'mismatching-owner'
    client, opener = remote(value)
    with pytest.raises(ValueError, match='owner mismatch'):
        client.check_scope()
    assert len(opener.requests) == 1
    assert not any('connect' in request.full_url for request, _ in opener.requests)


def test_request_failure_is_sticky_and_remote_controls_have_explicit_consumer():
    client, opener = remote({'ok': True})
    client.control_consumer('discord-voice', 'voice_enabled', True)
    request, _timeout = opener.requests[-1]
    body = json.loads(request.data)
    assert body['audience'] == 'owner_private' and body['consumer_id'] == 'discord-voice'
    assert body['action'] == 'voice_enabled' and body['value'] is True
    assert request.full_url.endswith('/consumer-control')
    opener.error = OSError('synthetic transport unavailable')
    with pytest.raises(RuntimeError, match='request failed'):
        client.status('discord-dm')
    assert client.failed


class Target:
    def __init__(self, identifier):
        self.id = identifier


class Overwrite:
    def __init__(self, allow=0, deny=0):
        self.allow, self.deny = allow, deny

    def pair(self):
        return NS(value=self.allow), NS(value=self.deny)


def room(*overwrites):
    return NS(guild=NS(id=3), overwrites={Target(target): Overwrite(allow, deny)
                                        for target, allow, deny in overwrites})


VIEW, CONNECT = 1 << 10, 1 << 20


def test_private_voice_requires_permission_boundary_not_empty_room():
    assert not private_voice_permissions(room(), '1', '5')
    assert not private_voice_permissions(room((3, CONNECT, 0)), '1', '5')
    assert private_voice_permissions(room((3, 0, CONNECT), (1, CONNECT, 0), (5, CONNECT, 0)), '1', '5')
    assert private_voice_permissions(room((3, 0, VIEW), (1, VIEW, 0), (5, VIEW, 0)), '1', '5')


def test_third_party_overrides_must_not_reopen_all_denied_boundaries():
    assert not private_voice_permissions(room((3, 0, CONNECT), (99, CONNECT, 0)), '1', '5')
    assert private_voice_permissions(room((3, 0, VIEW | CONNECT), (99, VIEW, 0)), '1', '5')
    assert not private_voice_permissions(room((3, 0, VIEW | CONNECT),
        (98, VIEW, 0), (99, CONNECT, 0)), '1', '5')


def credentials(tmp_path, **changes):
    path = tmp_path / 'credentials.json'
    path.write_text(json.dumps(dict(format=1, bot_token='synthetic-' + 'z' * 30,
                                    **BINDING, **changes)))
    path.chmod(0o600)
    return path


def test_private_credentials_require_exact_transport_binding(tmp_path):
    path = credentials(tmp_path)
    assert read_credentials(path, BINDING)['bot_token'].startswith('synthetic-')
    changed = dict(BINDING, owner_user_id='9')
    with pytest.raises(ValueError, match='binding_mismatch'):
        read_credentials(path, changed)
    with pytest.raises(ValueError):
        read_credentials('relative-credentials.json', BINDING)


@pytest.mark.parametrize('change', [{'format': True}, {'bot_token': 'short'},
    {'bot_token': 'x' * 30 + '\n'}, {'extra': 'unexpected'}])
def test_malformed_or_unbound_credentials_are_rejected(tmp_path, change):
    path = credentials(tmp_path)
    value = json.loads(path.read_text())
    value.update(change)
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        read_credentials(path, BINDING)


@pytest.mark.skipif(os.name != 'posix', reason='Production credential ownership is a Linux contract')
def test_credentials_reject_permissions_symlink_directory_and_fifo(tmp_path):
    path = credentials(tmp_path)
    path.chmod(0o640)
    with pytest.raises(ValueError):
        read_credentials(path, BINDING)
    path.chmod(0o600)
    link = tmp_path / 'link'
    link.symlink_to(path)
    with pytest.raises(ValueError):
        read_credentials(link, BINDING)
    with pytest.raises((ValueError, IsADirectoryError)):
        read_credentials(tmp_path, BINDING)
    fifo = tmp_path / 'fifo'
    os.mkfifo(fifo, 0o600)
    with pytest.raises(ValueError):
        read_credentials(fifo, BINDING)


class FakeInteraction:
    def __init__(self):
        self.generation = 7
        self.connected = True
        self.failed = False
        self.checks = 0
        self.observations = []

    def check_scope(self):
        self.checks += 1
        return dict(EXPECTED)

    def status(self, _consumer):
        return {'generation': self.generation, 'consumer': {'connected': self.connected}}


class FakeAdapter:
    dm_consumer = 'discord-dm'
    _controller = None
    _allowed_mentions = 'NO_MENTIONS'

    def __init__(self):
        self._started = False
        self.starts = 0
        self.closes = 0
        self.stats = {'text_delivered': 0}

    async def start(self):
        self.starts += 1
        self._started = True

    async def close(self):
        self.closes += 1
        self._started = False

    def _allowed_message(self, message):
        return message.author.id == 1 and message.channel.id == 2

    async def prepare_message(self, message):
        return message if self._allowed_message(message) else None


class FakeChannel:
    id = 2

    def __init__(self):
        self.sent = []

    async def send(self, text, **options):
        self.sent.append((text, options))


class FakeClient:
    def __init__(self, adapter, script):
        self.user = NS(id=5, name='SyntheticBot')
        self.adapter, self.script = adapter, script
        self.closed = 0
        self.handled = 0
        self.logins = []

    async def on_message(self, _message):
        self.handled += 1
        return {'ok': True, 'state': 'paused'}

    async def start(self, token, reconnect):
        self.logins.append((token, reconnect))
        await self.script(self)
        await asyncio.Event().wait()

    async def close(self):
        self.closed += 1


def accelerate_watch(monkeypatch):
    real_sleep = asyncio.sleep

    async def bounded_sleep(delay):
        await real_sleep(0 if delay == 5 else delay)
    monkeypatch.setattr(asyncio, 'sleep', bounded_sleep)


def run_serve(tmp_path, script, *, interaction=None):
    interaction = interaction or FakeInteraction()
    adapter = FakeAdapter()
    client = FakeClient(adapter, script)
    factories = []

    def factory(actual, **options):
        assert actual is interaction
        factories.append(options)
        return client, adapter

    async def run():
        stop = asyncio.Event()
        client.stop = stop
        await asyncio.wait_for(serve(interaction, BINDING, {'bot_token': 'synthetic-bot-token'},
            tmp_path / 'status.json', stop=stop, client_factory=factory), timeout=2)
    return run, client, adapter, interaction, factories


def test_sdk_resume_recovers_and_command_feedback_is_owner_bound(tmp_path):
    channel = FakeChannel()

    async def script(client):
        # Resumed sessions do not dispatch on_ready.
        await client.on_resumed()
        await client.on_message(NS(author=NS(id=9), channel=channel, content='!shana status'))
        await client.on_message(NS(author=NS(id=1), channel=channel, content='!shana pause'))
        client.stop.set()

    run, client, adapter, interaction, factories = run_serve(tmp_path, script)
    asyncio.run(run())
    assert adapter.starts == 1 and adapter.closes == 1 and client.closed == 1
    assert client.handled == 1
    assert client.logins == [('synthetic-bot-token', True)]
    assert channel.sent == [('Shana voice: paused.', {'allowed_mentions': 'NO_MENTIONS'})]
    assert interaction.checks >= 1
    assert factories[0]['adapter_class'] is ReleaseDiscordAdapter
    assert factories[0]['room_policy'] is private_voice_permissions
    state = json.loads((tmp_path / 'status.json').read_text())
    assert state['connected'] is False and state['phase'] == 'closed'
    assert 'synthetic-bot-token' not in json.dumps(state)


@pytest.mark.parametrize('fault', ['generation', 'lease', 'failed'])
def test_monitor_fences_changed_owner_lease_or_failed_client_without_replay(tmp_path, monkeypatch, fault):
    accelerate_watch(monkeypatch)
    interaction = FakeInteraction()

    async def script(client):
        await client.on_ready()
        if fault == 'generation':
            interaction.generation += 1
        elif fault == 'lease':
            interaction.connected = False
        else:
            interaction.failed = True

    run, client, adapter, interaction, _ = run_serve(tmp_path, script, interaction=interaction)
    with pytest.raises(RuntimeError, match='owner_or_lease_changed'):
        asyncio.run(run())
    assert client.closed == 1 and adapter.closes == 1
    assert adapter.starts == 1 and len(client.logins) == 1
    assert not interaction.observations and client.handled == 0
    assert json.loads((tmp_path / 'status.json').read_text())['connected'] is False


def test_wrong_bot_identity_closes_without_starting_consumers(tmp_path):
    async def script(client):
        client.user.id = 9
        await client.on_resumed()
    run, client, adapter, _interaction, _ = run_serve(tmp_path, script)
    with pytest.raises(RuntimeError, match='ready_check_failed'):
        asyncio.run(run())
    assert adapter.starts == 0 and adapter.closes == 1 and client.closed == 1


def test_fixed_feedback_contains_no_private_failure_details():
    text = command_feedback({'ok': False, 'reason': 'private_owner_room_required', 'secret': 'do not include'})
    assert 'by yourself' in text and 'do not include' not in text
    assert 'private_token' not in command_feedback({'ok': False, 'reason': 'private_token'})


def test_transport_voice_controls_do_not_enable_browser_or_dm_consumers(tmp_path):
    from test_coordination_core import make_core
    from gamma.coordination.interaction import PrivateInteraction
    from gamma.coordination.interaction_models import InteractionObservation
    core = make_core(tmp_path)
    service = PrivateInteraction(core)
    try:
        for consumer in ('desktop', 'discord-dm', 'discord-voice'):
            service.connect(consumer)
        for action in ('voice_enabled', 'speech_enabled'):
            service.control_consumer('discord-voice', action, True)
        assert service.status('discord-voice')['voice_enabled'] is True
        assert service.status('discord-voice')['speech_enabled'] is True
        for consumer in ('desktop', 'discord-dm'):
            state = service.status(consumer)
            assert state['voice_enabled'] is False and state['speech_enabled'] is False
        desktop = InteractionObservation(audience='owner_private', request_id='disabled-browser',
            consumer_id='desktop', session_id='browser', channel='private_voice',
            speaker='owner', text='Do not enter disabled voice')
        assert service.observe(desktop)['reason'] == 'voice_disabled'
        assert core.store.read('turn', desktop.request_id) is None
    finally:
        service.close()
        core.close()


def test_transport_disable_rotates_only_its_lease_and_keeps_current_browser_output(tmp_path):
    from test_private_interaction import setup, observation
    core, service = setup(tmp_path)
    try:
        service.connect('discord-voice')
        service.control_consumer('discord-voice', 'voice_enabled', True)
        service.control_consumer('discord-voice', 'speech_enabled', True)
        service.observe(observation('browser-current'))
        before = service.next_output('desktop')['output']
        assert before and before['audio_available']
        epoch = service.status('desktop')['output_epoch']
        browser_lease = core.store.read('interaction_consumer', 'desktop')['lease']
        discord_lease = core.store.read('interaction_consumer', 'discord-voice')['lease']
        service.control_consumer('discord-voice', 'speech_enabled', False)
        assert core.store.read('interaction_consumer', 'discord-voice')['lease'] != discord_lease
        assert core.store.read('interaction_consumer', 'desktop')['lease'] == browser_lease
        assert service.status('desktop')['output_epoch'] == epoch
        raw, mime = service.audio(before['id'], 'desktop', before['generation'], before['output_epoch'])
        assert raw and mime == 'audio/wav'
        assert service.status('desktop')['speech_enabled'] is True
        assert service.status('discord-voice')['speech_enabled'] is False
    finally:
        service.close()
        core.close()


@pytest.mark.skipif(os.name != 'posix', reason='Production credential ownership is a Linux contract')
def test_credentials_refuse_unowned_and_oversized_records(tmp_path, monkeypatch):
    import gamma.coordination.discord_release as release
    path = credentials(tmp_path)
    original = os.fstat
    def different_owner(fd):
        value = original(fd)
        return NS(st_mode=value.st_mode, st_size=value.st_size, st_uid=os.getuid() + 1)
    with monkeypatch.context() as changes:
        changes.setattr(release.os, 'fstat', different_owner)
        with pytest.raises(ValueError, match='not_owned_private_regular_file'):
            read_credentials(path, BINDING)
    path.write_bytes(b'x' * 8193)
    with pytest.raises(ValueError, match='not_owned_private_regular_file'):
        read_credentials(path, BINDING)


def test_same_consumer_disable_revokes_old_audio_and_reconnect_clears_overrides(tmp_path):
    from test_private_interaction import setup, observation
    core, service = setup(tmp_path)
    try:
        service.connect('discord-voice')
        service.control_consumer('discord-voice', 'voice_enabled', True)
        service.control_consumer('discord-voice', 'speech_enabled', True)
        service.observe(observation('discord-current', consumer_id='discord-voice', channel='discord_voice'))
        old = service.next_output('discord-voice')['output']
        assert old and old['audio_available']
        service.control_consumer('discord-voice', 'speech_enabled', False)
        with pytest.raises(ValueError, match='stale'):
            service.audio(old['id'], 'discord-voice', old['generation'], old['output_epoch'])
        assert service.next_output('discord-voice')['output'] is None
        assert service.status('desktop')['speech_enabled'] is True
        service.control_consumer('discord-voice', 'voice_enabled', False)
        assert service.status('discord-voice')['voice_enabled'] is False
        # A newly connected transport inherits the current explicit defaults,
        # rather than retaining overrides from a dead transport lease.
        service.disconnect('discord-voice')
        service.connect('discord-voice')
        assert service.status('discord-voice')['voice_enabled'] is True
        assert service.status('discord-voice')['speech_enabled'] is True
    finally:
        service.close()
        core.close()


@pytest.mark.parametrize('action,value', [('voice_enabled', 1), ('speech_enabled', 'true'),
                                         ('global_stop', True)])
def test_transport_controls_reject_coercions_and_unknown_actions(tmp_path, action, value):
    from test_coordination_core import make_core
    from gamma.coordination.interaction import PrivateInteraction
    core = make_core(tmp_path)
    service = PrivateInteraction(core)
    try:
        service.connect('discord-voice')
        with pytest.raises(ValueError):
            service.control_consumer('discord-voice', action, value)
        assert not service.status('discord-voice')['voice_enabled']
        assert not service.status('discord-voice')['speech_enabled']
    finally:
        service.close()
        core.close()


@pytest.mark.parametrize('control_failure', [False, True])
def test_release_join_uses_only_scoped_controls_and_leaves_on_failed_enable(control_failure):
    from test_coordination_discord_private import Interaction
    interaction = Interaction()
    changes = []
    def scoped(consumer, action, value):
        changes.append((consumer, action, value))
        if control_failure:
            raise RuntimeError('synthetic control refusal')
    interaction.control_consumer = scoped
    interaction.control = lambda *_: pytest.fail('join changed global browser controls')
    selected = NS(id=4, guild=NS(id=3), members=[NS(id=1), NS(id=5)])
    client = NS(user=NS(id=5), get_channel=lambda _: selected)
    adapter = ReleaseDiscordAdapter(client, interaction, owner_user_id='1', dm_channel_id='2',
        voice_guild_id='3', voice_channel_id='4', room_policy=lambda *_: True)
    class Controller:
        _state = 'disconnected'
        _voice = NS(stop=lambda: None)
        async def handle(self, _owner, action):
            self._state = {'join':'listening', 'resume':'listening',
                           'pause':'paused', 'leave':'disconnected'}.get(action, self._state)
            return {'ok': True, 'state': self._state}
    adapter._controller = Controller()
    async def run():
        if control_failure:
            with pytest.raises(RuntimeError, match='control refusal'):
                await adapter.voice_command('1', 'join')
            assert adapter._controller._state == 'disconnected'
            assert adapter.voice_consumer not in interaction.connected
        else:
            assert (await adapter.voice_command('1', 'join'))['state'] == 'listening'
            assert changes == [('discord-voice', 'voice_enabled', True),
                               ('discord-voice', 'speech_enabled', True)]
            assert (await adapter.voice_command('1', 'pause'))['state'] == 'paused'
            assert len(changes) == 2 and adapter.voice_consumer not in interaction.connected
    asyncio.run(run())
