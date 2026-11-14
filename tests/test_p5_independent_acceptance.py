"""Independent P5 integration checks through the real owner and transport state."""
import asyncio
import json

from gamma.coordination.interaction_models import InteractionObservation
from gamma.coordination.turn_schema import private_turn_schema
from gamma.coordination.personal_models import PrivateTurnDraft
from gamma.conversation.turn_contract import StructuredTurnParser
from gamma.schemas.response import AssistantResponse
from test_private_interaction import setup as real_setup, observation, wav
from test_coordination_discord_private import setup as discord_setup, Voice, Source


def _ack(service, output, status):
    return service.acknowledge(consumer_id=output['consumer_id'], output_id=output['id'],
        generation=output['generation'], output_epoch=output['output_epoch'], status=status)


def test_current_output_remains_visible_after_more_than_eight_completed_outputs(tmp_path):
    core, service = real_setup(tmp_path)
    try:
        for index in range(10):
            service.observe(observation('history-' + str(index)))
            output = service.next_output('desktop')['output']
            _ack(service, output, 'started')
            _ack(service, output, 'played')
        receipt = service.observe(observation('current'))
        polled = service.next_output('desktop')['output']
        assert polled['id'] == receipt['output_id']
        visible = service.status('desktop')['outputs']
        assert any(row['id'] == polled['id'] and row['status'] == 'offered' for row in visible)
    finally:
        service.close()
        core.close()


def test_a_consumers_live_output_is_not_crowded_out_by_other_consumer_history(tmp_path):
    core, service = real_setup(tmp_path)
    service.connect('other-consumer')
    try:
        for index in range(10):
            service.observe(observation('other-' + str(index), consumer_id='other-consumer'))
            output = service.next_output('other-consumer')['output']
            _ack(service, output, 'started')
            _ack(service, output, 'played')
        receipt = service.observe(observation('current'))
        visible = service.status('desktop')['outputs']
        assert any(row['id'] == receipt['output_id'] for row in visible)
    finally:
        service.close()
        core.close()


def test_discord_plays_every_chunk_in_order_and_only_reports_individual_callbacks(tmp_path):
    text = 'First complete sentence carries the useful point. Second complete sentence adds detail. Third sentence closes the answer.'
    chunks = []
    core, service = real_setup(tmp_path, draft=lambda *_: AssistantResponse(display_text=text, delivery_mode='text_only'),
        synthesize=lambda chunk, emotion: (chunks.append(chunk) or (wav(), 'audio/wav')))
    sources = []
    def audio_source(raw, mime):
        assert raw == wav() and mime == 'audio/wav'
        source = Source()
        sources.append(source)
        return source
    adapter, _fake, _dm, _room = discord_setup(audio_source_factory=audio_source)
    adapter.interaction = service
    service.connect('discord-voice')
    adapter._controller = __import__('types').SimpleNamespace(_state='listening', _voice=Voice(service))
    try:
        receipt = service.observe(InteractionObservation(audience='owner_private', request_id='chunked-discord',
            session_id='discord-private', consumer_id='discord-voice', channel='discord_voice',
            speaker='owner', text='Shana, explain the result.', address_cue='name'))
        assert len(receipt['output_ids']) == len(chunks) >= 2
        async def run():
            for _ in range(len(chunks) + 1):
                await adapter.deliver_voice_once()
        asyncio.run(run())
        records = [core.store.read('interaction_output', identifier) for identifier in receipt['output_ids']]
        assert [row['chunk_index'] for row in records] == list(range(len(chunks)))
        assert [row['speech_text'] for row in records] == chunks
        assert all(row['status'] == 'played' for row in records)
        assert len(sources) == len(chunks) and all(source.cleaned for source in sources)
        assert core._delivery_summary(records) == 'all_chunks_consumer_reported_played'
    finally:
        service.close()
        core.close()


