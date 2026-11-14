"""Stream speech pacing: minimum gaps, spam-quip cooldown, per-minute speech budget and the pending slot."""
from __future__ import annotations

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
        self._speech_budget_events: list[tuple[float, float]] = []

    def apply(self, event: StreamInputEvent, decision: TurnDecision) -> TurnDecision:
        if not _decision_would_speak(decision):
            return decision
        if not _is_paced_stream_event(event):
            self.mark_spoken()
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
        self.mark_spoken(now)
        if decision.response_mode == "spam_quip":
            self._last_spam_quip_at = now
        return decision

    def apply_budget(self, event: StreamInputEvent, decision: TurnDecision, spoken_text: str) -> TurnDecision | None:
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

    def mark_spoken(self, now: float | None = None) -> None:
        self._last_spoken_at = self._now() if now is None else now

    def pending_snapshot(self) -> dict:
        return {
            "updated_at": _utc_now(),
            "slots": {
                slot: dict(item)
                for slot, item in sorted(self._pending.items())
            },
        }

    def clear_pending(self) -> None:
        self._pending.clear()

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
