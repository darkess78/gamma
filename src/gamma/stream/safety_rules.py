"""Stream safety rules: fast text checks, review timeouts, filtered replies and late overrides."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..config import settings
from ..safety.hard_blocklist import matched_rules as hard_block_matches
from ..safety.heuristic_filter import review as heuristic_review
from ..safety.llm_reviewer import LLMReviewDecision
from ..safety.privacy_guard import review_private_info_output
from ..safety.rewrite_guard import rewrite_text
from ..schemas.response import AssistantResponse
from .event_rules import _is_public_stream_event, _twitch_control_enabled
from .models import StreamInputEvent, StreamOutputEvent, output_context_from_input, output_events_from_response
from .output_events import _mark_filtered_output_events


class StreamSafetyReviewer(Protocol):
    """Protocol for stream safety reviewers.
    
    Methods:
        review: Review text for safety issues.
    """

    def review(self, text: str) -> LLMReviewDecision:
        ...


def _filtered_stream_response() -> AssistantResponse:
    audio_path = _filtered_audio_path()
    return AssistantResponse(
        spoken_text="filtered",
        emotion="concerned",
        audio_path=str(audio_path) if audio_path else None,
        audio_content_type="audio/wav" if audio_path else None,
        motions=[],
        tool_calls=[],
        tool_results=[],
        memory_candidates=[],
    )


def _filtered_audio_path() -> Path | None:
    raw_path = settings.stream_filtered_audio_path
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.is_absolute():
        path = settings.project_root / path
    return path if path.exists() else None


def llm_review_enabled() -> bool:
    """Whether the stream LLM safety review runs: server-side settings and the owner's
    go-live toggle only. Event metadata is caller-controlled and can't switch it off."""
    if not settings.twitch_llm_safety_review_enabled:
        return False
    try:
        from ..presence import load_presence_state
        presence = load_presence_state()
        if str(presence.get("mode") or "") == "go_live":
            return bool((presence.get("safety") or {}).get("llm_safety_review_enabled", True))
    except Exception:
        pass
    return True


def _parallel_stream_safety_enabled(event: StreamInputEvent, *, synthesize_speech: bool) -> bool:
    return (
        synthesize_speech
        and _is_public_stream_event(event)
        and settings.speech_filter_llm_enabled
        and llm_review_enabled()
    )


def _llm_review_payload(review: LLMReviewDecision) -> dict:
    blocked = review.action != "allow"
    return {
        "action": "filtered" if blocked else "allow",
        "blocked": blocked,
        "source": "stream_output_gate_llm",
        "stage": "llm_parallel",
        "matched_rules": [review.reason] if review.reason else [],
        "layers": ["llm"],
        "filter_action": review.action,
        "safe_output": "filtered" if blocked else None,
        "playback_approved": not blocked,
        "review_pending": False,
    }


def _late_safety_override_events(
    *,
    event: StreamInputEvent,
    turn_id: str,
    response: AssistantResponse,
    safety_decision: dict,
) -> list[StreamOutputEvent]:
    context = output_context_from_input(event)
    clear_events = [
        StreamOutputEvent(
            input_event_id=event.event_id,
            turn_id=turn_id,
            type="speech_ended",
            payload={
                "reason": "late_safety_filter",
                "interrupted": True,
                "clear_pending": True,
                "filtered": True,
                **context,
            },
        ),
        StreamOutputEvent(
            input_event_id=event.event_id,
            turn_id=turn_id,
            type="subtitle_line",
            payload={"text": "", "clear": True, "filtered": True, **context},
        ),
    ]
    filtered_events = output_events_from_response(input_event=event, turn_id=turn_id, response=response)
    return clear_events + _mark_filtered_output_events(filtered_events, safety_decision)


def _fast_stream_safety_review(text: str) -> dict:
    normalized = " ".join((text or "").split())
    privacy = review_private_info_output(normalized)
    if privacy.blocked:
        return {
            "blocked": True,
            "matched_rules": privacy.matched_rules,
            "layers": ["privacy_guard"],
            "filter_action": "privacy_refusal",
        }
    matched: list[str] = []
    layers: list[str] = []
    filter_action = "allow"
    if settings.speech_filter_hard_block_enabled:
        hard = hard_block_matches(normalized)
        if hard:
            matched.extend(hard)
            layers.append("hard_block")
            filter_action = "block"
    if filter_action == "allow" and settings.speech_filter_heuristic_enabled:
        heuristic = heuristic_review(text=normalized, level=settings.speech_filter_level)
        if heuristic.matched_rules:
            matched.extend(heuristic.matched_rules)
            layers.append("heuristic")
            filter_action = heuristic.action
    return {
        "blocked": filter_action == "block",
        "matched_rules": matched,
        "layers": layers,
        "filter_action": filter_action,
        "safe_text": rewrite_text(text=normalized, action=filter_action) if filter_action != "allow" else normalized,
    }


def _stream_review_timeout_action() -> str:
    if settings.stream_safety_review_timeout_action in {"skip", "defer", "hold"}:
        return settings.stream_safety_review_timeout_action
    return "skip"
