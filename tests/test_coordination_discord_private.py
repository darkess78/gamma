"""Private Discord recipient, delivery and stale callback tests. No Discord login."""
import asyncio
from types import SimpleNamespace as NS

import pytest

from gamma.coordination.discord import PrivateDiscordAdapter
from gamma.coordination.discord_scope import admit_event, discord_id, validate_scope


class Channel:
    def __init__(self):
        self.id = 2
        self.type = 1
        self.recipient = NS(id=1)
        self.sent = []
        self.fail = False

    async def send(self, text, **kwargs):
        self.sent.append((text, kwargs))
        if self.fail:
            raise RuntimeError('uncertain provider failure')
        return NS(id=99)


class Interaction:
    def __init__(self):
        self.observations = []
        self.acks = []
        self.connected = set()
        self.outputs = []
        self.generation = 1
        self.epoch = 0
        self.audio_calls = []
        self.connect_calls = []

    def connect(self, consumer_id):
        self.connected.add(consumer_id)
        self.connect_calls.append(consumer_id)
        return self.status(consumer_id)

    def disconnect(self, consumer_id):
        self.connected.discard(consumer_id)
        return self.status(consumer_id)

    def observe(self, observation):
        self.observations.append(observation.model_dump())
        self.output(observation.consumer_id, observation.channel)
        return {'ok': True}

    def output(self, consumer='discord-dm', channel='discord_dm'):
        row = {'id': 'output-' + ('%032x' % len(self.outputs)), 'generation': self.generation,
               'output_epoch': self.epoch, 'consumer_id': consumer, 'channel': channel,
               'display_text': 'A private reply.', 'speech_text': 'A private reply.',
               'status': 'generated', 'audio_available': channel == 'discord_voice'}
        self.outputs.append(row)
        return row

    def status(self, consumer_id=None):
        return {'generation': self.generation, 'output_epoch': self.epoch, 'awake': True,
                'consumer': {'connected': consumer_id in self.connected},
                'outputs': [dict(row) for row in self.outputs if row['consumer_id'] == consumer_id]}

    def next_output(self, consumer_id):
        for row in self.outputs:
            if row['consumer_id'] == consumer_id and row['status'] == 'generated':
                row['status'] = 'offered'
                return {'output': dict(row), 'generation': self.generation, 'output_epoch': self.epoch}
        return {'output': None}

    def acknowledge(self, **values):
        self.acks.append(values)
        row = next(row for row in self.outputs if row['id'] == values['output_id'])
        row['status'] = values['status']
        return {'ok': True}

    def audio(self, output_id, consumer_id, generation, output_epoch):
        self.audio_calls.append((output_id, consumer_id, generation, output_epoch))
        return b'a bounded WAV fixture', 'audio/wav'

    def submit_audio(self, observation, audio):
        self.observations.append(observation.model_dump())
        return {'ok': True}


def setup(**kwargs):
    room = NS(id=4, guild=NS(id=3), members=[NS(id=1), NS(id=5)])
    client = NS(user=NS(id=5), get_channel=lambda channel_id: room if channel_id == 4 else None)
    service, dm = Interaction(), Channel()
    adapter = PrivateDiscordAdapter(client, service, owner_user_id='1', dm_channel_id='2',
        voice_guild_id='3', voice_channel_id='4', allowed_mentions='NO_MENTIONS', **kwargs)
    adapter._started = True
    adapter._dm = dm
    service.connect(adapter.dm_consumer)
    return adapter, service, dm, room


def message(dm, **kwargs):
    return NS(id=10, guild=None, webhook_id=None, channel=dm,
              author=NS(id=1, bot=False), content='Hello Shana', **kwargs)


@pytest.mark.parametrize('value', [True, 1, '0', '01', ' 1', '\u0661', str(2**64), '1\n'])
def test_scope_rejects_coercions(value):
    with pytest.raises(ValueError):
        discord_id(value)


def test_scope_pins_audience():
    scope = validate_scope('1', '2', '3', '4')
    assert admit_event(scope, user_id='1', channel_id='2')
    assert not admit_event(scope, user_id='1', channel_id='2', guild_id='3')
    assert not admit_event(scope, user_id='9', channel_id='2')
    assert not admit_event(scope, user_id='1', channel_id='4', guild_id='3')
    assert admit_event(scope, user_id='1', channel_id='4', guild_id='3', voice=True)


@pytest.mark.parametrize('change', ['author', 'bot', 'guild', 'channel', 'group', 'recipient', 'webhook'])
def test_foreign_input_never_reaches_personal_context(change):
    adapter, service, dm, _room = setup()
    incoming = message(dm)
    if change == 'author': incoming.author.id = 7
    if change == 'bot': incoming.author.bot = True
    if change == 'guild': incoming.guild = NS(id=3)
    if change == 'channel': incoming.channel.id = 8
    if change == 'group': incoming.channel.type = 3
    if change == 'recipient': incoming.channel.recipient.id = 8
    if change == 'webhook': incoming.webhook_id = 8
    result = asyncio.run(adapter.on_message(incoming))
    assert result['ok'] is False
    assert not service.observations and not dm.sent


