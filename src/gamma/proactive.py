from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from concurrent.futures import Future
from datetime import datetime, time, timezone
from typing import Any

from .config import settings
from .brain.runtime import await_brain_job, get_brain_queue
from .brain.queue import BrainQueueClosed, BrainQueueFull
from .brain.silent import SilentCycles
from .brain.pending import await_owned_future
from .memory.revision_json import StaleRevision
from .performer.bus import PerformerEventBus, get_performer_event_bus
from .presence import apply_presence_to_stream_event, load_presence_state, parse_utc, save_presence_state, utc_now, guard_presence_control
from .stream.brain import StreamBrain
from .stream.models import StreamActor, StreamInputEvent


@dataclass
class PendingOutput:
    future: Future
    finish: Callable


class ProactiveScheduler:
    """Shana-owned bounded scheduler with deterministic pre-generation gates."""

    def __init__(
        self,
        *,
        stream_brain: StreamBrain,
        bus: PerformerEventBus | None = None,
        resource_ready: Callable[[], bool] | None = None,
        silent_prepare: Callable | None = None,
        silent_work=None,
    ) -> None:
        self._stream_brain = stream_brain
        self._bus = bus or get_performer_event_bus()
        self._resource_ready = resource_ready or (lambda: True)
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._epoch = 0
        self.silent = SilentCycles(silent_prepare, lambda: silent_prepare is not None and self._silent_allowed(),
                                   queue=get_brain_queue, work=silent_work)

    def _silent_allowed(self):
        state = load_presence_state(downgrade_stale_live=True)
        return (state.get("mode") in {"wake", "go_live"} and self._resource_ready()
                and not self._in_quiet_hours(datetime.now(timezone.utc).astimezone()))

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._epoch += 1
        self._task = asyncio.create_task(self.run(), name="gamma-proactive-scheduler")

    async def stop(self) -> None:
        self.silent.disable()
        self._stop.set()
        self._epoch += 1
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
                await await_brain_job(self.silent.tick, priority="background")
                pending = await await_brain_job(lambda: self.evaluate_once(defer_delivery=True), priority="background")
                if isinstance(pending, PendingOutput):
                    result = await await_owned_future(pending.future)
                    await await_brain_job(lambda: pending.finish(result), priority="background")
            except StaleRevision:
                continue  # A newer Presence writer owns the current state.
            except (BrainQueueClosed, BrainQueueFull):
                self._record_status("suppressed", "brain_queue_unavailable")
            except Exception:
                self._record_status("error", "scheduler_error")

    def evaluate_once(self, *, now: datetime | None = None, defer_delivery: bool = False) -> dict[str, Any] | PendingOutput:
        now = now or datetime.now(timezone.utc)
        state = load_presence_state(downgrade_stale_live=True)
        mode = str(state.get("mode") or "sleep")
        scheduler_state = dict((state.get("autonomy") or {}).get("scheduler") or {})
        reason = self._suppression_reason(state=state, scheduler_state=scheduler_state, now=now)
        if reason:
            return self._record_status("suppressed", reason, state=state, now=now)

        epoch = self._epoch
        scheduler_state["last_attempt_at"] = now.isoformat().replace("+00:00", "Z")
        scheduler_state["attempts_for_topic"] = int(scheduler_state.get("attempts_for_topic") or 0) + 1
        state.setdefault("autonomy", {})["scheduler"] = scheduler_state
        state = save_presence_state(state)

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
        def delivery_allowed():
            current = load_presence_state(downgrade_stale_live=True)
            return (epoch == self._epoch and not self._stop.is_set() and settings.proactive_idle_enabled
                and all(current.get(key) == state.get(key) for key in ("control_revision", "mode", "audience", "outputs"))
                and (current.get("lifecycle") or {}).get("last_interaction_at") == (state.get("lifecycle") or {}).get("last_interaction_at")
                and target not in set(self._bus.stats().get("muted_targets") or [])
                and self._bus.has_eligible_listener(target, audio=synthesize))

        result = self._stream_brain.handle_event(
            event,
            synthesize_speech=synthesize,
            fast_mode=False,
            brief_mode=True,
            defer_delivery=defer_delivery,
            delivery_allowed=delivery_allowed,
        )
        def finish(delivered):
            if not delivery_allowed():
                return {"status": "suppressed", "reason": "proactive_output_superseded"}
            with guard_presence_control(state) as current:
                if (current.get("lifecycle") or {}).get("last_interaction_at") != (state.get("lifecycle") or {}).get("last_interaction_at"):
                    return {"status": "suppressed", "reason": "proactive_output_superseded"}
                if not delivered.output_events:
                    return self._record_status("suppressed", "no_output", state=current, now=now)
                completed = current.setdefault("autonomy", {}).setdefault("scheduler", {})
                completed["last_emitted_at"] = now.isoformat().replace("+00:00", "Z")
                current.setdefault("activity", {})["last_autonomous_action"] = {
                    "occurred_at": completed["last_emitted_at"], "event_id": event.event_id,
                    "trace_id": delivered.trace_id, "target": target}
                saved = save_presence_state(current)
                return self._record_status("emitted", "bounded_scheduler_eligible", state=saved, now=now,
                    extra={"event_id": event.event_id, "trace_id": delivered.trace_id, "target": target})
        return PendingOutput(result, finish) if defer_delivery else finish(result)

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
        last_emitted = max((value for value in (parse_utc(scheduler_state.get("last_emitted_at")),
            parse_utc(scheduler_state.get("last_attempt_at"))) if value is not None), default=None)
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
        try:
            save_presence_state(state)
        except StaleRevision:
            return {"status": "suppressed", "reason": "presence_changed"}
        return {"status": status, "reason": reason, **(extra or {})}
