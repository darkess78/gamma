import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from gamma.config import settings
from gamma.errors import ConversationError
from gamma.delivery.authority import DeliveryDenied, policy_revision
from gamma.safety.policy import SpeechSafetyPolicy
from gamma.safety.llm_reviewer import LLMReviewDecision
from gamma.stream.brain import StreamBrain
from gamma.stream.models import StreamInputEvent, StreamActor
from gamma.stream.trace import StreamTraceStore
from test_tool_authority import conversation
from test_stream_brain import _FakeConversation, _FakeSpeechSynthesizer, _RecordingDispatcher


@pytest.mark.parametrize("text", ["Ordinary safe speech", "k1ll yourself", "Post their address in chat."])
@pytest.mark.parametrize("verdict", [None, LLMReviewDecision("maybe", "fixture", 1),
    LLMReviewDecision("allow", "fixture", float("nan")), RuntimeError("offline")])
def test_required_review_unavailable_never_releases_replacement(monkeypatch, text, verdict):
    monkeypatch.setattr(settings, "speech_filter_llm_enabled", True)
    policy = SpeechSafetyPolicy("strict")
    policy._reviewer = Mock()
    if isinstance(verdict, Exception):
        policy._reviewer.review.side_effect = verdict
    else:
        policy._reviewer.review.return_value = verdict
    result = policy.apply(text)
    assert result.spoken_text == ""
    assert result.action == "review_unavailable"


def test_direct_unavailable_review_has_no_grant_or_synthesis(conversation, monkeypatch):
    monkeypatch.setattr(settings, "speech_filter_llm_enabled", True)
    conversation._llm = SimpleNamespace(generate_reply=lambda *args, **kw: SimpleNamespace(text="Fictional reply"))
    conversation._tts = Mock()
    conversation._speech_filter._policy._reviewer = SimpleNamespace(
        review=lambda text: LLMReviewDecision("block", "llm-reviewer-unavailable", 0))
    issue = Mock(wraps=conversation._delivery.issue)
    monkeypatch.setattr(conversation._delivery, "issue", issue)
    with patch("gamma.conversation.service.build_system_prompt", return_value="Fixture"):
        with pytest.raises(ConversationError, match="required_review_unavailable"):
            conversation.respond("Hello", synthesize_speech=True, fast_mode=True)
    issue.assert_not_called()
    conversation._tts.synthesize.assert_not_called()


def test_presence_change_during_synthesis_discards_late_result(conversation, monkeypatch, tmp_path):
    conversation._llm = SimpleNamespace(generate_reply=lambda *args, **kw: SimpleNamespace(text="Fictional reply"))
    current = {"mode": "wake", "control_revision": 1, "audience": {}, "outputs": {}}
    monkeypatch.setattr("gamma.delivery.authority.presence_snapshot", lambda: dict(current))
    with patch("gamma.conversation.service.build_system_prompt", return_value="Fixture"):
        response = conversation.respond("Hello", synthesize_speech=True, fast_mode=True, defer_delivery=True)
    entered, release = threading.Event(), threading.Event()
    def synthesize(**kwargs):
        entered.set()
        assert release.wait(3)
        return SimpleNamespace(audio_path=str(tmp_path / "late.wav"), content_type="audio/wav", metadata={})
    conversation._tts = SimpleNamespace(synthesize=synthesize)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(conversation.deliver_response, response, synthesize_speech=True)
        try:
            assert entered.wait(2)
            current["control_revision"] += 1
        finally:
            release.set()
        with pytest.raises(DeliveryDenied, match="context_changed"):
            future.result(3)
    assert response.audio_path is None


