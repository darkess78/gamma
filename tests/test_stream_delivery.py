import threading
from unittest.mock import Mock

import pytest

from gamma.brain.queue import BrainQueue
from gamma.config import settings
from gamma.errors import ConversationError
from gamma.stream.brain import StreamBrain
from gamma.stream.models import StreamInputEvent, StreamActor
from gamma.stream.trace import StreamTraceStore
from test_stream_brain import _FakeConversation, _FakeSpeechSynthesizer, _RecordingDispatcher


@pytest.mark.parametrize("superseded", [False, True])
def test_stream_delivery_releases_brain_preserves_order_and_rechecks_permission(tmp_path, monkeypatch, superseded):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'state.db'}")
    monkeypatch.setattr(settings, "speech_filter_llm_enabled", False)
    entered, release = threading.Event(), threading.Event()
    synth = _FakeSpeechSynthesizer()
    original = synth.synthesize
    def held(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    synth.synthesize = held
    dispatcher = _RecordingDispatcher()
    brain = StreamBrain(conversation=_FakeConversation(), speech_synthesizer=synth,
        output_dispatcher=dispatcher, trace_store=StreamTraceStore(tmp_path / "trace.jsonl"))
    brain._pacer = Mock()
    brain._pacer.apply.side_effect = lambda event, decision: decision
    brain._pacer.apply_budget.return_value = None
    queue = BrainQueue()
    allowed = [True]
    first = StreamInputEvent(kind="mic_transcript", text="First fictional turn", actor=StreamActor(source="local"))
    second = StreamInputEvent(kind="mic_transcript", text="Second fictional turn", actor=StreamActor(source="local"))
    try:
        delivery = queue.submit(lambda: brain.handle_event(first, synthesize_speech=True,
            defer_delivery=True, delivery_allowed=lambda: allowed[0])).result(3)
        assert entered.wait(3)
        following = queue.submit(lambda: brain.handle_event(second, defer_delivery=True)).result(3)
        assert not following.done() and dispatcher.calls == []
        allowed[0] = not superseded
        release.set()
        if superseded:
            with pytest.raises(ConversationError, match="superseded"):
                delivery.result(3)
        else:
            assert delivery.result(3).output_events
        assert following.result(3).output_events
        ids = [events[0].input_event_id for events in dispatcher.calls]
        assert ids == ([second.event_id] if superseded else [first.event_id, second.event_id])
    finally:
        release.set()
        queue.close()
        brain.delivery_jobs.close()