def test_compact_serialization_keeps_property_constraints_and_parser_defaults():
    full, compact = private_turn_schema(), private_turn_schema(compact=True)
    assert full['$defs'].keys() == compact['$defs'].keys()
    for before, after in zip(full['oneOf'], compact['oneOf']):
        assert before['properties'] == after['properties']
        assert before['additionalProperties'] is after['additionalProperties'] is False
    names = {row['properties']['state_updates']['$ref'].split('/')[-1] for row in full['oneOf']}
    for name, definition in full['$defs'].items():
        adjusted = dict(compact['$defs'][name])
        if name in names:
            assert adjusted.pop('required') == ['activity_action']
            definition = dict(definition)
            definition.pop('required')
        assert definition == adjusted
    minimal = {'action':'reply', 'requested_delivery':'text_only', 'final_text':'A concise answer.',
               'emotion':'neutral', 'state_updates':{'activity_action':'wait'}, 'reason_code':'reply'}
    parser = StructuredTurnParser()
    parsed = parser.parse(json.dumps(minimal), legacy_delivery='text_only', draft_type=PrivateTurnDraft)
    expanded = parsed.draft.model_dump()
    assert expanded['tool_calls'] == expanded['memory_candidates'] == []
    assert expanded['state_updates']['personal_changes'] == []
    assert expanded['state_updates']['growth_proposals'] == []
    assert expanded['state_updates']['activity_run_id'] is None
    assert parser.parse(json.dumps(expanded), legacy_delivery='text_only', draft_type=PrivateTurnDraft).draft == parsed.draft


def test_discord_valid_seventy_second_chunk_survives_old_sixty_second_deadline(tmp_path, monkeypatch):
    """Advance only the adapter's clock, then receive a genuine SDK callback."""
    import gamma.coordination.discord as discord_transport
    core, service = real_setup(tmp_path, synthesize=lambda *_: (wav(70), 'audio/wav'))
    source = Source()
    adapter, _fake, _dm, _room = discord_setup(audio_source_factory=lambda *_: source)
    adapter.interaction = service
    service.connect('discord-voice')
    class DelayedCallbackVoice(Voice):
        def play(self, audio_source, after):
            self.after = after
            asyncio.get_running_loop().call_later(.001, after, None)
    voice = DelayedCallbackVoice(service, completed=False)
    adapter._controller = __import__('types').SimpleNamespace(_state='listening', _voice=voice)
    ticks = iter([0.0, 65.0])
    monkeypatch.setattr(discord_transport, '_monotonic', lambda: next(ticks))
    try:
        service.observe(InteractionObservation(audience='owner_private', request_id='long-discord',
            session_id='discord-private', consumer_id='discord-voice', channel='discord_voice',
            speaker='owner', text='Shana, read the result.', address_cue='name'))
        output = service.status('discord-voice')['outputs'][0]
        assert output['audio_duration_seconds'] == 70
        asyncio.run(adapter.deliver_voice_once())
        assert service.status('discord-voice')['outputs'][0]['status'] == 'played'
        assert adapter.stats['audio_played'] == 1 and source.cleaned
    finally:
        service.close()
        core.close()


def test_discord_playback_deadline_remains_bounded_for_old_or_invalid_metadata():
    from gamma.coordination.discord import _playback_timeout
    assert _playback_timeout({'audio_duration_seconds': 90}) == 100
    for value in [None, True, '70', -1, 0, 91, float('nan'), float('inf')]:
        assert _playback_timeout({'audio_duration_seconds': value}) == 60


