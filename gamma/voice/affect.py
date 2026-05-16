from __future__ import annotations

import math
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class VoiceAffectResult:
    ok: bool
    features: dict[str, Any]
    labels: dict[str, Any]
    detail: str | None = None

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": self.ok,
            "features": self.features,
            "labels": self.labels,
        }
        if self.detail:
            payload["detail"] = self.detail
        return payload


class VoiceAffectAnalyzer:
    """Lightweight prosody features for live voice context.

    This intentionally avoids claiming reliable emotion recognition. The output
    is a low-confidence signal layer that can help later turn policy and prompts
    understand energy, pace, and hesitation around a transcript.
    """

    window_ms = 40

    def analyze_path(self, path: Path | str, *, transcript: str = "") -> VoiceAffectResult:
        try:
            with wave.open(str(path), "rb") as wav_file:
                channels = wav_file.getnchannels()
                sample_width = wav_file.getsampwidth()
                sample_rate = wav_file.getframerate()
                frame_count = wav_file.getnframes()
                raw = wav_file.readframes(frame_count)
        except Exception as exc:
            return VoiceAffectResult(
                ok=False,
                features={},
                labels={"energy": "unknown", "pace": "unknown", "delivery": "unknown"},
                detail=f"voice affect unavailable: {exc}",
            )

        samples = self._pcm_samples(raw, sample_width=sample_width, channels=channels)
        if not samples or sample_rate <= 0:
            return VoiceAffectResult(
                ok=False,
                features={},
                labels={"energy": "unknown", "pace": "unknown", "delivery": "unknown"},
                detail="voice affect unavailable: empty audio",
            )

        duration_seconds = len(samples) / float(sample_rate)
        max_abs = float((2 ** (8 * sample_width - 1)) - 1) if sample_width > 1 else 128.0
        rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
        peak = max(abs(sample) for sample in samples)
        rms_dbfs = self._dbfs(rms, max_abs)
        peak_dbfs = self._dbfs(float(peak), max_abs)
        zero_crossing_rate = self._zero_crossing_rate(samples)
        silence_ratio = self._silence_ratio(samples, sample_rate=sample_rate, max_abs=max_abs)
        word_count = len([word for word in transcript.split() if word.strip()])
        speaking_rate_wpm = (word_count / duration_seconds * 60.0) if duration_seconds > 0 and word_count else None
        energy = self._energy_label(rms_dbfs)
        pace = self._pace_label(speaking_rate_wpm)
        delivery = self._delivery_label(energy=energy, pace=pace, silence_ratio=silence_ratio)

        return VoiceAffectResult(
            ok=True,
            features={
                "duration_ms": round(duration_seconds * 1000.0, 1),
                "rms_dbfs": round(rms_dbfs, 1),
                "peak_dbfs": round(peak_dbfs, 1),
                "zero_crossing_rate": round(zero_crossing_rate, 4),
                "silence_ratio": round(silence_ratio, 3),
                "word_count": word_count,
                "speaking_rate_wpm": round(speaking_rate_wpm, 1) if speaking_rate_wpm is not None else None,
            },
            labels={
                "energy": energy,
                "pace": pace,
                "delivery": delivery,
                "confidence": 0.35,
                "source": "signal_features",
            },
        )

    def _pcm_samples(self, raw: bytes, *, sample_width: int, channels: int) -> list[int]:
        if sample_width not in {1, 2, 4} or channels <= 0:
            return []
        frame_width = sample_width * channels
        samples: list[int] = []
        for offset in range(0, len(raw) - frame_width + 1, frame_width):
            channel_values = []
            for channel in range(channels):
                start = offset + channel * sample_width
                chunk = raw[start:start + sample_width]
                if sample_width == 1:
                    value = int(chunk[0]) - 128
                else:
                    value = int.from_bytes(chunk, byteorder="little", signed=True)
                channel_values.append(value)
            samples.append(round(sum(channel_values) / len(channel_values)))
        return samples

    def _dbfs(self, value: float, max_abs: float) -> float:
        if value <= 0 or max_abs <= 0:
            return -96.0
        return max(-96.0, 20.0 * math.log10(value / max_abs))

    def _zero_crossing_rate(self, samples: list[int]) -> float:
        if len(samples) < 2:
            return 0.0
        crossings = 0
        last = samples[0]
        for sample in samples[1:]:
            if (last < 0 <= sample) or (last >= 0 > sample):
                crossings += 1
            last = sample
        return crossings / float(len(samples) - 1)

    def _silence_ratio(self, samples: list[int], *, sample_rate: int, max_abs: float) -> float:
        window_size = max(1, int(sample_rate * self.window_ms / 1000.0))
        threshold = max_abs * 0.015
        if not samples:
            return 1.0
        silent = 0
        total = 0
        for start in range(0, len(samples), window_size):
            window = samples[start:start + window_size]
            if not window:
                continue
            total += 1
            rms = math.sqrt(sum(sample * sample for sample in window) / len(window))
            if rms < threshold:
                silent += 1
        return silent / float(total or 1)

    def _energy_label(self, rms_dbfs: float) -> str:
        if rms_dbfs <= -38:
            return "low"
        if rms_dbfs >= -20:
            return "high"
        return "medium"

    def _pace_label(self, speaking_rate_wpm: float | None) -> str:
        if speaking_rate_wpm is None:
            return "unknown"
        if speaking_rate_wpm < 95:
            return "slow"
        if speaking_rate_wpm > 175:
            return "fast"
        return "medium"

    def _delivery_label(self, *, energy: str, pace: str, silence_ratio: float) -> str:
        if silence_ratio > 0.45:
            return "hesitant_or_paused"
        if energy == "low" and pace == "slow":
            return "soft_slow"
        if energy == "high" and pace == "fast":
            return "energetic_fast"
        if energy == "high":
            return "emphatic"
        return "neutral"
