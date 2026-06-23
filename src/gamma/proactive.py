from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, time, timezone
from typing import Any

from .config import settings
from .performer.bus import PerformerEventBus, get_performer_event_bus
from .presence import apply_presence_to_stream_event, load_presence_state, parse_utc, save_presence_state, utc_now
from .stream.brain import StreamBrain
from .stream.models import StreamActor, StreamInputEvent


class ProactiveScheduler:
    """Shana-owned bounded scheduler with deterministic pre-generation gates."""

    def __init__(
        self,
        *,
        stream_brain: StreamBrain,
        bus: PerformerEventBus | None = None,
        resource_ready: Callable[[], bool] | None = None,
    ) -> None:
        self._stream_brain = stream_brain
        self._bus = bus or get_performer_event_bus()
        self._resource_ready = resource_ready or (lambda: True)
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self.run(), name="gamma-proactive-scheduler")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def run(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(max(1, settings.proactive_idle_tick_seconds))
            try:
                await asyncio.to_thread(self.evaluate_once)
            except Exception as exc:
                self._record_status("error", f"scheduler_error:{exc}")

    def evaluate_once(self, *, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        state = load_presence_state(downgrade_stale_live=True)
        mode = str(state.get("mode") or "sleep")
        scheduler_state = dict((state.get("autonomy") or {}).get("scheduler") or {})
        reason = self._suppression_reason(state=state, scheduler_state=scheduler_state, now=now)
        if reason:
            return self._record_status("suppressed", reason, state=state, now=now)

        target = "stream_public" if mode == "go_live" else "dashboard_monitor"
        session_id = str((state.get("activity") or {}).get("session_id") or "presence-local")
        public = mode == "go_live"
        event = StreamInputEvent(
            kind="conversation_lull",
            text=(
                "Offer one brief public-safe observation or question for the stream."
                if public
                else "Offer one brief privacy-safe local observation or question to continue the session naturally."
            ),
            session_id=session_id,
            actor=StreamActor(
                source="presence_scheduler",
                platform_id="public" if public else "unknown",
                display_name="Shana proactive scheduler",
                roles=["runtime"],
            ),
            metadata={
                "scheduler_authorized": True,
                "idle_policy_decision": "check_in",
                "idle_policy_reason": "bounded_scheduler_eligible",
                "presence_mode": mode,
                "output_target_policy": target,
                "privacy_scope": "public" if public else "local_generic",
                "public_output": public,
                "tools_allowed": False,
                "twitch_controls": {
                    "dry_run": False,
                    "voice_enabled": bool(settings.proactive_idle_speech_enabled),
                    "subtitles_enabled": True,
                    "llm_safety_review_enabled": True,
                    "self_goal_proposals_enabled": False,
                },
            },
        )
        event, synthesize = apply_presence_to_stream_event(
            event,
            synthesize_speech=bool(settings.proactive_idle_speech_enabled),
            state=state,
        )
        result = self._stream_brain.handle_event(
            event,
            synthesize_speech=synthesize,
            fast_mode=False,
            brief_mode=True,
        )
        scheduler_state["last_emitted_at"] = now.isoformat().replace("+00:00", "Z")
        scheduler_state["attempts_for_topic"] = int(scheduler_state.get("attempts_for_topic") or 0) + 1
        state.setdefault("autonomy", {})["scheduler"] = scheduler_state
        state.setdefault("activity", {})["last_autonomous_action"] = {
            "occurred_at": scheduler_state["last_emitted_at"],
            "event_id": event.event_id,
            "trace_id": result.trace_id,
            "target": target,
        }
        saved = save_presence_state(state)
        return self._record_status(
            "emitted",
            "bounded_scheduler_eligible",
            state=saved,
            now=now,
            extra={"event_id": event.event_id, "trace_id": result.trace_id, "target": target},
        )

    def _suppression_reason(self, *, state: dict[str, Any], scheduler_state: dict[str, Any], now: datetime) -> str | None:
        if not settings.proactive_idle_enabled:
            return "proactive_idle_disabled"
        mode = str(state.get("mode") or "sleep")
        if mode == "sleep":
            return "presence_sleep"
        if mode == "break":
            return "presence_break"
        if mode not in {"wake", "go_live"}:
            return "presence_mode_not_eligible"
        if self._in_quiet_hours(now.astimezone()):
            return "quiet_hours"
        if not self._resource_ready():
            return "resources_unavailable"
        target = "stream_public" if mode == "go_live" else "dashboard_monitor"
        stats = self._bus.stats()
        if target in set(stats.get("muted_targets") or []):
            return "operator_target_muted"
        if not self._bus.has_eligible_listener(target, audio=bool(settings.proactive_idle_speech_enabled)):
            return "no_eligible_output_listener"
        active_statuses = {"queued", "generating", "synthesizing", "speaking"}
        if any(str(turn.get("status") or "") in active_statuses for turn in self._bus.recent_turns(limit=5)):
            return "assistant_turn_active"
        recent_turns = self._bus.recent_turns(limit=5)
        if recent_turns and str(recent_turns[-1].get("status") or "") == "interrupted":
            return "recent_interrupt"
        last_activity = parse_utc((state.get("lifecycle") or {}).get("last_interaction_at"))
        if last_activity is None:
            last_activity = parse_utc((state.get("wake") or {}).get("last_event_at"))
        if last_activity is None:
            return "no_completed_turn_context"
        silence = max(0.0, (now - last_activity).total_seconds())
        if silence < max(settings.proactive_idle_min_silence_seconds, settings.proactive_idle_target_silence_seconds):
            return "below_target_silence"
        last_emitted = parse_utc(scheduler_state.get("last_emitted_at"))
        if last_emitted is not None and (now - last_emitted).total_seconds() < settings.proactive_idle_cooldown_seconds:
            return "cooldown_active"
        if int(scheduler_state.get("attempts_for_topic") or 0) >= settings.proactive_idle_max_attempts_per_topic:
            return "topic_attempt_cap_reached"
        return None

    def _in_quiet_hours(self, local_now: datetime) -> bool:
        start = self._parse_clock(settings.proactive_quiet_hours_start)
        end = self._parse_clock(settings.proactive_quiet_hours_end)
        if start is None or end is None or start == end:
            return False
        current = local_now.time().replace(tzinfo=None)
        if start < end:
            return start <= current < end
        return current >= start or current < end

    @staticmethod
    def _parse_clock(value: str) -> time | None:
        try:
            hour, minute = str(value or "").split(":", 1)
            return time(hour=int(hour), minute=int(minute))
        except (TypeError, ValueError):
            return None

    def _record_status(
        self,
        status: str,
        reason: str,
        *,
        state: dict[str, Any] | None = None,
        now: datetime | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = state or load_presence_state(downgrade_stale_live=True)
        timestamp = (now or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")
        scheduler = dict((state.get("autonomy") or {}).get("scheduler") or {})
        scheduler.update(
            {
                "status": status,
                "reason": reason,
                "last_checked_at": timestamp,
                "next_check_at": datetime.fromtimestamp(
                    (now or datetime.now(timezone.utc)).timestamp() + max(1, settings.proactive_idle_tick_seconds),
                    tz=timezone.utc,
                ).isoformat().replace("+00:00", "Z"),
            }
        )
        scheduler.update(extra or {})
        state.setdefault("autonomy", {})["scheduler"] = scheduler
        save_presence_state(state)
        return {"status": status, "reason": reason, **(extra or {})}