def test_owner_dm_uses_same_service_and_explicit_no_mentions():
    adapter, service, dm, _room = setup()
    result = asyncio.run(adapter.on_message(message(dm)))
    assert result['ok'] is True
    assert len(service.observations) == 1
    observed = service.observations[0]
    assert observed['consumer_id'] == 'discord-dm'
    assert observed['speaker'] == 'owner' and observed['address_cue'] == 'direct'
    assert observed['request_id'] == 'discord-dm-10'
    assert dm.sent == [('A private reply.', {'allowed_mentions': 'NO_MENTIONS'})]
    assert service.acks[-1]['status'] == 'delivered'
    assert service.acks[-1]['receipt_id'] == '99'


def test_failed_send_is_attempted_once_and_never_replayed():
    adapter, service, dm, _room = setup()
    dm.fail = True
    service.output()
    async def run():
        await adapter.deliver_text_once()
        await adapter.deliver_text_once()
    asyncio.run(run())
    assert len(dm.sent) == 1
    assert service.acks[-1]['status'] == 'failed'
    assert adapter.stats['text_delivered'] == 0


@pytest.mark.parametrize('reason', ['generation', 'epoch', 'expired', 'disconnected'])
def test_stale_text_is_withheld_at_release(reason):
    adapter, service, dm, _room = setup()
    row = service.output()
    if reason == 'generation': service.generation += 1
    if reason == 'epoch': service.epoch += 1
    if reason == 'expired': row['status'] = 'expired'
    if reason == 'disconnected': service.connected.clear()
    asyncio.run(adapter.deliver_text_once())
    assert dm.sent == []
    assert not any(ack['status'] == 'delivered' for ack in service.acks)


def test_stop_between_text_chunks_prevents_remaining_chunks():
    adapter, service, dm, _room = setup()
    row = service.output()
    row['display_text'] = 'x' * 4500
    original_send = dm.send
    async def send(*args, **kwargs):
        result = await original_send(*args, **kwargs)
        service.epoch += 1
        return result
    dm.send = send
    asyncio.run(adapter.deliver_text_once())
    assert len(dm.sent) == 1
    assert [ack['status'] for ack in service.acks] == ['attempted']


def test_voice_room_requires_owner_and_no_third_party():
    adapter, _service, _dm, room = setup()
    assert adapter.voice_room_private()
    room.members.append(NS(id=8))
    assert not adapter.voice_room_private()
    room.members = [NS(id=5)]
    assert not adapter.voice_room_private()


class Voice:
    def __init__(self, service, *, completed=True):
        self.stops = 0
        self.completed = completed
        self.after = None
        self.service = service

    def play(self, source, after):
        self.after = after
        if self.completed:
            after(None)

    def stop(self):
        self.stops += 1


class Source:
    def __init__(self): self.cleaned = False
    def cleanup(self): self.cleaned = True


def test_audio_requires_actual_success_callback_and_fenced_ack():
    source = Source()
    adapter, service, _dm, _room = setup(audio_source_factory=lambda audio, mime: source)
    service.connect(adapter.voice_consumer)
    service.output('discord-voice', 'discord_voice')
    voice = Voice(service)
    adapter._controller = NS(_state='listening', _voice=voice)
    asyncio.run(adapter.deliver_voice_once())
    assert [ack['status'] for ack in service.acks] == ['attempted', 'started', 'played']
    assert adapter.stats['audio_played'] == 1
    assert source.cleaned and voice.stops


def test_voice_interrupt_stops_without_waiting_for_audio_end():
    source = Source()
    adapter, service, _dm, _room = setup(audio_source_factory=lambda audio, mime: source)
    service.connect(adapter.voice_consumer)
    service.output('discord-voice', 'discord_voice')
    voice = Voice(service, completed=False)
    adapter._controller = NS(_state='listening', _voice=voice)
    async def run():
        task = asyncio.create_task(adapter.deliver_voice_once())
        while voice.after is None:
            await asyncio.sleep(.005)
        service.epoch += 1
        await asyncio.wait_for(task, timeout=.5)
        voice.after(None)
        await asyncio.sleep(.01)
    asyncio.run(run())
    assert voice.stops and source.cleaned
    assert [ack['status'] for ack in service.acks] == ['attempted', 'started', 'cancelled']
    assert adapter.stats['audio_played'] == 0


