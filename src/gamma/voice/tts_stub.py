"""Stub TTS backend that writes a short tone, for tests and GPU-free runs."""
from __future__ import annotations

import time
import wave
from math import pi, sin
from pathlib import Path
from struct import pack

from .tts_base import BaseFileTTSBackend, TTSResult


class StubTTSBackend(BaseFileTTSBackend):
    """Stub TTS backend for testing.
    
    Writes simple tone waveform; reads from sidecar .txt for text.
    
    Attributes:
        provider_name: 'stub'.
    """

    provider_name = "stub"

    def synthesize(self, text: str, emotion: str | None = None) -> TTSResult:
        """Generate simple tone wave.
        
        Args:
            text: Text to synthesize.
            emotion: Optional emotion style (ignored for stub).
            
        Returns:
            TTSResult: Simple tone audio.
        """
        started_at = time.perf_counter()
        path = self._target_path(".wav")
        self._write_tone_wave(path, duration=max(0.18 * max(len(text.split()), 1), 0.35))
        text_path = path.with_suffix(".txt")
        text_path.write_text(text + "\n", encoding="utf-8")
        return TTSResult(
            provider=self.provider_name,
            text=text,
            audio_path=str(path),
            content_type="audio/wav",
            metadata={"emotion": emotion, "timings_ms": {"backend_ms": round((time.perf_counter() - started_at) * 1000, 1)}},
        )

    def _write_tone_wave(self, path: Path, duration: float, sample_rate: int = 16_000) -> None:
        """Write simple tone WAV file.
        
        Args:
            path: Output path.
            duration: Duration in seconds.
            sample_rate: WAV sample rate (default 16kHz).
        """
        frame_count = int(duration * sample_rate)
        amplitude = 10_000
        frequency = 440.0
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            for i in range(frame_count):
                sample = int(amplitude * sin(2.0 * pi * frequency * (i / sample_rate)))
                wav_file.writeframes(pack("<h", sample))
