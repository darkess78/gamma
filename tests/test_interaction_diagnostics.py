"""Actual interaction phase timings and content-free retained delivery diagnostics."""
import json
import threading

import pytest

from gamma.schemas.response import AssistantResponse
from test_private_interaction import setup, observation, wav, get_output, ack


class Clock:
    def __init__(self):
        self.wall = 1000.0
        self.mono = 10.0

    def advance(self, seconds):
        self.wall += seconds
        self.mono += seconds

    def time(self):
        return self.wall

    def monotonic(self):
        return self.mono


class MeasuredWait:
    def __init__(self, clock):
        self.clock = clock
        self.lock = threading.Lock()

    def acquire(self, **kwargs):
        self.clock.advance(.05)
        return self.lock.acquire(**kwargs)

    def release(self):
        self.lock.release()


def make_timed(tmp_path, *, jump_wall=False):
    clock = Clock()
    def transcribe(_raw):
        clock.advance(.125)
        if jump_wall:
            clock.wall -= 500
        return 'Who am I?'
    def draft(*_):
        clock.advance(.25)
        return AssistantResponse(display_text='You are Neety.', delivery_mode='text_only')
    def synthesize(*_):
        clock.advance(.4)
        return wav(), 'audio/wav'
    core, service = setup(tmp_path, transcribe=transcribe, draft=draft,
                          synthesize=synthesize, clock=clock.time)
    service.monotonic = clock.monotonic
    service._synthesis = MeasuredWait(clock)
    return core, service, clock


@pytest.mark.parametrize('channel', ['private_voice', 'discord_voice'])
def test_voice_phases_are_measured_separately_and_returned(tmp_path, channel):
    core, service, _clock = make_timed(tmp_path)
    try:
        receipt = service.submit_audio(observation(channel=channel, text=''), wav())
        assert receipt['timing_ms'] == {'stt_ms':125.0, 'core_ms':250.0, 'foreground_wait_ms':0.0,
            'synthesis_wait_ms':50.0, 'total_ms':825.0, 'first_audio_ready_ms':825.0}
        record = core.store.read('interaction_request', 'one')
        assert record['timing_ms'] == receipt['timing_ms']
        assert record['final_outcome'] == receipt['final_outcome'] == {
            'status':'generated', 'reason':'checked_output_generated'}
        assert get_output(service)['first_audio_ready_ms'] == 825.0
    finally:
        service.close(); core.close()


def test_phase_measurement_uses_monotonic_time_across_wall_clock_change(tmp_path):
    core, service, _clock = make_timed(tmp_path, jump_wall=True)
    try:
        receipt = service.submit_audio(observation(text=''), wav())
        assert receipt['timing_ms']['total_ms'] == 825.0
        assert receipt['timing_ms']['first_audio_ready_ms'] == 825.0
        assert get_output(service)['first_audio_ready_ms'] == 825.0
    finally:
        service.close(); core.close()


@pytest.mark.parametrize('channel', ['desktop_text', 'discord_dm'])
def test_text_phases_have_no_invented_stt_or_audio_time(tmp_path, channel):
    core, service, _clock = make_timed(tmp_path)
    try:
        receipt = service.observe(observation(channel=channel))
        assert receipt['timing_ms'] == {'stt_ms':0.0, 'core_ms':250.0, 'foreground_wait_ms':0.0,
            'synthesis_wait_ms':0.0, 'total_ms':250.0}
        assert receipt['final_outcome']['status'] == 'generated'
    finally:
        service.close(); core.close()


def test_silent_outcome_and_timing_survive_cache_expiry_without_model_reason_or_replay(tmp_path):
    clock, calls = Clock(), []
    def draft(*_):
        calls.append(True); clock.advance(.2)
        return AssistantResponse(delivery_mode='silent', response_action='stay_silent',
            reason_code='PRIVATE_MODEL_REASON', internal_summary='PRIVATE_SUMMARY')
    core, service = setup(tmp_path, draft=draft, clock=clock.time)
    service.monotonic = clock.monotonic
    try:
        receipt = service.observe(observation())
        assert receipt['final_outcome'] == {'status':'silent', 'reason':'core_selected_silence'}
        assert receipt['timing_ms']['core_ms'] == receipt['timing_ms']['total_ms'] == 200.0
        clock.advance(61)
        service.connect('desktop')
        replay = service.observe(observation())
        assert replay['status'] == 'expired' and replay['output_id'] is None
        assert replay['timing_ms'] == receipt['timing_ms']
        record = core.store.read('interaction_request', 'one')
        assert record['final_outcome'] == receipt['final_outcome']
        assert 'PRIVATE_' not in json.dumps(record)
        assert calls == [True] and service.next_output('desktop')['output'] is None
    finally:
        service.close(); core.close()


