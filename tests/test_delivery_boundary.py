import io
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from gamma.config import settings
from gamma.delivery.synthesis import synthesize_response
from gamma.delivery.authority import DeliveryAuthority, DeliveryDenied
from gamma.safety.llm_reviewer import LLMReviewDecision, SpeechLLMReviewer
from gamma.schemas.response import AssistantResponse
from gamma.stream.brain import StreamBrain
from gamma.stream.models import StreamActor, StreamInputEvent
from gamma.stream.trace import StreamTraceStore
from test_stream_brain import _FakeConversation, _RecordingDispatcher, _FakeSpeechSynthesizer
from test_tool_authority import conversation
from gamma.identity.profile import SpeakerProfile
from gamma.memory.continuity import ConversationJournalEntry
from gamma.memory.state_authority import StateConflict
from sqlalchemy import select
from sqlmodel import Session


def test_pending_review_cannot_synthesize_or_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "speech_filter_llm_enabled", True)
    monkeypatch.setattr(settings, "stream_safety_review_timeout_seconds", 2)
    entered, release, synthesized = threading.Event(), threading.Event(), threading.Event()
    def review(text):
        entered.set()
        assert release.wait(3)
        return LLMReviewDecision("allow", "fixture", 1)
    synth = _FakeSpeechSynthesizer()
    original = synth.synthesize
    def checked_synthesize(*args, **kwargs):
        synthesized.set()
        assert release.is_set()
        return original(*args, **kwargs)
    synth.synthesize = checked_synthesize
    dispatcher = _RecordingDispatcher()
    brain = StreamBrain(conversation=_FakeConversation(), trace_store=StreamTraceStore(tmp_path / "trace.jsonl"),
        safety_reviewer=SimpleNamespace(review=review), speech_synthesizer=synth, output_dispatcher=dispatcher)
    event = StreamInputEvent(kind="chat_message", text="Shana say hello", actor=StreamActor(source="twitch", platform_id="fixture"),
        metadata={"twitch_controls": {"voice_enabled": True, "subtitles_enabled": True}})
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(brain.handle_event, event, synthesize_speech=True)
        try:
            assert entered.wait(2)
            assert not synthesized.wait(0.05)
            assert dispatcher.calls == []
        finally:
            release.set()
        result = future.result(timeout=3)
    assert synthesized.is_set() and dispatcher.calls
    assert result.assistant_response.tts_metadata["delivery_state"] == "prepared"


@pytest.mark.parametrize("raw", ["{}", "not JSON", '{"action":"unknown","reason":"bad","confidence":1}',
                                  '{"action":"allow","reason":"bad","confidence":"nan"}'])
def test_malformed_review_never_grants_permission(monkeypatch, raw):
    monkeypatch.setattr(settings, "speech_filter_llm_enabled", True)
    reviewer = SpeechLLMReviewer()
    reviewer._model = "fixture"
    with patch("gamma.safety.review_provider.request.urlopen", return_value=io.BytesIO(json.dumps({"response": raw}).encode())):
        verdict = reviewer.review("Fictional text")
    assert verdict.action == "block" and verdict.reason == "llm-reviewer-invalid"


def test_delivery_does_not_load_provider_for_silent_or_unapproved_response(tmp_path):
    synth = Mock()
    authority = DeliveryAuthority(database_url=f"sqlite:///{tmp_path / 'delivery.db'}")
    context = lambda: dict(target="dashboard_monitor", audience="private", policy="fixture")
    silent = AssistantResponse(spoken_text="", delivery_mode="silent", response_action="stay_silent")
    from state_fixtures import commit_response
    grant = authority.issue(commit_response(authority, silent), **context())
    synthesize_response(silent, synth, authority=authority, grant_id=grant, context=context)
    with pytest.raises(DeliveryDenied):
        synthesize_response(AssistantResponse(spoken_text="Unapproved"), synth, authority=authority, grant_id="forged", context=context)
    synth.synthesize.assert_not_called()


