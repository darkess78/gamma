"""Small rules about stream events and turn decisions shared by the stream modules."""
from __future__ import annotations

import time

from .models import StreamInputEvent, TurnDecision


def _twitch_control_enabled(event: StreamInputEvent, key: str, default: bool) -> bool:
    controls = event.metadata.get("twitch_controls")
    if not isinstance(controls, dict) or key not in controls:
        return default
    return bool(controls[key])


def _is_public_stream_event(event: StreamInputEvent) -> bool:
    return bool(event.metadata.get("public_output")) or event.actor.source == "twitch" or event.kind in {
        "chat_message",
        "follow",
        "raid",
        "donation",
        "bits",
        "subscription",
        "redeem",
    }


def _input_safety(event: StreamInputEvent) -> dict:
    value = event.metadata.get("input_safety")
    return value if isinstance(value, dict) else {}


def _estimate_speech_seconds(text: str) -> float:
    words = len((text or "").split())
    if words <= 0:
        return 0.0
    return round(max(0.8, words / 2.6), 3)


def _canned_response_from_decision(decision: TurnDecision) -> str | None:
    value = decision.metadata.get("canned_response")
    if not value:
        return None
    return str(value).strip() or None


def _metadata_without_canned_response(metadata: dict) -> dict:
    cleaned = dict(metadata)
    cleaned.pop("canned_response", None)
    cleaned.pop("canned_emotion", None)
    return cleaned


def _would_twitch_dry_run_suppress(decision: TurnDecision) -> bool:
    if decision.decision in {"ignore", "moderation_escalation"}:
        return False
    return decision.should_call_conversation or bool(_canned_response_from_decision(decision))


def _decision_would_speak(decision: TurnDecision) -> bool:
    if decision.decision not in {"reply", "acknowledge"}:
        return False
    return decision.should_call_conversation or bool(_canned_response_from_decision(decision))


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
