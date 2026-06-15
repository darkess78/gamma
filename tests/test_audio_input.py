from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path

from gamma.voice.audio_input import AudioDecodeError, AudioInputDecoder


class AudioInputDecoderTest(unittest.TestCase):
    def test_native_wav_fallback_normalizes_channels_rate_and_duration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "stereo.wav"
            self._write_wav(path, duration_seconds=1.5, sample_rate=8_000, channels=2)

            audio = AudioInputDecoder(
                ffmpeg_executable="/not/a/real/ffmpeg",
                max_seconds=1,
                timeout_ms=1_000,
            ).decode_path(path)

        self.assertEqual(audio.decoder, "wave")
        self.assertEqual(audio.sample_rate, 16_000)
        self.assertGreaterEqual(len(audio.samples), 15_990)
        self.assertLessEqual(len(audio.samples), 16_010)
        self.assertAlmostEqual(audio.duration_seconds, 1.0, places=2)
        self.assertLessEqual(max(abs(sample) for sample in audio.samples), 1.0)

    def test_missing_file_raises_decode_error(self) -> None:
        with self.assertRaises(AudioDecodeError):
            AudioInputDecoder().decode_path("/tmp/not-a-real-gamma-audio-file.webm")

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is required for compressed-audio coverage")
    def test_decodes_webm_to_mono_16khz_pcm(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            wav_path = temp_path / "source.wav"
            webm_path = temp_path / "source.webm"
            self._write_wav(wav_path, duration_seconds=0.5, sample_rate=16_000, channels=1)
            subprocess.run(
                [
                    shutil.which("ffmpeg") or "ffmpeg",
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(wav_path),
                    "-c:a",
                    "libopus",
                    str(webm_path),
                ],
                check=True,
            )

            audio = AudioInputDecoder(timeout_ms=2_000).decode_path(webm_path)

        self.assertEqual(audio.decoder, "ffmpeg")
        self.assertEqual(audio.sample_rate, 16_000)
        self.assertGreater(len(audio.samples), 7_000)
        self.assertLess(len(audio.samples), 9_000)

    @staticmethod
    def _write_wav(path: Path, *, duration_seconds: float, sample_rate: int, channels: int) -> None:
        frames = bytearray()
        for index in range(int(sample_rate * duration_seconds)):
            value = int(math.sin(2 * math.pi * 220 * index / sample_rate) * 0.3 * 32767)
            encoded = value.to_bytes(2, byteorder="little", signed=True)
            frames.extend(encoded * channels)
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(bytes(frames))


if __name__ == "__main__":
    unittest.main()