def test_voice_unauthorized_room_never_fetches_audio():
    adapter, service, _dm, room = setup()
    service.connect(adapter.voice_consumer)
    service.output('discord-voice', 'discord_voice')
    adapter._controller = NS(_state='listening', _voice=Voice(service))
    room.members.append(NS(id=9))
    asyncio.run(adapter.deliver_voice_once())
    assert not service.audio_calls


def test_no_owner_commands_from_other_account():
    adapter, _service, _dm, _room = setup()
    assert asyncio.run(adapter.voice_command('8', 'join'))['reason'] == 'owner_required'
    assert asyncio.run(adapter.voice_command(1, 'join'))['reason'] == 'owner_required'


def test_actual_core_http_and_sdk_callbacks_deliver_once(tmp_path):
    """Actual owner/store/HTTP path; only the external Discord SDK is replaced."""
    from gamma.coding.durable_practice import PracticeRunner
    from gamma.coordination.app import build_app
    from gamma.coordination.discord_client import RemotePrivateInteraction
    from gamma.coordination.hosting import loopback_server
    from gamma.schemas.response import AssistantResponse
    from test_coordination_core import propose, check, evaluate

    app = build_app(tmp_path, bearer_token='t' * 32,
        draft_factory=lambda: lambda *_: AssistantResponse(display_text='A shared Core reply.', delivery_mode='text_only'),
        runner_factory=lambda store: PracticeRunner(store, propose=propose, check=check, evaluate=evaluate),
        run_background=False)
    with loopback_server(app) as origin:
        remote = RemotePrivateInteraction(origin, 't' * 32)
        assert remote.check_scope() == {'scope': 'isolated_owner_private', 'status': 'ok'}
        adapter, _fake, dm, _room = setup()
        adapter.interaction = remote
        remote.connect(adapter.dm_consumer)
        async def run():
            await adapter.on_message(message(dm))
            await adapter.on_message(message(dm))
        asyncio.run(run())
        assert dm.sent == [('A shared Core reply.', {'allowed_mentions': 'NO_MENTIONS'})]
        state = remote.status(adapter.dm_consumer)
        assert len(state['outputs']) == 1 and state['outputs'][0]['status'] == 'delivered'
        assert state['outputs'][0]['receipt_id'] == '99'
        with app.state.core.store.transaction() as db:
            turn = app.state.core.store.get(db, 'turn', 'discord-dm-10')
            assert turn['status'] == 'completed' and turn['session_id'] == 'discord-private'


def test_actual_sleep_has_no_discord_delivery(tmp_path):
    from gamma.coordination.interaction import PrivateInteraction
    from test_coordination_core import make_core
    core = make_core(tmp_path)
    service = PrivateInteraction(core)
    adapter, _fake, dm, _room = setup()
    adapter.interaction = service
    service.connect(adapter.dm_consumer)
    try:
        core.control('sleep-owner', 'sleep')
        asyncio.run(adapter.on_message(message(dm)))
        assert not dm.sent
        assert not service.status(adapter.dm_consumer)['outputs']
    finally:
        service.close()
        core.close()


def test_network_disabled_runner_does_not_import_sdk_or_read_token(monkeypatch, capsys):
    from gamma.coordination.discord_client import main
    monkeypatch.setenv('GAMMA_COORDINATION_DISCORD_TEST_BOT_TOKEN', 'not-used')
    assert main(['--api-origin', 'http://127.0.0.1:18777', '--owner-user-id', '1',
                 '--dm-channel-id', '2', '--test-bot-user-id', '5']) == 0
    assert 'prepared_only' in capsys.readouterr().out


@pytest.mark.parametrize('origin', ['http://127.0.0.1:8000','https://127.0.0.1:18777',
    'http://localhost:18777','http://10.78.78.13:18777','http://u:p@127.0.0.1:18777',
    'http://127.0.0.1:18777/path','http://127.0.0.1:18777?token=x'])
def test_remote_client_cannot_target_production_or_external_origin(origin):
    from gamma.coordination.discord_client import RemotePrivateInteraction
    with pytest.raises(ValueError):
        RemotePrivateInteraction(origin, 't' * 32)


def test_existing_owner_controller_join_pause_resume_leave_rejoin():
    adapter, service, _dm, room = setup(voice_factory=object, sink_factory=lambda: NS())
    voices = []
    class ConnectedVoice(Voice):
        def __init__(self):
            super().__init__(service)
            self.connected = True
            self.listening = False
        def is_connected(self): return self.connected
        def listen(self, sink): self.listening = True
        def stop_listening(self): self.listening = False
        async def disconnect(self, force): self.connected = False
    async def connect(**kwargs):
        voice = ConnectedVoice()
        voices.append(voice)
        return voice
    room.connect = connect
    async def run():
        assert (await adapter.voice_command('1', 'join'))['state'] == 'listening'
        before_status = service.connect_calls.count('discord-voice')
        assert (await adapter.voice_command('1', 'status'))['state'] == 'listening'
        assert service.connect_calls.count('discord-voice') == before_status
        assert (await adapter.voice_command('1', 'pause'))['state'] == 'paused'
        assert 'discord-voice' not in service.connected
        assert (await adapter.voice_command('1', 'resume'))['state'] == 'listening'
        assert (await adapter.voice_command('1', 'leave'))['state'] == 'disconnected'
        assert (await adapter.voice_command('1', 'join'))['state'] == 'listening'
        assert len(voices) == 2
        await adapter.close()
        assert not voices[-1].connected
    asyncio.run(run())


