from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest

from gamma.config import settings
from gamma.safety import floor_log
from gamma.safety.llm_reviewer import LLMReviewDecision
from gamma.schemas.response import AssistantResponse
from gamma.stream.models import StreamActor, StreamInputEvent
from gamma.stream.publication_review import PublicationReviewMixin


@pytest.fixture
def gate(monkeypatch, tmp_path):
    monkeypatch.setattr(floor_log, "floor_log_path", lambda: tmp_path / "floor.jsonl")
    monkeypatch.setattr(settings, "speech_filter_hard_block_enabled", True)
    monkeypatch.setattr(settings, "speech_filter_llm_enabled", True)
    monkeypatch.setattr(settings, "twitch_llm_safety_review_enabled", True)
    monkeypatch.setattr("gamma.stream.publication_review.llm_review_enabled", lambda: True)
    event = StreamInputEvent(kind="chat_message", actor=StreamActor(source="twitch"), session_id="audit")
    return PublicationReviewMixin(), event


@pytest.mark.parametrize("action,count", [("block", 1), ("soften", 0), ("allow", 0)])
def test_sync_review_audits_only_floor(gate, action, count):
    gate, event = gate
    gate._safety_reviewer = SimpleNamespace(review=lambda text: LLMReviewDecision(action=action, reason="threat", confidence=1.0))
    gate._review_stream_output_with_llm(event, "reviewed line")
    entries = floor_log.recent_floor_events()
    assert len(entries) == count
    if entries:
        assert entries[0]["text"] == "reviewed line"
        assert entries[0]["session_id"] == "audit"


def test_fast_floor_is_logged_once(gate):
    gate, event = gate
    result = gate._review_stream_output(event, AssistantResponse(spoken_text="I am going to stab someone"), include_llm=False)
    assert result["blocked"]
    assert len(floor_log.recent_floor_events()) == 1


def test_late_review_logs_original_and_dispatches_clear(gate, monkeypatch):
    monkeypatch.setattr("gamma.stream.publication_review._apply_output_target_policy", lambda event, events: events)
    monkeypatch.setattr("gamma.stream.publication_review._filter_stream_output_events", lambda event, events: events)
    gate, event = gate
    done = Event()
    dispatched = []
    def dispatch(events):
        dispatched.extend(events)
        done.set()
    gate._output_dispatcher = SimpleNamespace(dispatch=dispatch)
    future = Future()
    future.set_result(LLMReviewDecision(action="block", reason="threat", confidence=1.0))
    gate._arm_late_safety_interrupt(event=event, turn_id="late", review_future=future,
                                  executor=ThreadPoolExecutor(1), reviewed_text="original late line")
    assert done.wait(3)
    assert floor_log.recent_floor_events()[0]["text"] == "original late line"
    assert any(e.payload.get("clear_pending") for e in dispatched)


def test_parallel_review_records_before_replacing_response(gate):
    gate, event = gate
    gate._safety_reviewer = SimpleNamespace(
        review=lambda text: LLMReviewDecision(action="block", reason="threat", confidence=1.0))
    gate._stream_speech_synthesizer = lambda: SimpleNamespace(synthesize=lambda *args: None)
    response, decision, late = gate._synthesize_with_parallel_review(
        event, AssistantResponse(spoken_text="parallel original"))
    assert decision["blocked"]
    assert response.spoken_text == "filtered"
    assert late is None
    assert floor_log.recent_floor_events()[0]["text"] == "parallel original"


def test_conversation_floor_stop_is_not_logged_twice(gate):
    gate, event = gate
    result = gate._review_stream_output(event, AssistantResponse(
        spoken_text="", tts_metadata={"speech_filter": {"blocked": True, "action": "floor_block"}}))
    assert result["blocked"]
    assert floor_log.recent_floor_events() == []
