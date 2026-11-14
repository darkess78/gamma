"""Stream speech pacing: minimum gaps, spam-quip cooldown, per-minute speech budget and the pending slot.

The pacer is shared by every caller of ``StreamBrain.handle_event`` (the HTTP
sync path, the chat-window picker thread, the proactive scheduler), so every
public method takes ``self._lock``.

Speech timing: ``apply`` reserves the speaking slot when it allows a turn (so
two concurrent turns cannot both pass the gap check), and the brain confirms
the reservation with ``confirm_spoken`` once the turn's speech output is
actually dispatched, or releases it with ``release`` when the turn ended
without speaking. The gap is therefore measured from dispatch, and
``speaking_until`` (dispatch + estimated speech length) lets the chat picker
wait for the current line to finish. Real playback-finished events from the
TTS/performer side are not wired in yet; the estimate stands in for them.
"""
from __future__ import annotations

import threading
import time

from .event_rules import _decision_would_speak, _estimate_speech_seconds, _metadata_without_canned_response, _twitch_control_enabled, _utc_now
from .models import StreamInputEvent, TurnDecision


DEFAULT_STREAM_SPEECH_GAP_SECONDS = 5.0


DEFAULT_SPAM_QUIP_COOLDOWN_SECONDS = 60.0


DEFAULT_MAX_SPEECH_SECONDS_PER_MINUTE = 20.0


HIGH_PRIORITY_BYPASS_THRESHOLD = 20


