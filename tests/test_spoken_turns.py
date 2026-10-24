from __future__ import annotations

import unittest

from gamma.performer.turns import SpokenTurnStore
from gamma.performer.models import PerformerOutputEvent


class SpokenTurnStoreTest(unittest.TestCase):
    def test_spoken_turn_store_tracks_state_and_context(self) -> None:
        store = SpokenTurnStore()

        turn = store.upsert(
            "turn-1",
            status="generating",
            target_policy="stream_public",
            source="stream_output",
            input={"kind": "chat_message"},
            actor={"source": "twitch", "platform_id": "u1"},
        )
        speaking = store.transition("turn-1", "speaking", generated_text="Hello.", subtitle="Hello.", chunk_count=1)

        self.assertEqual(turn.status, "generating")
        self.assertEqual(speaking.status, "speaking")
        self.assertEqual(speaking.actor["source"], "twitch")
        self.assertEqual(store.get("turn-1").generated_text, "Hello.")  # type: ignore[union-attr]

    def test_spoken_turn_store_keeps_recent_limit(self) -> None:
        store = SpokenTurnStore(history_limit=2)
        store.upsert("turn-1")
        store.upsert("turn-2")
        store.upsert("turn-3")

        self.assertIsNone(store.get("turn-1"))
        self.assertEqual([turn.turn_id for turn in store.recent(limit=10)], ["turn-2", "turn-3"])

    def test_spoken_turn_store_applies_performer_events(self) -> None:
        store = SpokenTurnStore()

        store.apply_event(
            PerformerOutputEvent(
                type="subtitle_update",
                turn_id="turn-1",
                source="stream_output",
                target_policy="stream_public",
                payload={"text": "Hello.", "actor": {"source": "twitch"}, "input": {"kind": "chat_message"}},
            )
        )
        store.apply_event(PerformerOutputEvent(type="speech_chunk_ready", turn_id="turn-1", payload={"chunk_index": 2}))
        turn = store.apply_event(PerformerOutputEvent(type="speech_ended", turn_id="turn-1"))

        self.assertEqual(turn.status, "ready")
        self.assertEqual(turn.subtitle, "Hello.")
        self.assertEqual(turn.chunk_count, 2)
        self.assertEqual(turn.actor["source"], "twitch")

    def test_producer_events_cannot_claim_playback(self) -> None:
        cases = [
            ("turn_started", {"status": "speaking"}, "synthesizing"),
            ("turn_state_changed", {"state": "speaking"}, "synthesizing"),
            ("turn_state_changed", {"state": "completed"}, "ready"),
            ("turn_started", {"status": "completed"}, "ready"),
            ("speech_started", {}, "ready"),
            ("speech_chunk_ready", {"chunk_index": 1}, "ready"),
            ("speech_ended", {}, "ready"),
        ]
        for event_type, payload, expected in cases:
            with self.subTest(event_type=event_type, payload=payload):
                store = SpokenTurnStore()
                turn = store.apply_event(PerformerOutputEvent(
                    type=event_type, turn_id="no-listener", payload=payload))
                self.assertEqual(turn.status, expected)

    def test_late_producer_events_do_not_resurrect_cleared_turn(self) -> None:
        for terminal in ("cancelled", "interrupted", "failed"):
            with self.subTest(terminal=terminal):
                store = SpokenTurnStore()
                store.apply_event(PerformerOutputEvent(type="output_cleared", turn_id="turn",
                    payload={"status": terminal, "reason": "operator_stop"}))
                for event_type, payload in [
                    ("turn_started", {"status": "generating"}),
                    ("turn_state_changed", {"state": "speaking"}),
                    ("speech_chunk_ready", {"chunk_index": 2}),
                    ("speech_ended", {}),
                ]:
                    turn = store.apply_event(PerformerOutputEvent(
                        type=event_type, turn_id="turn", payload=payload))
                    self.assertEqual(turn.status, terminal)
                    self.assertEqual(turn.cancellation_reason, "operator_stop")
                self.assertEqual(turn.chunk_count, 2)


if __name__ == "__main__":
    unittest.main()
