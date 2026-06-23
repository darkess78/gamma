from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .config import settings
from .conversation.service import ConversationService
from .identity.profile import SpeakerProfile, UNKNOWN_PUBLIC
from .identity.resolver import IdentityResolver
from .memory.service import MemoryService
from .performer.bus import PerformerEventBus, get_performer_event_bus
from .performer.models import DASHBOARD_MONITOR_TARGET
from .schemas.presence import AudienceSelection
from .stream.models import StreamActor, StreamInputEvent, output_events_from_response
from .stream.output import StreamOutputDispatcher

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
        "audience": {"kind": "unknown", "known_person_id": None, "display_name": "Unknown"},
        "lifecycle": {
            "last_sleep_at": None,
            "last_wake_at": None,
            "last_interaction_at": None,
        },
        "wake": {
            "enabled": True,
            "last_status": None,
            "last_opening": None,
            "last_event_at": None,
            "recent_openings": [],
            "suppression_reason": None,
        },
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
        state["audience"] = dict(previous.get("audience") or state["audience"])
        state["lifecycle"] = dict(previous.get("lifecycle") or state["lifecycle"])
        state["wake"] = dict(previous.get("wake") or state["wake"])
    state["mode"] = normalized
    state["desired_mode"] = normalized
    state["requires_confirmation"] = False
    state["updated_at"] = utc_now()
    state["updated_by"] = updated_by

    if normalized == "sleep":
        state["lifecycle"]["last_sleep_at"] = state["updated_at"]
        return state
    if normalized == "wake":
        state["inputs"]["local_mic"] = True
        state["outputs"]["voice"] = True
        state["outputs"]["subtitles"] = True
        state["lifecycle"]["last_wake_at"] = state["updated_at"]
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
    for key in ("audience", "lifecycle", "wake", "autonomy", "inputs", "outputs", "safety", "activity"):
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
    downgraded["outputs"]["voice"] = False
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