class StreamSpeechPacer:
    def __init__(
        self,
        *,
        now=time.monotonic,
        default_min_gap_seconds: float = DEFAULT_STREAM_SPEECH_GAP_SECONDS,
        default_spam_quip_cooldown_seconds: float = DEFAULT_SPAM_QUIP_COOLDOWN_SECONDS,
        default_max_speech_seconds_per_minute: float = DEFAULT_MAX_SPEECH_SECONDS_PER_MINUTE,
    ) -> None:
        self._now = now
        self._default_min_gap_seconds = default_min_gap_seconds
        self._default_spam_quip_cooldown_seconds = default_spam_quip_cooldown_seconds
        self._default_max_speech_seconds_per_minute = default_max_speech_seconds_per_minute
        self._last_spoken_at: float | None = None
        self._last_spam_quip_at: float | None = None
        self._pending: dict[str, dict] = {}
        # slot -> {"event", "queued_at" (pacer clock), "options"}; lets the chat picker replay deferred turns.
        self._pending_events: dict[str, dict] = {}
        self._speech_budget_events: list[tuple[float, float]] = []
        self._speaking_until: float | None = None
        # event_id -> (last_spoken_at before the reservation, reservation time)
        self._reservations: dict[str, tuple[float | None, float]] = {}
        self._lock = threading.RLock()

    def apply(self, event: StreamInputEvent, decision: TurnDecision) -> TurnDecision:
        with self._lock:
            return self._apply(event, decision)

    def _apply(self, event: StreamInputEvent, decision: TurnDecision) -> TurnDecision:
        if not _decision_would_speak(decision):
            return decision
        if not _is_paced_stream_event(event):
            self._reserve(event, self._now())
            return decision
        now = self._now()
        spam_cooldown_decision = self._spam_quip_cooldown_decision(event, decision, now)
        if spam_cooldown_decision is not None:
            return spam_cooldown_decision
        min_gap = _stream_min_gap_seconds(event, self._default_min_gap_seconds)
        if self._last_spoken_at is not None:
            elapsed = now - self._last_spoken_at
            if elapsed < min_gap and event.priority < HIGH_PRIORITY_BYPASS_THRESHOLD:
                pending = self._store_pending(event=event, decision=decision, min_gap=min_gap, elapsed=elapsed)
                return TurnDecision(
                    decision="defer",
                    reason="stream_speech_pacing_deferred",
                    should_call_conversation=False,
                    response_mode=decision.response_mode,
                    requires_moderation_review=decision.requires_moderation_review,
                    metadata={
                        **decision.metadata,
                        "would_decision": decision.decision,
                        "would_reason": decision.reason,
                        "min_gap_seconds": min_gap,
                        "elapsed_seconds": round(elapsed, 3),
                        "priority": event.priority,
                        "pending_slot": pending["slot"],
                        "replaced_event_id": pending.get("replaced_event_id"),
                    },
                )
        self._reserve(event, now)
        if decision.response_mode == "spam_quip":
            self._last_spam_quip_at = now
        return decision

    def apply_budget(self, event: StreamInputEvent, decision: TurnDecision, spoken_text: str) -> TurnDecision | None:
        with self._lock:
            return self._apply_budget(event, decision, spoken_text)

    def _apply_budget(self, event: StreamInputEvent, decision: TurnDecision, spoken_text: str) -> TurnDecision | None:
        if not _decision_would_speak(decision) or not _is_paced_stream_event(event):
            return None
        if not _twitch_control_enabled(event, "voice_enabled", True):
            return None
        max_seconds = _stream_max_speech_seconds_per_minute(event, self._default_max_speech_seconds_per_minute)
        if max_seconds <= 0:
            estimate = _estimate_speech_seconds(spoken_text)
            pending = self._store_pending(
                event=event,
                decision=decision,
                min_gap=0.0,
                elapsed=0.0,
                budget_seconds=0.0,
                estimated_speech_seconds=estimate,
            )
            return self._budget_deferred_decision(
                decision=decision,
                pending=pending,
                max_seconds=max_seconds,
                used_seconds=0.0,
                estimated_seconds=estimate,
            )
        now = self._now()
        self._prune_speech_budget(now)
        used_seconds = sum(seconds for _timestamp, seconds in self._speech_budget_events)
        estimated_seconds = _estimate_speech_seconds(spoken_text)
        if used_seconds + estimated_seconds > max_seconds:
            pending = self._store_pending(
                event=event,
                decision=decision,
                min_gap=0.0,
                elapsed=0.0,
                budget_seconds=max_seconds,
                budget_used_seconds=used_seconds,
                estimated_speech_seconds=estimated_seconds,
            )
            return self._budget_deferred_decision(
                decision=decision,
                pending=pending,
                max_seconds=max_seconds,
                used_seconds=used_seconds,
                estimated_seconds=estimated_seconds,
            )
        self._speech_budget_events.append((now, estimated_seconds))
        return None

    def budget_exhausted(self, event: StreamInputEvent, decision: TurnDecision) -> TurnDecision | None:
        """Defer before the model call when the per-minute speech budget is already used up."""
        if not _decision_would_speak(decision) or not _is_paced_stream_event(event):
            return None
        if not _twitch_control_enabled(event, "voice_enabled", True):
            return None
        max_seconds = _stream_max_speech_seconds_per_minute(event, self._default_max_speech_seconds_per_minute)
        if max_seconds <= 0:
            return self.apply_budget(event, decision, "")
        with self._lock:
            return self._budget_exhausted(event, decision, max_seconds)

    def _budget_exhausted(self, event: StreamInputEvent, decision: TurnDecision, max_seconds: float) -> TurnDecision | None:
        now = self._now()
        self._prune_speech_budget(now)
        used_seconds = sum(seconds for _timestamp, seconds in self._speech_budget_events)
        if used_seconds < max_seconds:
            return None
        pending = self._store_pending(
            event=event, decision=decision, min_gap=0.0, elapsed=0.0, budget_seconds=max_seconds,
            budget_used_seconds=used_seconds, estimated_speech_seconds=0.0,
        )
        return self._budget_deferred_decision(
            decision=decision, pending=pending, max_seconds=max_seconds, used_seconds=used_seconds,
            estimated_seconds=0.0,
        )

    def mark_spoken(self, now: float | None = None, *, speech_seconds: float = 0.0) -> None:
        with self._lock:
            stamp = self._now() if now is None else now
            self._last_spoken_at = stamp
            if speech_seconds > 0:
                until = stamp + speech_seconds
                self._speaking_until = until if self._speaking_until is None else max(self._speaking_until, until)

    def confirm_spoken(self, event_id: str, *, speech_seconds: float = 0.0) -> None:
        """The turn's speech output was dispatched: start the gap from now."""
        with self._lock:
            self._reservations.pop(event_id, None)
            self.mark_spoken(speech_seconds=speech_seconds)

    def release(self, event_id: str) -> None:
        """The turn ended without speaking: undo its reservation if nobody spoke since."""
        with self._lock:
            reservation = self._reservations.pop(event_id, None)
            if reservation is None:
                return
            previous, reserved_at = reservation
            if self._last_spoken_at == reserved_at and not any(
                stamp == reserved_at for _prev, stamp in self._reservations.values()
            ):
                self._last_spoken_at = previous

    def ready_in(self, event: StreamInputEvent) -> float:
        """Seconds until a paced turn for ``event`` could speak (0.0 = now).

        Covers the minimum gap, the line still being spoken and a spent
        per-minute budget. Events that bypass pacing are always ready.
        """
        with self._lock:
            if not _is_paced_stream_event(event):
                return 0.0
            now = self._now()
            waits = [0.0]
            if self._speaking_until is not None:
                waits.append(self._speaking_until - now)
            if event.priority < HIGH_PRIORITY_BYPASS_THRESHOLD and self._last_spoken_at is not None:
                min_gap = _stream_min_gap_seconds(event, self._default_min_gap_seconds)
                waits.append(min_gap - (now - self._last_spoken_at))
            if _twitch_control_enabled(event, "voice_enabled", True):
                max_seconds = _stream_max_speech_seconds_per_minute(event, self._default_max_speech_seconds_per_minute)
                self._prune_speech_budget(now)
                used = sum(seconds for _timestamp, seconds in self._speech_budget_events)
                if max_seconds > 0 and used >= max_seconds and self._speech_budget_events:
                    oldest = min(timestamp for timestamp, _seconds in self._speech_budget_events)
                    waits.append(oldest + 60.0 - now)
            return round(max(waits), 3)

    def remember_pending_options(self, event_id: str, options: dict) -> None:
        """Record how a deferred turn was requested (speech, fast mode) so it can be replayed."""
        with self._lock:
            for entry in self._pending_events.values():
                if entry["event"].event_id == event_id:
                    entry["options"] = dict(options)

    def take_pending(self) -> list[dict]:
        """Remove and return the deferred events (oldest first) for the chat picker to replay."""
        with self._lock:
            entries = sorted(self._pending_events.values(), key=lambda entry: entry["queued_at"])
            self._pending_events.clear()
            self._pending.clear()
            return [dict(entry) for entry in entries]

    def pending_snapshot(self) -> dict:
        with self._lock:
            return {
                "updated_at": _utc_now(),
                "slots": {
                    slot: dict(item)
                    for slot, item in sorted(self._pending.items())
                },
            }

    def clear_pending(self) -> None:
        with self._lock:
            self._pending.clear()
            self._pending_events.clear()

    def _reserve(self, event: StreamInputEvent, now: float) -> None:
        self._reservations[event.event_id] = (self._last_spoken_at, now)
        self._last_spoken_at = now

    def _store_pending(
        self,
        *,
        event: StreamInputEvent,
        decision: TurnDecision,
        min_gap: float,
        elapsed: float,
        **extra: float,
    ) -> dict:
        slot = _pending_slot(event)
        replaced = self._pending.get(slot)
        item = {
            "slot": slot,
            "queued_at": _utc_now(),
            "event_id": event.event_id,
            "kind": event.kind,
            "text": event.text,
            "priority": event.priority,
            "actor": event.actor.model_dump(),
            "decision": decision.decision,
            "reason": decision.reason,
            "response_mode": decision.response_mode,
            "min_gap_seconds": min_gap,
            "elapsed_seconds": round(elapsed, 3),
        }
        for key, value in extra.items():
            item[key] = round(value, 3)
        if replaced:
            item["replaced_event_id"] = replaced.get("event_id")
        self._pending[slot] = item
        self._pending_events[slot] = {"event": event, "queued_at": self._now(), "options": {}, "slot": slot}
        return item

    def _prune_speech_budget(self, now: float) -> None:
        self._speech_budget_events = [
            (timestamp, seconds)
            for timestamp, seconds in self._speech_budget_events
            if now - timestamp < 60.0
        ]

    def _budget_deferred_decision(
        self,
        *,
        decision: TurnDecision,
        pending: dict,
        max_seconds: float,
        used_seconds: float,
        estimated_seconds: float,
    ) -> TurnDecision:
        return TurnDecision(
            decision="defer",
            reason="stream_speech_budget_deferred",
            should_call_conversation=False,
            response_mode=decision.response_mode,
            requires_moderation_review=decision.requires_moderation_review,
            metadata={
                **decision.metadata,
                "would_decision": decision.decision,
                "would_reason": decision.reason,
                "max_speech_seconds_per_minute": max_seconds,
                "budget_used_seconds": round(used_seconds, 3),
                "estimated_speech_seconds": round(estimated_seconds, 3),
                "pending_slot": pending["slot"],
                "replaced_event_id": pending.get("replaced_event_id"),
            },
        )

    def _spam_quip_cooldown_decision(self, event: StreamInputEvent, decision: TurnDecision, now: float) -> TurnDecision | None:
        if decision.response_mode != "spam_quip":
            return None
        cooldown = _stream_spam_quip_cooldown_seconds(event, self._default_spam_quip_cooldown_seconds)
        if self._last_spam_quip_at is None or cooldown <= 0:
            return None
        elapsed = now - self._last_spam_quip_at
        if elapsed >= cooldown:
            return None
        return TurnDecision(
            decision="ignore",
            reason="twitch_spam_quip_cooldown_active",
            should_call_conversation=False,
            response_mode="none",
            metadata={
                **_metadata_without_canned_response(decision.metadata),
                "would_decision": decision.decision,
                "would_reason": decision.reason,
                "cooldown_seconds": cooldown,
                "elapsed_seconds": round(elapsed, 3),
            },
        )


