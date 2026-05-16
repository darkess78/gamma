from __future__ import annotations

import unittest

from gamma.voice.idle_policy import LiveIdlePolicy, LiveIdleSettings, LiveIdleState


class LiveIdlePolicyTest(unittest.TestCase):
    def _state(self, **overrides):
        values = {
            "live_session_active": True,
            "turn_open": False,
            "remote_turn_active": False,
            "has_completed_turn": True,
            "silence_seconds": 60.0,
            "seconds_since_last_idle_decision": None,
            "proactive_attempts_for_topic": 0,
            "user_recently_interrupted": False,
        }
        values.update(overrides)
        return LiveIdleState(**values)

    def test_disabled_policy_does_not_emit(self) -> None:
        decision = LiveIdlePolicy(LiveIdleSettings(enabled=False)).evaluate(self._state())

        self.assertFalse(decision.should_emit_event)
        self.assertEqual(decision.reason, "proactive_idle_disabled")

    def test_waits_until_target_silence(self) -> None:
        policy = LiveIdlePolicy(LiveIdleSettings(enabled=True, min_silence_seconds=30, target_silence_seconds=60))

        below_min = policy.evaluate(self._state(silence_seconds=20))
        below_target = policy.evaluate(self._state(silence_seconds=45))

        self.assertFalse(below_min.should_emit_event)
        self.assertEqual(below_min.reason, "below_min_silence")
        self.assertFalse(below_target.should_emit_event)
        self.assertEqual(below_target.reason, "below_target_silence")

    def test_emits_reply_after_target_silence(self) -> None:
        decision = LiveIdlePolicy(
            LiveIdleSettings(enabled=True, min_silence_seconds=30, target_silence_seconds=60)
        ).evaluate(self._state(silence_seconds=75))

        self.assertTrue(decision.should_emit_event)
        self.assertTrue(decision.would_reply)
        self.assertEqual(decision.policy_decision, "reply")

    def test_cooldown_suppresses_repeat_decision(self) -> None:
        decision = LiveIdlePolicy(
            LiveIdleSettings(enabled=True, target_silence_seconds=60, cooldown_seconds=180)
        ).evaluate(self._state(silence_seconds=120, seconds_since_last_idle_decision=90))

        self.assertFalse(decision.should_emit_event)
        self.assertEqual(decision.reason, "cooldown_active")

    def test_attempt_cap_switches_to_topic_shift(self) -> None:
        decision = LiveIdlePolicy(
            LiveIdleSettings(enabled=True, target_silence_seconds=60, max_attempts_per_topic=2)
        ).evaluate(self._state(silence_seconds=120, proactive_attempts_for_topic=2))

        self.assertTrue(decision.should_emit_event)
        self.assertEqual(decision.policy_decision, "topic_shift")


if __name__ == "__main__":
    unittest.main()
