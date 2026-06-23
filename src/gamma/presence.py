from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings
from .stream.models import StreamInputEvent

PRESENCE_MODES = {"sleep", "wake", "go_live", "break"}
SHANA_BOOTED_AT = datetime.now(timezone.utc)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def presence_state_path() -> Path:
    return settings.data_dir / "runtime" / "presence" / "state.json"


def default_presence_state() -> dict[str, Any]:
    return {
        "mode": "sleep",
        "desired_mode": "sleep",
        "requires_confirmation": False,
        "last_confirmed_live_at": None,
        "updated_at": utc_now(),
        "updated_by": "default",
        "autonomy": {
            "proactive_idle_enabled": False,
            "ambient_chat_enabled": False,
            "self_goal_proposals_enabled": False,
        },
        "inputs": {
            "local_mic": False,
            "twitch_irc_observe": False,
            "twitch_eventsub_observe": False,
        },
        "outputs": {
            "dashboard_monitor": True,
            "stream_public": False,
            "voice": False,
            "subtitles": False,
        },
        "safety": {
            "speech_filter_enabled": True,
            "llm_safety_review_enabled": True,
            "dry_run": True,
        },
        "activity": {
            "current_turn_id": None,
            "last_event_kind": None,
            "last_output_target": None,
            "stream_ready_mode": None,
        },
    }


def presence_state_for_mode(
    mode: str,
    *,
    previous: dict[str, Any] | None = None,
    updated_by: str = "dashboard",
    confirmed_live_at: str | None = None,
) -> dict[str, Any]:
    normalized = normalize_presence_mode(mode)
    state = deepcopy(default_presence_state())
    if previous:
        state["activity"] = dict(previous.get("activity") or state["activity"])
        state["last_confirmed_live_at"] = previous.get("last_confirmed_live_at")
    state["mode"] = normalized
    state["desired_mode"] = normalized
    state["requires_confirmation"] = False
    state["updated_at"] = utc_now()
    state["updated_by"] = updated_by

    if normalized == "sleep":
        return state
    if normalized == "wake":
        state["inputs"]["local_mic"] = True
        return state
    if normalized == "break":
        state["inputs"].update({"local_mic": True, "twitch_irc_observe": True, "twitch_eventsub_observe": True})
        return state

    state["last_confirmed_live_at"] = confirmed_live_at or utc_now()
    state["autonomy"].update(
        {
            "proactive_idle_enabled": True,
            "ambient_chat_enabled": True,
            "self_goal_proposals_enabled": True,
        }
    )
    state["inputs"].update({"local_mic": True, "twitch_irc_observe": True, "twitch_eventsub_observe": True})
    state["outputs"].update({"dashboard_monitor": True, "stream_public": True, "voice": True, "subtitles": True})
    state["safety"].update({"speech_filter_enabled": True, "llm_safety_review_enabled": True, "dry_run": False})
    return state


def normalize_presence_mode(mode: str) -> str:
    normalized = str(mode or "").strip().lower().replace("-", "_")
    if normalized not in PRESENCE_MODES:
        raise ValueError("unsupported presence mode")
    return normalized


def load_presence_state(
    *,
    path: Path | None = None,
    downgrade_stale_live: bool = False,
    booted_at: datetime | None = None,
) -> dict[str, Any]:
    state_path = path or presence_state_path()
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default_presence_state()
    except Exception:
        payload = {}
    state = merge_presence_state(payload if isinstance(payload, dict) else {})
    if downgrade_stale_live:
        state = downgrade_stale_live_state(state, booted_at=booted_at or SHANA_BOOTED_AT)
    return state