def test_discord_second_audio_reaches_stt_and_core_while_older_tts_is_held(tmp_path):
    """Run the actual adapter tasks and Core, with a deliberately slow provider."""
    import threading
    from types import SimpleNamespace
    held, release = threading.Event(), threading.Event()
    first = 'Shana, give the first answer.'
    second = 'Shana, stop and answer the new question.'
    transcript_calls, draft_calls, synthesis_calls = [], [], []
    answer = 'First complete sentence carries the useful point. Second complete sentence adds detail.'
    def transcribe(raw):
        text = first if not transcript_calls else second
        transcript_calls.append(text)
        return text
    def draft(text, context):
        draft_calls.append(text)
        return AssistantResponse(display_text=answer if text == first else 'The new question is accepted.', delivery_mode='text_only')
    def synthesize(text, emotion):
        synthesis_calls.append(text)
        if len(synthesis_calls) == 2:
            held.set()
            assert release.wait(5)
        return wav(), 'audio/wav'
    core, service = real_setup(tmp_path, transcribe=transcribe, draft=draft, synthesize=synthesize)
    adapter, _fake, _dm, _room = discord_setup(audio_source_factory=lambda *_: Source())
    adapter.interaction = service
    adapter._started = False
    service.connect('discord-voice')
    class Controller:
        _state = 'listening'
        _voice = Voice(service, completed=False)
        async def handle(self, owner, command):
            self._state = 'disconnected'
            return {'ok': True, 'state': self._state}
    adapter._controller = Controller()
    async def run():
        tasks = []
        try:
            await adapter.start()
            tasks = list(adapter._tasks)
            sink = SimpleNamespace(closed=False, epoch=adapter._voice_epoch)
            item = {'user_id':'1', 'wav':wav()}
            adapter._enqueue_audio(sink, item)
            assert await asyncio.to_thread(held.wait, 3)
            assert draft_calls == [first] and transcript_calls == [first]
            adapter._enqueue_audio(sink, item)
            deadline = asyncio.get_running_loop().time() + 2
            while asyncio.get_running_loop().time() < deadline:
                with core.store.transaction() as db:
                    accepted = [row for row in core.store.list(db, 'turn', None)
                                if row.get('text') == second and row['status'] == 'completed']
                if accepted:
                    break
                await asyncio.sleep(.01)
            assert accepted and not release.is_set()
            assert transcript_calls == [first, second] and draft_calls == [first, second]
            release.set()
            await asyncio.wait_for(adapter._audio_queue.join(), timeout=5)
        finally:
            release.set()
            await adapter.close()
            assert tasks and all(task.done() for task in tasks)
            assert adapter._tasks == [] and adapter._audio_queue.empty()
    try:
        asyncio.run(run())
    finally:
        release.set()
        service.close()
        core.close()


def test_first_owner_dm_after_idle_is_not_lost_to_unmaintained_consumer_lease(tmp_path):
    from test_coordination_discord_private import message
    now = [1000.0]
    core, service = real_setup(tmp_path, clock=lambda: now[0])
    adapter, _fake, dm, _room = discord_setup()
    adapter.interaction = service
    adapter._dm = None
    service.connect(adapter.dm_consumer)
    async def run():
        now[0] = 1050.0
        await adapter.deliver_text_once()
        now[0] = 1061.0
        result = await adapter.on_message(message(dm))
        assert result['ok'] is True
        assert dm.sent and service.status(adapter.dm_consumer)['outputs'][0]['status'] == 'delivered'
    try:
        asyncio.run(run())
    finally:
        service.close()
        core.close()


def test_discord_long_playback_renews_existing_voice_lease_without_reconnecting(tmp_path, monkeypatch):
    import gamma.coordination.discord as discord_transport
    from types import SimpleNamespace
    now = [1000.0]
    core, service = real_setup(tmp_path, clock=lambda: now[0], synthesize=lambda *_: (wav(70), 'audio/wav'))
    adapter, _fake, _dm, _room = discord_setup(audio_source_factory=lambda *_: Source())
    adapter.interaction = service
    service.connect('discord-voice')
    original_lease = core.store.read('interaction_consumer', 'discord-voice')['lease']
    monkeypatch.setattr(discord_transport, '_heartbeat_clock', lambda: now[0] - 1000)
    class AdvancingVoice(Voice):
        def play(self, source, after):
            loop = asyncio.get_running_loop()
            loop.call_later(.001, lambda: now.__setitem__(0, 1033.0))
            loop.call_later(.065, lambda: now.__setitem__(0, 1070.0))
            loop.call_later(.08, after, None)
    adapter._controller = SimpleNamespace(_state='listening', _voice=AdvancingVoice(service))
    try:
        service.observe(InteractionObservation(audience='owner_private', request_id='long-lease',
            session_id='discord-private', consumer_id='discord-voice', channel='discord_voice',
            speaker='owner', text='Shana, read the result.', address_cue='name'))
        asyncio.run(adapter.deliver_voice_once())
        record = core.store.read('interaction_consumer', 'discord-voice')
        assert record['lease'] == original_lease and record['expires_at'] > now[0]
        assert service.status('discord-voice')['outputs'][0]['status'] == 'played'
        assert adapter.stats['audio_played'] == 1
    finally:
        service.close()
        core.close()