class PresenceService:
    """Own Presence transitions and dedicated Wake event generation inside Shana."""

    def __init__(
        self,
        *,
        conversation: ConversationService | None = None,
        memory: MemoryService | None = None,
        bus: PerformerEventBus | None = None,
        dispatcher: StreamOutputDispatcher | None = None,
        identity: IdentityResolver | None = None,
    ) -> None:
        self._conversation = conversation or ConversationService()
        self._memory = memory or MemoryService()
        self._identity = identity or IdentityResolver()
        self._bus = bus or get_performer_event_bus()
        self._dispatcher = dispatcher or StreamOutputDispatcher()

    def status(self) -> dict[str, Any]:
        state = load_presence_state(downgrade_stale_live=True)
        save_presence_state(state)
        return {"ok": True, "state": state, "performer": self._bus.stats()}

    def transition(
        self,
        mode: str,
        *,
        audience: AudienceSelection | None = None,
        confirm_public_output: bool = False,
        updated_by: str = "dashboard",
    ) -> dict[str, Any]:
        normalized = normalize_presence_mode(mode)
        if normalized == "go_live" and not confirm_public_output:
            raise ValueError("confirm_public_output is required for Go Live")
        previous = load_presence_state(downgrade_stale_live=True)
        state = presence_state_for_mode(
            normalized,
            previous=previous,
            updated_by=updated_by,
            confirmed_live_at=utc_now() if normalized == "go_live" else None,
        )
        if audience is not None:
            state["audience"] = self._audience_payload(audience)
        if normalized == "go_live":
            state["audience"] = {"kind": "unknown", "known_person_id": None, "display_name": "Public audience"}
        saved = save_presence_state(state)
        return {"ok": True, "state": saved, "detail": self._detail(saved)}

    def wake(self, *, audience: AudienceSelection, session_id: str | None = None) -> dict[str, Any]:
        transition = self.transition("wake", audience=audience, updated_by="presence_wake")
        state = transition["state"]
        if not state.get("wake", {}).get("enabled", True):
            return self._record_wake_result(state, status="suppressed", reason="wake_opening_disabled")

        speaker = self._speaker_for_audience(audience)
        session = str(session_id or f"presence-{datetime.now(timezone.utc).date().isoformat()}")[:120]
        audio_eligible = self._bus.has_eligible_listener(DASHBOARD_MONITOR_TARGET, audio=True)
        response = self._conversation.respond_presence_wake(
            session_id=session,
            speaker=speaker,
            wake_context=self._build_wake_context(state=state, speaker=speaker),
            synthesize_speech=audio_eligible,
        )
        event = StreamInputEvent(
            kind="presence_wake",
            session_id=session,
            actor=StreamActor(
                source="presence",
                platform_id=str(state["audience"].get("known_person_id") or state["audience"].get("kind")),
                display_name=str(state["audience"].get("display_name") or "Unknown"),
                roles=[str(state["audience"].get("kind") or "unknown")],
            ),
            metadata={
                "presence_mode": "wake",
                "privacy_scope": "local_private" if speaker.memory_read_allowed else "local_generic",
                "output_target_policy": DASHBOARD_MONITOR_TARGET,
            },
        )
        turn_id = f"presence-wake-{uuid4().hex}"
        output_events = output_events_from_response(input_event=event, turn_id=turn_id, response=response)
        for output_event in output_events:
            output_event.payload["target_policy"] = DASHBOARD_MONITOR_TARGET
        dispatch = self._dispatcher.dispatch(output_events)
        status = "spoken" if response.audio_path or response.audio_content_type else "text_only"
        self._conversation.record_durable_output(
            target_policy=DASHBOARD_MONITOR_TARGET,
            turn_id=turn_id,
            text=response.spoken_text,
            status="completed",
            spoken=status == "spoken",
        )
        return self._record_wake_result(
            state,
            status=status,
            opening=response.spoken_text,
            event_id=event.event_id,
            turn_id=turn_id,
            session_id=session,
            route_events=response.tts_metadata.get("route_events", []),
            dispatch=dispatch.model_dump(),
            reason=None if audio_eligible else "no_audio_ready_monitor_listener",
        )

    def _speaker_for_audience(self, audience: AudienceSelection) -> SpeakerProfile:
        if audience.kind == "unknown":
            return UNKNOWN_PUBLIC
        if audience.kind == "owner":
            return self._identity.resolve(None)
        person = self._memory.get_known_person(int(audience.known_person_id or 0))
        if person is None:
            raise ValueError("known person not found")
        trust = str(person.get("trust") or "guest")
        if trust not in {"owner", "trusted", "guest", "public"}:
            trust = "guest"
        return SpeakerProfile(
            name=str(person.get("name") or "Known person"),
            trust=trust,  # type: ignore[arg-type]
            notes=str(person.get("notes") or "") if trust in {"owner", "trusted"} else "",
            resolved_via="presence_selection",
            is_owner=trust == "owner",
        )

    def _audience_payload(self, audience: AudienceSelection) -> dict[str, Any]:
        if audience.kind == "unknown":
            return {"kind": "unknown", "known_person_id": None, "display_name": "Unknown"}
        if audience.kind == "owner":
            owner = self._identity.resolve(None)
            return {"kind": "owner", "known_person_id": None, "display_name": owner.name}
        person = self._memory.get_known_person(int(audience.known_person_id or 0))
        if person is None:
            raise ValueError("known person not found")
        return {
            "kind": "known_person",
            "known_person_id": int(person["id"]),
            "display_name": str(person.get("name") or "Known person"),
        }

    def _build_wake_context(self, *, state: dict[str, Any], speaker: SpeakerProfile) -> str:
        lifecycle = dict(state.get("lifecycle") or {})
        recent_openings = list((state.get("wake") or {}).get("recent_openings") or [])[-5:]
        lines = [
            f"Local time: {datetime.now().astimezone().isoformat(timespec='minutes')}",
            "Presence mode: wake",
            "Output target: dashboard_monitor",
            f"Audience: {speaker.name}; trust={speaker.trust}; memory_read_allowed={speaker.memory_read_allowed}",
            f"Last sleep: {lifecycle.get('last_sleep_at') or 'unknown'}",
            f"Last wake: {lifecycle.get('last_wake_at') or 'unknown'}",
            f"Last meaningful interaction: {lifecycle.get('last_interaction_at') or 'unknown'}",
        ]
        if speaker.memory_read_allowed:
            memories = self._memory.search_memories(
                "",
                limit=min(5, settings.memory_top_k),
                subject_type=speaker.subject_type,
                subject_name=speaker.name if speaker.subject_type == "other_person" else None,
            )
            if memories:
                lines.append("Selected relevant memories:")
                lines.extend(f"- {memory.summary[:500]}" for memory in memories)
        if recent_openings:
            lines.append("Recent Wake openings to avoid repeating:")
            lines.extend(f"- {str(item.get('opening') or '')[:300]}" for item in recent_openings)
        lines.append(
            "Privacy: do not reveal owner or known-person facts unless this audience is explicitly permitted to read them."
        )
        return "\n".join(lines)[:12_000]

    def _record_wake_result(self, state: dict[str, Any], *, status: str, **details: Any) -> dict[str, Any]:
        now = utc_now()
        wake = dict(state.get("wake") or {})
        opening = details.get("opening")
        history = list(wake.get("recent_openings") or [])
        if opening:
            history.append({"occurred_at": now, "opening": str(opening)[:1000], "status": status})
        wake.update(
            {
                "last_status": status,
                "last_opening": opening,
                "last_event_at": now,
                "recent_openings": history[-10:],
                "suppression_reason": details.get("reason"),
            }
        )
        state["wake"] = wake
        state["activity"] = {
            **dict(state.get("activity") or {}),
            "last_event_kind": "presence_wake",
            "last_output_target": DASHBOARD_MONITOR_TARGET if status in {"spoken", "text_only"} else None,
            "current_turn_id": details.get("turn_id"),
        }
        saved = save_presence_state(state)
        return {"ok": status != "failed", "state": saved, "wake": {"status": status, **details}}

    @staticmethod
    def _detail(state: dict[str, Any]) -> str:
        mode = str(state.get("mode") or "sleep")
        if mode == "sleep":
            return "Presence set to Sleep. Autonomous and public output are suppressed."
        if mode == "wake":
            return "Presence set to Wake. Local Monitor interaction is enabled."
        if mode == "break":
            return "Presence set to Break. Observation may continue while proactive/public speech is suppressed."
        return "Presence set to Go Live. Public-safe stream output is enabled for this backend session."
