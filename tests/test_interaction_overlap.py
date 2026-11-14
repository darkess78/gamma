"""Two admitted audio inputs transcribe while Core decisions remain serialized."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from gamma.schemas.response import AssistantResponse
from test_private_interaction import setup, observation, wav
from test_interaction_diagnostics import Clock


def test_second_audio_transcribes_during_older_core_then_gets_exactly_one_turn(tmp_path):
    entered, release, transcribed_second = threading.Event(), threading.Event(), threading.Event()
    first_audio, second_audio = wav(.1), wav(.2)
    transcripts, drafts, counts = [], [], {'active':0, 'maximum':0}
    lock = threading.Lock()
    def transcribe(raw):
        text = 'First question?' if raw == first_audio else 'Second question?'
        transcripts.append(text)
        if text == 'Second question?':
            transcribed_second.set()
        return text
    def draft(text, _context):
        with lock:
            counts['active'] += 1
            counts['maximum'] = max(counts['maximum'], counts['active'])
            drafts.append(text)
        try:
            if text == 'First question?':
                entered.set(); assert release.wait(3)
            return AssistantResponse(display_text='Answer to '+text, delivery_mode='text_only')
        finally:
            with lock:
                counts['active'] -= 1
    core, service = setup(tmp_path, transcribe=transcribe, draft=draft)
    try:
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(service.submit_audio, observation('first', text=''), first_audio)
            assert entered.wait(1)
            second = pool.submit(service.submit_audio, observation('second', text=''), second_audio)
            try:
                assert transcribed_second.wait(1), 'Second admitted voice input was dropped before STT.'
                assert not second.done()
                third = service.submit_audio(observation('third', text=''), wav(.3))
                assert third['status'] == 'busy' and third['reason'] == 'private_interaction_capacity'
                assert len(transcripts) == 2
            finally:
                release.set()
            first.result(3)
            receipt = second.result(3)
        assert receipt['status'] == 'generated'
        assert receipt['timing_ms']['foreground_wait_ms'] >= 0
        assert drafts == transcripts == ['First question?', 'Second question?']
        assert counts['maximum'] == 1
        assert core.store.read('turn','second')['status'] == 'completed'
        assert service.submit_audio(observation('second', text=''), second_audio) == receipt
        assert len(drafts) == 2
    finally:
        release.set(); service.close(); core.close()


@pytest.mark.parametrize('cancel', ['voice_disabled', 'disconnect', 'sleep', 'close'])
def test_waiting_audio_is_cancelled_before_core_when_authority_changes(tmp_path, cancel):
    entered, release, second_stt = threading.Event(), threading.Event(), threading.Event()
    transcripts, drafts = [], []
    def transcribe(_raw):
        text = 'First question?' if not transcripts else 'Second question?'
        transcripts.append(text)
        if len(transcripts) == 2:
            second_stt.set()
        return text
    def draft(text, _context):
        drafts.append(text)
        entered.set(); assert release.wait(3)
        return AssistantResponse(display_text='Answer.', delivery_mode='text_only')
    core, service = setup(tmp_path, transcribe=transcribe, draft=draft)
    try:
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(service.submit_audio, observation('first', text=''), wav())
            assert entered.wait(1)
            second = pool.submit(service.submit_audio, observation('second', text=''), wav())
            try:
                assert second_stt.wait(1), 'Waiting voice input did not reach transcription.'
                if cancel == 'voice_disabled':
                    service.control('voice_enabled', False)
                elif cancel == 'disconnect':
                    service.disconnect('desktop')
                elif cancel == 'sleep':
                    core.control('sleep-request', 'sleep')
                else:
                    service.close()
                receipt = second.result(1)
                assert receipt['status'] == 'cancelled'
                assert receipt['reason'] == 'foreground_wait_cancelled'
                assert receipt['timing_ms']['core_ms'] == 0
                assert core.store.read('turn','second') is None
                assert drafts == ['First question?']
            finally:
                release.set()
            first.result(3)
    finally:
        release.set(); service.close(); core.close()


def test_foreground_wait_deadline_returns_retained_diagnosis_without_inference_replay(tmp_path):
    clock, transcripts, drafts = Clock(), [], []
    class UnavailableCoreSlot:
        def acquire(self, *, blocking=True, timeout=-1):
            if blocking:
                assert 0 < timeout <= .05
                clock.advance(30.1)
            return False
        def release(self):
            pytest.fail('An unacquired Core slot was released.')
    core, service = setup(tmp_path, clock=clock.time,
        transcribe=lambda _raw:(transcripts.append(True) or 'A question?'),
        draft=lambda *_:(drafts.append(True) or pytest.fail('Deadline reached but Core was invoked.')))
    service.monotonic = clock.monotonic
    service._flight = UnavailableCoreSlot()
    try:
        receipt = service.submit_audio(observation(text=''), wav())
        assert receipt['status'] == 'busy' and receipt['reason'] == 'foreground_wait_timeout'
        assert receipt['timing_ms']['foreground_wait_ms'] == 30100.0
        assert receipt['timing_ms']['core_ms'] == 0 and not drafts
        assert service.submit_audio(observation(text=''), wav()) == receipt
        assert transcripts == [True]
        assert core.store.read('interaction_request','one')['final_outcome'] == {
            'status':'busy', 'reason':'foreground_wait_timeout'}
    finally:
        service.close(); core.close()


@pytest.mark.parametrize('same_payload', [True, False])
def test_simultaneous_request_id_reservation_never_overwrites_or_replays(tmp_path, same_payload):
    from concurrent.futures import FIRST_COMPLETED, wait
    barrier, release = threading.Barrier(2), threading.Event()
    transcribes, drafts = [], []
    class SynchronizedAdmissions:
        def __init__(self):
            self.semaphore = threading.BoundedSemaphore(2)
        def acquire(self, **kwargs):
            accepted = self.semaphore.acquire(**kwargs)
            if accepted:
                barrier.wait(timeout=2)
            return accepted
        def release(self):
            self.semaphore.release()
    def transcribe(_raw):
        transcribes.append(True)
        assert release.wait(3)
        return 'A single admitted question?'
    def draft(*_):
        drafts.append(True)
        return AssistantResponse(display_text='A single answer.', delivery_mode='text_only')
    core, service = setup(tmp_path, transcribe=transcribe, draft=draft)
    service._requests = SynchronizedAdmissions()
    try:
        with ThreadPoolExecutor(2) as pool:
            jobs = [pool.submit(service.submit_audio, observation(text=''), wav(.1)),
                    pool.submit(service.submit_audio, observation(text=''), wav(.1 if same_payload else .2))]
            try:
                done, _ = wait(jobs, timeout=1, return_when=FIRST_COMPLETED)
                assert len(done) == 1
                loser = next(iter(done))
                if same_payload:
                    assert loser.result()['status'] == 'pending_or_uncertain_no_replay'
                else:
                    with pytest.raises(ValueError, match='request id reused'):
                        loser.result()
            finally:
                release.set()
            winner = next(job for job in jobs if job is not loser)
            assert winner.result(3)['status'] == 'generated'
        assert transcribes == drafts == [True]
        record = core.store.read('interaction_request', 'one')
        assert record['receipt']['status'] == 'generated'
        assert record['final_outcome']['status'] == 'generated'
        assert len(record['output_ids']) == 1
    finally:
        release.set(); service.close(); core.close()