def test_voice_receive_queue_is_bounded_and_nonowner_pcm_is_rejected():
    adapter, _service, _dm, _room = setup()
    adapter._controller = NS(_state='listening')
    sink = NS(closed=False, epoch=adapter._voice_epoch)
    good = {'user_id':'1', 'wav':b'x'*44}
    adapter._enqueue_audio(sink, {'user_id':'8', 'wav':b'x'*44})
    adapter._enqueue_audio(sink, {'user_id':'1', 'wav':b'x'*800_001})
    assert adapter._audio_queue.empty()
    adapter._enqueue_audio(sink, good)
    adapter._enqueue_audio(sink, good)
    adapter._enqueue_audio(sink, good)
    assert adapter._audio_queue.qsize() == 2 and adapter.stats['dropped'] == 1


def test_disconnect_cleans_local_tasks_when_owner_api_is_unavailable():
    adapter, service, _dm, _room = setup()
    def unavailable(*args): raise RuntimeError('provider unavailable')
    service.disconnect = unavailable
    async def run():
        task = asyncio.create_task(asyncio.sleep(60))
        adapter._tasks = [task]
        await adapter.close()
        assert task.done() and adapter._tasks == [] and not adapter._started
    asyncio.run(run())


def test_private_api_redirects_are_never_followed():
    from gamma.coordination.discord_client import _NoRedirect
    assert _NoRedirect().redirect_request(None, None, 302, 'found', {}, 'https://untrusted.example') is None


def test_actual_core_voice_audio_hash_and_callback_accounting(tmp_path):
    from gamma.coordination.interaction_models import InteractionObservation
    from test_private_interaction import setup as real_setup, wav
    core, service = real_setup(tmp_path)
    source = Source()
    received = []
    adapter, _fake, _dm, _room = setup(audio_source_factory=lambda audio, mime: received.append((audio, mime)) or source)
    adapter.interaction = service
    service.connect(adapter.voice_consumer)
    adapter._controller = NS(_state='listening', _voice=Voice(service))
    try:
        service.observe(InteractionObservation(audience='owner_private', request_id='real-discord-voice',
            session_id='discord-private', consumer_id='discord-voice', channel='discord_voice',
            speaker='owner', text='Shana, what is ready?', address_cue='name'))
        asyncio.run(adapter.deliver_voice_once())
        output = service.status('discord-voice')['outputs'][0]
        assert output['status'] == 'played' and not output['audio_available']
        assert received == [(wav(), 'audio/wav')]
        assert adapter.stats['audio_played'] == 1 and source.cleaned
    finally:
        service.close()
        core.close()


def test_actual_new_voice_turn_invalidates_sdk_playback_before_callback(tmp_path):
    from gamma.coordination.interaction_models import InteractionObservation
    from test_private_interaction import setup as real_setup
    core, service = real_setup(tmp_path)
    source = Source()
    adapter, _fake, _dm, _room = setup(audio_source_factory=lambda audio, mime: source)
    adapter.interaction = service
    service.connect(adapter.voice_consumer)
    voice = Voice(service, completed=False)
    adapter._controller = NS(_state='listening', _voice=voice)
    def observation(request, text):
        return InteractionObservation(audience='owner_private', request_id=request,
            session_id='discord-private', consumer_id='discord-voice', channel='discord_voice',
            speaker='owner', text=text, address_cue='name')
    async def run():
        service.observe(observation('first-audio', 'Shana, what is ready?'))
        task = asyncio.create_task(adapter.deliver_voice_once())
        while voice.after is None or not any(row['status']=='started' for row in service.status('discord-voice')['outputs']):
            await asyncio.sleep(.005)
        service.observe(observation('second-audio', 'Wait, use the other note instead.'))
        await asyncio.wait_for(task, timeout=.5)
        voice.after(None)
        await asyncio.sleep(.01)
    try:
        asyncio.run(run())
        states = [row['status'] for row in service.status('discord-voice')['outputs']]
        assert 'played' not in states and states.count('generated') == 1
        assert voice.stops and adapter.stats['audio_played'] == 0 and source.cleaned
    finally:
        service.close()
        core.close()