@pytest.mark.parametrize("wake", [False, True])
def test_failed_decision_never_calls_synthesis(conversation, monkeypatch, wake):
    conversation._llm = SimpleNamespace(generate_reply=lambda *args, **kw: SimpleNamespace(text="Fictional reply"))
    conversation._tts = Mock()
    monkeypatch.setattr(conversation._continuity, "complete_exchange", Mock(side_effect=StateConflict("stale")))
    with patch("gamma.conversation.service.build_system_prompt", return_value="Fixture"):
        with pytest.raises(StateConflict):
            if wake:
                conversation.respond_presence_wake(session_id="wake", speaker=SpeakerProfile("Fixture", "owner", is_owner=True),
                    wake_context="Fictional wake context", synthesize_speech=True)
            else:
                conversation.respond("Hello", synthesize_speech=True, fast_mode=True)
    conversation._tts.synthesize.assert_not_called()


def test_presence_wake_records_runtime_event_and_decision(conversation):
    conversation._llm = SimpleNamespace(generate_reply=lambda *args, **kw: SimpleNamespace(text="Fictional greeting"))
    conversation._tts = Mock()
    with patch("gamma.conversation.service.build_system_prompt", return_value="Fixture"):
        response = conversation.respond_presence_wake(session_id="wake", speaker=SpeakerProfile("Fixture", "owner", is_owner=True),
            wake_context="Fictional wake context", synthesize_speech=False)
    conversation._tts.synthesize.assert_not_called()
    with Session(conversation._continuity._engine) as session:
        rows = list(session.execute(select(ConversationJournalEntry).where(ConversationJournalEntry.exchange_id == response.route_trace_id)).scalars())
    assert [row.role for row in rows] == ["event", "assistant"]
    assert all(row.completion_status == "completed" for row in rows)


def test_deferred_delivery_commits_before_synthesis_and_rechecks_cancellation(conversation):
    conversation._llm = SimpleNamespace(generate_reply=lambda *args, **kw: SimpleNamespace(text="Fictional reply"))
    conversation._tts = Mock()
    with patch("gamma.conversation.service.build_system_prompt", return_value="Fixture"):
        response = conversation.respond("Hello", session_id="deferred", synthesize_speech=True,
            fast_mode=True, defer_delivery=True)
    conversation._tts.synthesize.assert_not_called()
    assert response.audio_path is None
    assert conversation.continuity_snapshot("deferred")["state_version"] == 1
    grant = response.tts_metadata["delivery_grant"]
    conversation.cancel_delivery(grant)
    with pytest.raises(DeliveryDenied):
        conversation.deliver_response(response, synthesize_speech=True)
    conversation._tts.synthesize.assert_not_called()


@pytest.mark.parametrize("closed", [False, True])
def test_stopped_tts_keeps_reviewed_voice_input_text(conversation, monkeypatch, closed):
    conversation._llm = SimpleNamespace(generate_reply=lambda *args, **kw: SimpleNamespace(text="Fictional reply"))
    worker = Mock()
    worker.status.return_value = {"enabled": not closed, "closed": closed}
    if not closed:
        worker.status.return_value["enabled"] = False
    conversation._tts = worker
    monkeypatch.setattr(settings, "tts_runtime", "worker")
    with patch("gamma.voice.tts_runtime.get_tts_worker", return_value=worker), patch("gamma.conversation.service.build_system_prompt", return_value="Fixture"):
        response = conversation.respond("Hello", session_id="tts-stopped", synthesize_speech=True,
            delivery_context="direct_voice", fast_mode=True)
    assert response.display_text == "Fictional reply"
    assert response.speech_text == ""
    assert response.delivery_mode == "text_only"
    assert response.audio_path is None
    assert response.tts_metadata["speech_unavailable"] == "tts_worker_stopped"
    assert conversation.check_delivery(response)["status"] == "dispatched"
    worker.synthesize.assert_not_called()
