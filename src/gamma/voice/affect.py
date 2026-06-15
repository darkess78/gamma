from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .audio_input import AudioInputDecoder, NormalizedAudio


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

    def __init__(self, decoder: AudioInputDecoder | None = None) -> None:
        self._decoder = decoder or AudioInputDecoder()

    def analyze_path(self, path: Path | str, *, transcript: str = "") -> VoiceAffectResult:
        try:
            audio = self._decoder.decode_path(path)
        except Exception as exc:
            return VoiceAffectResult(
                ok=False,
                features={},
                labels={"energy": "unknown", "pace": "unknown", "delivery": "unknown"},
                detail=f"voice affect unavailable: {exc}",
            )
        return self.analyze_audio(audio, transcript=transcript)

    def analyze_audio(self, audio: NormalizedAudio, *, transcript: str = "") -> VoiceAffectResult:
        samples = audio.samples
        sample_rate = audio.sample_rate
        if not samples or sample_rate <= 0:
            return VoiceAffectResult(
                ok=False,
                features={},
                labels={"energy": "unknown", "pace": "unknown", "delivery": "unknown"},
                detail="voice affect unavailable: empty audio",
            )

        duration_seconds = len(samples) / float(sample_rate)
        rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
        peak = max(abs(sample) for sample in samples)
        rms_dbfs = self._dbfs(rms)
        peak_dbfs = self._dbfs(float(peak))
        zero_crossing_rate = self._zero_crossing_rate(samples)
        silence_ratio = self._silence_ratio(samples, sample_rate=sample_rate)
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
                "sample_rate": sample_rate,
                "decoder": audio.decoder,
            },
            labels={
                "energy": energy,
                "pace": pace,
                "delivery": delivery,
                "confidence": 0.35,
                "source": "signal_features",
            },
        )

    def _dbfs(self, value: float) -> float:
        if value <= 0:
            return -96.0
        return max(-96.0, 20.0 * math.log10(value))

    def _zero_crossing_rate(self, samples: tuple[float, ...]) -> float:
        if len(samples) < 2:
            return 0.0
        crossings = 0
        last = samples[0]
        for sample in samples[1:]:
            if (last < 0 <= sample) or (last >= 0 > sample):
                crossings += 1
            last = sample
        return crossings / float(len(samples) - 1)

    def _silence_ratio(self, samples: tuple[float, ...], *, sample_rate: int) -> float:
        window_size = max(1, int(sample_rate * self.window_ms / 1000.0))
        threshold = 0.015
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
