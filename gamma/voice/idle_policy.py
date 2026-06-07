from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LiveIdleSettings:
    enabled: bool = False
    min_silence_seconds: float = 30.0
    target_silence_seconds: float = 60.0
    cooldown_seconds: float = 180.0
    max_attempts_per_topic: int = 2
    tick_seconds: float = 5.0
    speech_enabled: bool = False


@dataclass(frozen=True, slots=True)
class LiveIdleState:
    live_session_active: bool
    turn_open: bool
    remote_turn_active: bool
    has_completed_turn: bool
    silence_seconds: float
    seconds_since_last_idle_decision: float | None = None
    proactive_attempts_for_topic: int = 0
    user_recently_interrupted: bool = False


@dataclass(frozen=True, slots=True)
class LiveIdleDecision:
    should_emit_event: bool
    policy_decision: str
    reason: str
    next_check_seconds: float
    would_reply: bool = False


class LiveIdlePolicy:
    def __init__(self, settings: LiveIdleSettings | None = None) -> None:
        self._settings = settings or LiveIdleSettings()

    @property
    def settings(self) -> LiveIdleSettings:
        return self._settings

    def evaluate(self, state: LiveIdleState) -> LiveIdleDecision:
        settings = self._settings
        if not settings.enabled:
            return LiveIdleDecision(False, "ignore", "proactive_idle_disabled", settings.tick_seconds)
        if not state.live_session_active:
            return LiveIdleDecision(False, "ignore", "live_session_not_active", settings.tick_seconds)
        if state.turn_open:
            return LiveIdleDecision(False, "ignore", "user_turn_open", settings.tick_seconds)
        if state.remote_turn_active:
            return LiveIdleDecision(False, "ignore", "assistant_turn_active", settings.tick_seconds)
        if not state.has_completed_turn:
            return LiveIdleDecision(False, "ignore", "no_completed_turn_context", settings.tick_seconds)
        if state.user_recently_interrupted:
            return LiveIdleDecision(False, "defer", "recent_interrupt", max(settings.tick_seconds, 15.0))
        if state.silence_seconds < settings.min_silence_seconds:
            return LiveIdleDecision(False, "ignore", "below_min_silence", settings.tick_seconds)
        if state.silence_seconds < settings.target_silence_seconds:
            return LiveIdleDecision(False, "defer", "below_target_silence", settings.tick_seconds)
        if (
            state.seconds_since_last_idle_decision is not None
            and state.seconds_since_last_idle_decision < settings.cooldown_seconds
        ):
            return LiveIdleDecision(False, "defer", "cooldown_active", settings.tick_seconds)
        if state.proactive_attempts_for_topic >= settings.max_attempts_per_topic:
            return LiveIdleDecision(
                True,
                "topic_shift",
                "topic_attempt_cap_reached",
                settings.cooldown_seconds,
                would_reply=True,
            )
        return LiveIdleDecision(
            True,
            "reply",
            "conversation_lull_after_target_silence",
            settings.cooldown_seconds,
            would_reply=True,
        )