def _stream_min_gap_seconds(event: StreamInputEvent, default: float) -> float:
    controls = event.metadata.get("twitch_controls")
    if not isinstance(controls, dict):
        return default
    raw_value = controls.get("min_speech_gap_seconds")
    if raw_value is None:
        return default
    try:
        return max(0.0, float(raw_value))
    except (TypeError, ValueError):
        return default


def _stream_spam_quip_cooldown_seconds(event: StreamInputEvent, default: float) -> float:
    controls = event.metadata.get("twitch_controls")
    if not isinstance(controls, dict):
        return default
    raw_value = controls.get("spam_quip_cooldown_seconds")
    if raw_value is None:
        return default
    try:
        return max(0.0, float(raw_value))
    except (TypeError, ValueError):
        return default


def _stream_max_speech_seconds_per_minute(event: StreamInputEvent, default: float) -> float:
    controls = event.metadata.get("twitch_controls")
    if not isinstance(controls, dict):
        return default
    raw_value = controls.get("max_speech_seconds_per_minute")
    if raw_value is None:
        return default
    try:
        return max(0.0, float(raw_value))
    except (TypeError, ValueError):
        return default


def _is_paced_stream_event(event: StreamInputEvent) -> bool:
    if event.actor.source != "twitch":
        return False
    if event.kind in {"chat_message", "follow", "raid", "donation", "bits", "subscription", "redeem"}:
        return True
    return False


def _pending_slot(event: StreamInputEvent) -> str:
    if event.kind in {"follow", "raid", "donation", "bits", "subscription", "redeem"} or event.priority >= 10:
        return "high_priority"
    return "ambient"
