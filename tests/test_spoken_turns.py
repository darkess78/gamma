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

        self.assertEqual(turn.status, "completed")
        self.assertEqual(turn.subtitle, "Hello.")
        self.assertEqual(turn.chunk_count, 2)
        self.assertEqual(turn.actor["source"], "twitch")


if __name__ == "__main__":
    unittest.main()
