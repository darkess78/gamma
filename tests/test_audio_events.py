from __future__ import annotations

import unittest

from gamma.schemas.voice import AudioEvent
from gamma.voice.audio_events import AudioEventPolicy, normalize_audio_event_label, postprocess_audio_events


class AudioEventPostprocessingTest(unittest.TestCase):
    def test_normalizes_known_provider_labels(self) -> None:
        self.assertEqual(normalize_audio_event_label("Applause"), "clapping")
        self.assertEqual(normalize_audio_event_label("throat-clearing"), "throat_clearing")
        self.assertIsNone(normalize_audio_event_label("engine"))

    def test_filters_merges_and_applies_cooldown(self) -> None:
        events = [
            AudioEvent(label="Laughing", confidence=0.81, start_ms=0, end_ms=300, source="test"),
            AudioEvent(label="laughter", confidence=0.92, start_ms=350, end_ms=700, source="test"),
            AudioEvent(label="cough", confidence=0.95, start_ms=800, end_ms=850, source="test"),
            AudioEvent(label="applause", confidence=0.88, start_ms=1_000, end_ms=1_500, source="test"),
            AudioEvent(label="clap", confidence=0.90, start_ms=1_900, end_ms=2_200, source="test"),
            AudioEvent(label="alarm", confidence=0.99, start_ms=2_500, end_ms=3_000, source="test"),
        ]
        policy = AudioEventPolicy(
            allowed_labels=frozenset({"laughter", "cough", "clapping"}),
            min_confidence=0.7,
            min_duration_ms=100,
            merge_gap_ms=100,
            cooldown_ms=500,
        )

        result = postprocess_audio_events(events, policy=policy)

        self.assertEqual([event.label for event in result], ["laughter", "clapping"])
        self.assertEqual(result[0].start_ms, 0.0)
        self.assertEqual(result[0].end_ms, 700.0)
        self.assertEqual(result[0].confidence, 0.92)


if __name__ == "__main__":
    unittest.main()
