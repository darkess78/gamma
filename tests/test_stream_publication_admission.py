"""Mock publication transport must never queue on a real model owner's lease."""
from concurrent.futures import ThreadPoolExecutor
import threading
from urllib import request

import pytest

from gamma.config import settings
from gamma.integrations.discord.delivery import approved_reply
from gamma.llm import backend_admission
from test_conversation_recipient import fixture_state
from test_stream_publication_review import setup, TEXT


@pytest.mark.parametrize('case', ['speech', 'text_only', 'review_timeout', 'tts_failure'])
def test_synthetic_publication_has_private_real_admission_and_finishes_before_transport_teardown(
        fixture_state, tmp_path, monkeypatch, case):
    external = tmp_path / 'other-owner-real-admission'
    monkeypatch.setattr(backend_admission, '_coordination_root', lambda: external)
    late_requests = []
    def no_network(*args, **kwargs):
        late_requests.append(True)
        raise AssertionError('synthetic reviewer escaped its transport fixture')
    monkeypatch.setattr(request, 'urlopen', no_network)
    held = backend_admission.backend_slot(settings.local_llm_endpoint, foreground=True, timeout=1)
    held.__enter__()
    review_done, slot_open = threading.Event(), True
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        with monkeypatch.context() as scoped:
            delivery = 'text_only' if case == 'text_only' else 'speech'
            verdict = 'timeout' if case == 'review_timeout' else 'allow'
            brain, bus, transport, tts, event = setup(fixture_state, tmp_path, scoped,
                'stream_public', delivery, verdict)
            original = brain._safety_reviewer.review
            def observed(text):
                try: return original(text)
                finally: review_done.set()
            scoped.setattr(brain._safety_reviewer, 'review', observed)
            error = OSError('controlled TTS failure')
            if case == 'tts_failure':
                def fail(*_): raise error
                scoped.setattr(tts, 'synthesize', fail)
            if case != 'review_timeout': transport.release.set()
            future = pool.submit(brain.handle_event, event, synthesize_speech=True, fast_mode=False)
            try:
                assert transport.entered.wait(2), 'mock review blocked on another owner admission lease'
                if case == 'tts_failure':
                    with pytest.raises(OSError) as caught: future.result(timeout=2)
                    assert caught.value is error and bus.recent(limit=100) == []
                else:
                    result = future.result(timeout=2)
                    if case == 'review_timeout':
                        assert result.safety_decision['review_timeout'] and approved_reply(result.model_dump()) is None
                        assert not transport.finished.is_set()
                    else:
                        assert approved_reply(result.model_dump()) == TEXT
                transport.release.set()
                assert review_done.wait(2)
                assert slot_open, 'external owner lease was released to make this test pass'
                assert backend_admission._coordination_root() != external
            finally:
                transport.release.set()
                # Even a regression must release and join under the controlled
                # opener, never restore the real opener while review can run.
                held.__exit__(None, None, None); slot_open = False
                assert review_done.wait(2)
                pool.shutdown(wait=True)
        assert late_requests == []
        assert review_done.is_set() and backend_admission._coordination_root() == external
    finally:
        if slot_open: held.__exit__(None, None, None)
        pool.shutdown(wait=True)
