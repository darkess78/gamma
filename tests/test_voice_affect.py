from __future__ import annotations

import math
import tempfile
import unittest
import wave
from pathlib import Path

from gamma.voice.affect import VoiceAffectAnalyzer


class VoiceAffectAnalyzerTest(unittest.TestCase):
    def test_analyzes_basic_wav_features(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "voice.wav"
            self._write_tone(path, duration_seconds=1.0, amplitude=0.35)

            result = VoiceAffectAnalyzer().analyze_path(path, transcript="hello there")

        payload = result.as_payload()
        self.assertTrue(payload["ok"])
        self.assertGreater(payload["features"]["duration_ms"], 900)
        self.assertEqual(payload["features"]["word_count"], 2)
        self.assertIn(payload["labels"]["energy"], {"medium", "high"})
        self.assertEqual(payload["labels"]["source"], "signal_features")

    def test_missing_audio_returns_unavailable_payload(self) -> None:
        result = VoiceAffectAnalyzer().analyze_path("/tmp/not-a-real-voice-file.wav", transcript="hello")

        self.assertFalse(result.ok)
        self.assertEqual(result.labels["energy"], "unknown")

    def _write_tone(self, path: Path, *, duration_seconds: float, amplitude: float) -> None:
        sample_rate = 16_000
        frames = bytearray()
        for index in range(int(sample_rate * duration_seconds)):
            value = int(math.sin(2 * math.pi * 220 * index / sample_rate) * amplitude * 32767)
            frames.extend(value.to_bytes(2, byteorder="little", signed=True))
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(bytes(frames))


if __name__ == "__main__":
    unittest.main()
