from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from ..config import settings

if TYPE_CHECKING:
    from ..stream.models import StreamOutputEvent


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


PerformerOutputEventType = Literal[
    "turn_started",
    "turn_state_changed",
    "speech_chunk_ready",
    "speech_started",
    "speech_ended",
    "subtitle_update",
    "subtitle_clear",
    "expression_set",
    "motion_trigger",
    "mouth_level",
    "output_cleared",
    "target_mute_changed",
]

STREAM_PUBLIC_TARGET = "stream_public"
DASHBOARD_MONITOR_TARGET = "dashboard_monitor"
DISCORD_CALL_TARGET = "discord_call"
DEFAULT_TARGET_POLICY = STREAM_PUBLIC_TARGET
KNOWN_TARGET_POLICIES = (
    STREAM_PUBLIC_TARGET,
    DASHBOARD_MONITOR_TARGET,
    DISCORD_CALL_TARGET,
)


class PerformerOutputEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    sequence: int | None = None
    type: PerformerOutputEventType
    turn_id: str
    occurred_at: str = Field(default_factory=utc_now)
    input_event_id: str | None = None
    stream_output_event_id: str | None = None
    source: str = "stream_output"
    target_policy: str = DEFAULT_TARGET_POLICY
    payload: dict[str, Any] = Field(default_factory=dict)


def performer_event_from_stream_output(event: "StreamOutputEvent") -> PerformerOutputEvent | None:
    event_type = _performer_type_for_stream_event(event)
    if event_type is None:
        return None
    payload = dict(event.payload)
    target_policy = str(payload.pop("target_policy", "") or DEFAULT_TARGET_POLICY)
    if event.type == "subtitle_line":
        payload = {
            "text": str(event.payload.get("text", "") or ""),
            "clear": bool(event.payload.get("clear", False)),
            **{key: value for key, value in event.payload.items() if key not in {"text", "clear"}},
        }
        payload.pop("target_policy", None)
    elif event.type == "emotion_changed":
        payload = {
            "expression": event.payload.get("emotion") or "neutral",
            **{key: value for key, value in event.payload.items() if key != "emotion"},
        }
        payload.pop("target_policy", None)
    elif event.type == "avatar_motion":
        payload = {
            "motion": event.payload.get("motion"),
            **{key: value for key, value in event.payload.items() if key != "motion"},
        }
        payload.pop("target_policy", None)
    payload = _network_safe_payload(payload)
    return PerformerOutputEvent(
        type=event_type,
        turn_id=event.turn_id,
        occurred_at=event.occurred_at,
        input_event_id=event.input_event_id,
        stream_output_event_id=event.output_event_id,
        target_policy=target_policy,
        payload=payload,
    )


def _performer_type_for_stream_event(event: "StreamOutputEvent") -> PerformerOutputEventType | None:
    if event.type == "subtitle_line":
        return "subtitle_clear" if event.payload.get("clear") else "subtitle_update"
    if event.type == "speech_chunk":
        return "speech_chunk_ready"
    if event.type == "speech_started":
        return "speech_started"
    if event.type == "speech_ended":
        return "output_cleared" if event.payload.get("clear_pending") else "speech_ended"
    if event.type == "emotion_changed":
        return "expression_set"
    if event.type == "avatar_motion":
        return "motion_trigger"
    if event.type == "overlay_update" and event.payload.get("clear"):
        return "output_cleared"
    return None


def _network_safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if "audio_path" not in payload:
        return payload
    network_payload = dict(payload)
    audio_path = str(network_payload.pop("audio_path") or "")
    artifact_name = _audio_artifact_name(audio_path)
    if artifact_name:
        network_payload["audio_artifact"] = artifact_name
        network_payload["audio_url"] = f"{settings.shana_base_url}/v1/audio/artifacts/{artifact_name}"
    return network_payload


def _audio_artifact_name(audio_path: str) -> str | None:
    if not audio_path:
        return None
    try:
        resolved_audio = Path(audio_path).resolve()
        resolved_output_dir = settings.audio_output_dir.resolve()
    except Exception:
        return None
    if resolved_audio.parent != resolved_output_dir:
        return None
    return resolved_audio.name