def test_failed_stt_retains_only_numeric_timing_and_fixed_backend_outcome(tmp_path):
    clock, calls = Clock(), []
    def transcribe(_raw):
        calls.append(True); clock.advance(.1)
        raise RuntimeError('PRIVATE_PROVIDER_DETAILS')
    core, service = setup(tmp_path, transcribe=transcribe, clock=clock.time)
    service.monotonic = clock.monotonic
    try:
        with pytest.raises(RuntimeError, match='PRIVATE_PROVIDER_DETAILS'):
            service.submit_audio(observation(text=''), wav())
        record = core.store.read('interaction_request', 'one')
        assert record['timing_ms'] == {'stt_ms':100.0, 'core_ms':0.0, 'foreground_wait_ms':0.0,
                                     'synthesis_wait_ms':0.0, 'total_ms':100.0}
        assert record['final_outcome'] == {'status':'failed', 'reason':'provider_or_turn_failed_no_replay'}
        clock.advance(61); service.connect('desktop')
        replay = service.submit_audio(observation(text=''), wav())
        assert replay['status'] == 'expired' and calls == [True]
        assert replay['final_outcome'] == record['final_outcome']
        assert 'PRIVATE_' not in json.dumps(core.store.read('interaction_request', 'one'))
    finally:
        service.close(); core.close()


def test_real_source_forget_clears_cached_content_and_cannot_replay_with_diagnostics(tmp_path):
    core, service, _clock = make_timed(tmp_path)
    try:
        service.submit_audio(observation(text=''), wav())
        record = core.store.read('interaction_request', 'one')
        turn = core.store.read('turn', 'one')
        core.personal_control('erase-source', 'forget', {'target_id':turn['personal_receipt']['source_id']})
        assert service.next_output('desktop')['output'] is None
        replay = service.submit_audio(observation(text=''), wav())
        assert replay == {'ok':True, 'status':'expired', 'reason':'source_removed', 'output_id':None}
        redacted = core.store.read('interaction_request', 'one')
        assert redacted['timing_ms'] == record['timing_ms']
        assert redacted['final_outcome'] == {'status':'expired', 'reason':'source_removed'}
        assert 'Who am I?' not in json.dumps(redacted) and 'You are Neety.' not in json.dumps(redacted)
        assert not list(service.root.glob('*.wav'))
    finally:
        service.close(); core.close()


def test_audio_ack_timeline_has_distinct_events_and_idempotent_ack_never_retimes(tmp_path):
    clock = Clock()
    core, service = setup(tmp_path, clock=clock.time)
    try:
        service.observe(observation()); output = get_output(service)
        clock.advance(1); attempted = ack(service, output, 'attempted')
        clock.advance(2); started = ack(service, output, 'started')
        clock.advance(3); played = ack(service, output, 'played')
        assert attempted['attempted_at'] == 1001.0
        assert started['attempted_at'] == 1001.0 and started['started_at'] == 1003.0
        assert played['attempted_at'] == 1001.0 and played['started_at'] == 1003.0
        assert played['played_at'] == played['acknowledged_at'] == 1006.0
        clock.advance(4); repeated = ack(service, output, 'played')
        for field in ('attempted_at', 'started_at', 'played_at', 'acknowledged_at'):
            assert repeated[field] == played[field]
        assert repeated['status'] == 'played'
        assert 'delivered_at' not in repeated
    finally:
        service.close(); core.close()


def test_text_delivery_timestamp_is_not_a_playback_timestamp(tmp_path):
    clock = Clock()
    core, service = setup(tmp_path, clock=clock.time)
    try:
        service.observe(observation(channel='discord_dm')); output = get_output(service)
        clock.advance(1); ack(service, output, 'attempted')
        clock.advance(2); delivered = ack(service, output, 'delivered', receipt_id='receipt')
        assert delivered['attempted_at'] == 1001.0
        assert delivered['delivered_at'] == delivered['acknowledged_at'] == 1003.0
        assert 'started_at' not in delivered and 'played_at' not in delivered
        clock.advance(4)
        assert ack(service, output, 'delivered', receipt_id='receipt') == delivered
    finally:
        service.close(); core.close()