def save_presence_state(state: dict[str, Any], *, path: Path | None = None) -> dict[str, Any]:
    state_path = path or presence_state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    merged = merge_presence_state(state)
    temp_path = state_path.with_suffix(state_path.suffix + ".tmp")
    temp_path.write_text(json.dumps(merged, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(state_path)
    return merged


def merge_presence_state(payload: dict[str, Any]) -> dict[str, Any]:
    state = default_presence_state()
    mode = str(payload.get("mode") or state["mode"]).strip().lower()
    if mode in PRESENCE_MODES:
        state["mode"] = mode
    desired_mode = str(payload.get("desired_mode") or state["mode"]).strip().lower()
    state["desired_mode"] = desired_mode if desired_mode in PRESENCE_MODES else state["mode"]
    state["requires_confirmation"] = bool(payload.get("requires_confirmation", state["requires_confirmation"]))
    state["last_confirmed_live_at"] = payload.get("last_confirmed_live_at") or None
    state["updated_at"] = str(payload.get("updated_at") or state["updated_at"])
    state["updated_by"] = str(payload.get("updated_by") or state["updated_by"])
    for key in ("autonomy", "inputs", "outputs", "safety", "activity"):
        if isinstance(payload.get(key), dict):
            state[key].update(payload[key])
    return state


def downgrade_stale_live_state(state: dict[str, Any], *, booted_at: datetime) -> dict[str, Any]:
    if state.get("mode") != "go_live":
        return state
    confirmed_at = parse_utc(state.get("last_confirmed_live_at"))
    if confirmed_at is not None and confirmed_at > booted_at.astimezone(timezone.utc):
        return state
    downgraded = presence_state_for_mode("wake", previous=state, updated_by="restart_guard")
    downgraded["desired_mode"] = "go_live"
    downgraded["requires_confirmation"] = True
    downgraded["last_confirmed_live_at"] = state.get("last_confirmed_live_at")
    return downgraded


def is_public_stream_event(event: StreamInputEvent) -> bool:
    if event.actor.source == "twitch":
        return True
    return event.kind in {"chat_message", "follow", "raid", "donation", "bits", "subscription", "redeem"}


def apply_presence_to_stream_event(
    event: StreamInputEvent,
    *,
    synthesize_speech: bool,
    state: dict[str, Any] | None = None,
) -> tuple[StreamInputEvent, bool]:
    presence = state or load_presence_state(downgrade_stale_live=True)
    mode = str(presence.get("mode") or "sleep")
    metadata = dict(event.metadata or {})
    metadata["presence_mode"] = mode
    metadata["presence"] = public_presence_payload(presence)

    if event.kind == "conversation_lull" and mode != "go_live":
        metadata["presence_suppressed"] = True
        return event.model_copy(update={"metadata": metadata}), False

    if not is_public_stream_event(event):
        if mode in {"wake", "go_live", "break"} and not metadata.get("output_target_policy"):
            metadata["output_target_policy"] = "dashboard_monitor"
        return event.model_copy(update={"metadata": metadata}), synthesize_speech

    controls = dict(metadata.get("twitch_controls") or {})
    if mode == "go_live":
        controls.update(
            {
                "dry_run": bool(presence["safety"].get("dry_run", False)),
                "voice_enabled": bool(presence["outputs"].get("voice", True)),
                "subtitles_enabled": bool(presence["outputs"].get("subtitles", True)),
                "ambient_chat_enabled": bool(presence["autonomy"].get("ambient_chat_enabled", True)),
                "mention_replies_enabled": True,
                "spam_quips_enabled": True,
                "self_goal_proposals_enabled": bool(presence["autonomy"].get("self_goal_proposals_enabled", True)),
                "llm_safety_review_enabled": bool(presence["safety"].get("llm_safety_review_enabled", True)),
            }
        )
        metadata["output_target_policy"] = "stream_public"
        metadata["twitch_controls"] = controls
        return event.model_copy(update={"metadata": metadata}), bool(presence["outputs"].get("voice", True))

    controls.update(
        {
            "dry_run": True,
            "voice_enabled": False,
            "subtitles_enabled": False,
            "ambient_chat_enabled": False,
            "mention_replies_enabled": False,
            "spam_quips_enabled": False,
            "self_goal_proposals_enabled": False,
            "llm_safety_review_enabled": True,
        }
    )
    metadata["twitch_controls"] = controls
    metadata["presence_suppressed"] = True
    metadata["output_target_policy"] = "dashboard_monitor"
    return event.model_copy(update={"metadata": metadata}), False


def public_presence_payload(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": state.get("mode"),
        "desired_mode": state.get("desired_mode"),
        "requires_confirmation": bool(state.get("requires_confirmation")),
        "outputs": dict(state.get("outputs") or {}),
        "autonomy": dict(state.get("autonomy") or {}),
        "safety": dict(state.get("safety") or {}),
    }