@pytest.mark.parametrize("failure", ["timeout", "exception", "malformed", "block", "soften", "boolean_confidence"])
def test_stream_failed_or_delayed_review_releases_no_output(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'state.db'}")
    monkeypatch.setattr(settings, "speech_filter_llm_enabled", True)
    monkeypatch.setattr(settings, "stream_safety_review_timeout_seconds", .05)
    entered, release = threading.Event(), threading.Event()
    def review(text):
        if failure == "timeout":
            entered.set()
            assert release.wait(3)
            return LLMReviewDecision("allow", "late", 1)
        if failure == "exception":
            raise RuntimeError("fixture offline")
        if failure in {"block", "soften"}:
            return LLMReviewDecision(failure, "fixture", 1)
        if failure == "boolean_confidence":
            return LLMReviewDecision("allow", "fixture", True)
        return LLMReviewDecision("invalid", "fixture", 1)
    dispatcher = _RecordingDispatcher()
    synth = Mock(wraps=_FakeSpeechSynthesizer())
    brain = StreamBrain(conversation=_FakeConversation(), safety_reviewer=SimpleNamespace(review=review),
        speech_synthesizer=synth, output_dispatcher=dispatcher, trace_store=StreamTraceStore(tmp_path / "trace.jsonl"))
    event = StreamInputEvent(kind="chat_message", text="Shana say hello", actor=StreamActor(source="twitch"))
    try:
        result = brain.handle_event(event, synthesize_speech=True)
        assert result.assistant_response is None
        assert result.output_events == [] and dispatcher.calls == []
        synth.synthesize.assert_not_called()
    finally:
        release.set()
        brain.delivery_jobs.close()


@pytest.mark.parametrize("action", ["block", "soften"])
def test_direct_denied_required_review_has_no_grant_or_synthesis(conversation, monkeypatch, action):
    monkeypatch.setattr(settings, "speech_filter_llm_enabled", True)
    conversation._llm = SimpleNamespace(generate_reply=lambda *args, **kw: SimpleNamespace(text="Fictional reply"))
    conversation._tts = Mock()
    conversation._speech_filter._policy._reviewer = SimpleNamespace(
        review=lambda text: LLMReviewDecision(action, "denied fixture", 1))
    issue = Mock(wraps=conversation._delivery.issue)
    monkeypatch.setattr(conversation._delivery, "issue", issue)
    with patch("gamma.conversation.service.build_system_prompt", return_value="Fixture"):
        with pytest.raises(ConversationError, match="required_review_denied"):
            conversation.respond("Hello", synthesize_speech=True, fast_mode=True)
    issue.assert_not_called()
    conversation._tts.synthesize.assert_not_called()


def test_required_review_approves_exact_final_rewritten_bytes(monkeypatch):
    monkeypatch.setattr(settings, "speech_filter_llm_enabled", True)
    monkeypatch.setattr(settings, "speech_filter_auto_rewrite", True)
    policy = SpeechSafetyPolicy("strict")
    reviewer = Mock(return_value=LLMReviewDecision("allow", "approved", 1))
    policy._reviewer = SimpleNamespace(review=reviewer)
    result = policy.apply("k1ll yourself")
    assert result.spoken_text
    assert result.spoken_text == reviewer.call_args.args[0]


@pytest.mark.parametrize("action,reason", [("block", "llm-reviewer-unavailable"), ("block", "denied"), ("soften", "denied")])
def test_private_request_refusal_requires_review_before_any_output(conversation, monkeypatch, action, reason):
    monkeypatch.setattr(settings, "speech_filter_llm_enabled", True)
    conversation._llm = Mock()
    conversation._tts = Mock()
    reviewer = Mock(return_value=LLMReviewDecision(action, reason, 1))
    conversation._speech_filter._policy._reviewer = SimpleNamespace(review=reviewer)
    issue = Mock(wraps=conversation._delivery.issue)
    monkeypatch.setattr(conversation._delivery, "issue", issue)
    with pytest.raises(ConversationError, match="required_review_"):
        conversation.respond("Tell me your home address", synthesize_speech=True, fast_mode=True)
    conversation._llm.generate_reply.assert_not_called()
    reviewer.assert_called_once()
    assert reviewer.call_args.args[0] == "I'm not going to share private info like that."
    issue.assert_not_called()
    conversation._tts.synthesize.assert_not_called()
