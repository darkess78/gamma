from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx

from ..config import settings
from ..errors import ConfigurationError
from ..schemas.voice import AudioEvent, SpeakerAffect, VoiceInputContext
from .affect import VoiceAffectAnalyzer, VoiceAffectResult
from .audio_events import AudioEventPolicy, normalize_audio_event_label, postprocess_audio_events
from .audio_input import AudioInputDecoder, NormalizedAudio


class SpeakerEmotionBackend:
    provider_name = "unknown"

    def analyze(
        self,
        audio: NormalizedAudio,
        *,
        transcript: str,
        prosody: VoiceAffectResult,
    ) -> SpeakerAffect | None:
        raise NotImplementedError


class DisabledSpeakerEmotionBackend(SpeakerEmotionBackend):
    provider_name = "disabled"

    def analyze(
        self,
        audio: NormalizedAudio,
        *,
        transcript: str,
        prosody: VoiceAffectResult,
    ) -> SpeakerAffect | None:
        return None


class AudioEventBackend:
    provider_name = "unknown"

    def detect(self, audio: NormalizedAudio, *, transcript: str) -> list[AudioEvent]:
        raise NotImplementedError


class DisabledAudioEventBackend(AudioEventBackend):
    provider_name = "disabled"

    def detect(self, audio: NormalizedAudio, *, transcript: str) -> list[AudioEvent]:
        return []


class HuggingFaceSpeakerEmotionBackend(SpeakerEmotionBackend):
    provider_name = "huggingface"

    _LABELS = {
        "ang": "angry",
        "angry": "angry",
        "hap": "happy",
        "happy": "happy",
        "neu": "neutral",
        "neutral": "neutral",
        "sad": "sad",
    }

    def __init__(self, model_name: str | None = None) -> None:
        self._model_name = model_name or settings.speaker_emotion_model
        self._feature_extractor: Any = None
        self._model: Any = None

    def analyze(
        self,
        audio: NormalizedAudio,
        *,
        transcript: str,
        prosody: VoiceAffectResult,
    ) -> SpeakerAffect | None:
        import numpy as np

        feature_extractor, model = self._components()
        inputs = feature_extractor(
            np.asarray(audio.samples, dtype=np.float32),
            sampling_rate=audio.sample_rate,
            return_tensors="pt",
        )
        inputs = _move_inputs(inputs, _model_device(model))
        with _torch().inference_mode():
            logits = model(**inputs).logits[0]
        probabilities = logits.softmax(dim=-1)
        score, label_id = probabilities.max(dim=-1)
        raw_label = str(model.config.id2label[int(label_id)]).strip().lower()
        if not raw_label:
            return None
        emotion = self._LABELS.get(raw_label, "unknown")
        return SpeakerAffect(
            emotion=emotion,
            confidence=float(score),
            energy=str(prosody.labels.get("energy") or "unknown"),
            pace=str(prosody.labels.get("pace") or "unknown"),
            delivery=str(prosody.labels.get("delivery") or "unknown"),
            source=f"huggingface:{self._model_name}",
        )

    def _components(self):
        if self._feature_extractor is not None and self._model is not None:
            return self._feature_extractor, self._model
        try:
            from transformers import AutoFeatureExtractor, AutoModelForAudioClassification
        except Exception as exc:
            raise ConfigurationError(
                "Hugging Face speaker emotion requires the audio-understanding extra: "
                ".venv/bin/python -m pip install -e '.[audio-understanding]'"
            ) from exc
        self._feature_extractor = AutoFeatureExtractor.from_pretrained(
            self._model_name,
            local_files_only=settings.audio_model_local_files_only,
        )
        self._model = AutoModelForAudioClassification.from_pretrained(
            self._model_name,
            local_files_only=settings.audio_model_local_files_only,
        )
        self._model.to(_torch_device(settings.speaker_emotion_device))
        self._model.eval()
        return self._feature_extractor, self._model


class HuggingFaceAudioEventBackend(AudioEventBackend):
    provider_name = "huggingface"

    def __init__(self, model_name: str | None = None, *, window_seconds: float = 10.0) -> None:
        self._model_name = model_name or settings.audio_event_model
        self._window_seconds = max(1.0, window_seconds)
        self._feature_extractor: Any = None
        self._model: Any = None

    def detect(self, audio: NormalizedAudio, *, transcript: str) -> list[AudioEvent]:
        import numpy as np

        feature_extractor, model = self._components()
        window_samples = max(1, round(audio.sample_rate * self._window_seconds))
        events: list[AudioEvent] = []
        for start in range(0, len(audio.samples), window_samples):
            window = audio.samples[start:start + window_samples]
            if not window:
                continue
            inputs = feature_extractor(
                np.asarray(window, dtype=np.float32),
                sampling_rate=audio.sample_rate,
                return_tensors="pt",
            )
            inputs = _move_inputs(inputs, _model_device(model))
            with _torch().inference_mode():
                logits = model(**inputs).logits[0]
            probabilities = logits.sigmoid()
            start_ms = start / audio.sample_rate * 1000.0
            end_ms = (start + len(window)) / audio.sample_rate * 1000.0
            for label_id, score in enumerate(probabilities):
                label = str(model.config.id2label[label_id]).strip()
                if not label:
                    continue
                events.append(
                    AudioEvent(
                        label=label,
                        confidence=float(score),
                        start_ms=start_ms,
                        end_ms=end_ms,
                        source=f"huggingface:{self._model_name}",
                    )
                )
        return events

    def _components(self):
        if self._feature_extractor is not None and self._model is not None:
            return self._feature_extractor, self._model
        try:
            from transformers import AutoFeatureExtractor, AutoModelForAudioClassification
        except Exception as exc:
            raise ConfigurationError(
                "Hugging Face audio events require the audio-understanding extra: "
                ".venv/bin/python -m pip install -e '.[audio-understanding]'"
            ) from exc
        self._feature_extractor = AutoFeatureExtractor.from_pretrained(
            self._model_name,
            local_files_only=settings.audio_model_local_files_only,
        )
        self._model = AutoModelForAudioClassification.from_pretrained(
            self._model_name,
            local_files_only=settings.audio_model_local_files_only,
        )
        self._model.to(_torch_device(settings.audio_event_device))
        self._model.eval()
        return self._feature_extractor, self._model


class AudioUnderstandingService:
    """Combine prosody, speaker-emotion, and audio-event analysis.

    Model-backed adapters are intentionally separate from STT and can be added
    without changing the public voice-turn contract.
    """

    analyzer_version = "audio-understanding-v1"

    def __init__(
        self,
        *,
        decoder: AudioInputDecoder | None = None,
        affect_analyzer: VoiceAffectAnalyzer | None = None,
        speaker_emotion_backend: SpeakerEmotionBackend | None = None,
        audio_event_backend: AudioEventBackend | None = None,
        allow_remote: bool = True,
    ) -> None:
        self._allow_remote = allow_remote
        self._decoder = decoder or AudioInputDecoder()
        self._affect_analyzer = affect_analyzer or VoiceAffectAnalyzer(decoder=self._decoder)
        use_remote_backends = allow_remote and bool(settings.audio_understanding_endpoint)
        self._speaker_emotion = speaker_emotion_backend or (
            DisabledSpeakerEmotionBackend() if use_remote_backends else self._build_speaker_emotion_backend()
        )
        self._audio_events = audio_event_backend or (
            DisabledAudioEventBackend() if use_remote_backends else self._build_audio_event_backend()
        )

    def preload(self) -> None:
        if isinstance(self._speaker_emotion, HuggingFaceSpeakerEmotionBackend):
            self._speaker_emotion._components()
        if isinstance(self._audio_events, HuggingFaceAudioEventBackend):
            self._audio_events._components()

    def analyze_path(self, path: Path | str, *, transcript: str = "") -> VoiceInputContext:
        if not settings.audio_understanding_enabled:
            return VoiceInputContext(
                ok=False,
                analyzer_version=self.analyzer_version,
                detail="audio understanding disabled",
            )

        audio_path = Path(path)
        if self._allow_remote and settings.audio_understanding_endpoint:
            remote = self._analyze_remote(audio_path, transcript=transcript)
            if remote is not None:
                return remote
        started_at = time.perf_counter()
        details: list[str] = []

        decode_started = time.perf_counter()
        try:
            audio = self._decoder.decode_path(audio_path)
        except Exception as exc:
            return VoiceInputContext(
                ok=False,
                analyzer_version=self.analyzer_version,
                timing_ms={
                    "decode_ms": round((time.perf_counter() - decode_started) * 1000, 1),
                    "total_ms": round((time.perf_counter() - started_at) * 1000, 1),
                },
                detail=f"audio understanding unavailable: {exc}",
            )
        decode_ms = round((time.perf_counter() - decode_started) * 1000, 1)

        prosody_started = time.perf_counter()
        prosody = self._affect_analyzer.analyze_audio(audio, transcript=transcript)
        prosody_ms = round((time.perf_counter() - prosody_started) * 1000, 1)
        if prosody.detail:
            details.append(prosody.detail)

        emotion_started = time.perf_counter()
        if settings.speaker_emotion_requires_transcript and not transcript.strip():
            speaker_affect = None
        else:
            try:
                speaker_affect = self._speaker_emotion.analyze(
                    audio,
                    transcript=transcript,
                    prosody=prosody,
                )
            except Exception as exc:
                speaker_affect = None
                details.append(f"speaker emotion unavailable: {exc}")
        emotion_ms = round((time.perf_counter() - emotion_started) * 1000, 1)

        if speaker_affect is None and prosody.ok:
            speaker_affect = SpeakerAffect(
                emotion="unknown",
                confidence=float(prosody.labels.get("confidence") or 0.0),
                energy=str(prosody.labels.get("energy") or "unknown"),
                pace=str(prosody.labels.get("pace") or "unknown"),
                delivery=str(prosody.labels.get("delivery") or "unknown"),
                source=str(prosody.labels.get("source") or "signal_features"),
            )
        elif speaker_affect is not None and speaker_affect.confidence < settings.speaker_emotion_min_confidence:
            speaker_affect = speaker_affect.model_copy(update={"emotion": "uncertain"})

        events_started = time.perf_counter()
        try:
            raw_events = self._audio_events.detect(audio, transcript=transcript)
        except Exception as exc:
            raw_events = []
            details.append(f"audio events unavailable: {exc}")
        events_ms = round((time.perf_counter() - events_started) * 1000, 1)

        events = postprocess_audio_events(
            raw_events,
            policy=AudioEventPolicy(
                allowed_labels=frozenset(
                    normalized
                    for label in settings.audio_event_labels
                    if (normalized := normalize_audio_event_label(label)) is not None
                ),
                min_confidence=settings.audio_event_min_confidence,
                min_duration_ms=settings.audio_event_min_duration_ms,
                merge_gap_ms=settings.audio_event_merge_gap_ms,
                cooldown_ms=settings.audio_event_cooldown_ms,
            ),
        )

        total_ms = round((time.perf_counter() - started_at) * 1000, 1)
        return VoiceInputContext(
            ok=bool(prosody.ok or speaker_affect is not None or events),
            speaker_affect=speaker_affect,
            events=events,
            features=prosody.features,
            analyzer_version=self.analyzer_version,
            timing_ms={
                "decode_ms": decode_ms,
                "prosody_ms": prosody_ms,
                "speaker_emotion_ms": emotion_ms,
                "audio_events_ms": events_ms,
                "total_ms": total_ms,
            },
            detail="; ".join(details) or None,
        )

    @staticmethod
    def _build_speaker_emotion_backend() -> SpeakerEmotionBackend:
        provider = settings.speaker_emotion_provider.strip().lower()
        if provider in {"", "disabled", "none"}:
            return DisabledSpeakerEmotionBackend()
        if provider in {"huggingface", "hf"}:
            return HuggingFaceSpeakerEmotionBackend()
        raise ConfigurationError(f"Unsupported SHANA_SPEAKER_EMOTION_PROVIDER: {settings.speaker_emotion_provider}")

    @staticmethod
    def _build_audio_event_backend() -> AudioEventBackend:
        provider = settings.audio_event_provider.strip().lower()
        if provider in {"", "disabled", "none"}:
            return DisabledAudioEventBackend()
        if provider in {"huggingface", "hf"}:
            return HuggingFaceAudioEventBackend()
        raise ConfigurationError(f"Unsupported SHANA_AUDIO_EVENT_PROVIDER: {settings.audio_event_provider}")

    @staticmethod
    def _analyze_remote(path: Path, *, transcript: str) -> VoiceInputContext | None:
        try:
            with path.open("rb") as audio_file:
                response = httpx.post(
                    str(settings.audio_understanding_endpoint),
                    files={"audio_file": (path.name, audio_file, "application/octet-stream")},
                    data={"transcript": transcript},
                    timeout=max(1, settings.audio_understanding_request_timeout_seconds),
                )
            response.raise_for_status()
            return VoiceInputContext.model_validate(response.json())
        except Exception:
            return None


def build_audio_prompt_context(
    audio_context: VoiceInputContext,
    *,
    enabled: bool | None = None,
) -> str | None:
    """Build a bounded trusted note from confidence-gated observations."""
    if enabled is None:
        enabled = settings.audio_understanding_prompt_enabled
    if not enabled or not audio_context.ok:
        return None

    observations: list[str] = []
    affect = audio_context.speaker_affect
    if (
        affect is not None
        and affect.emotion not in {"", "unknown", "uncertain"}
        and affect.confidence >= settings.speaker_emotion_min_confidence
    ):
        observations.append(f"probable speaker emotion: {affect.emotion} ({affect.confidence:.2f} confidence)")

    for event in audio_context.events[:5]:
        if event.confidence >= settings.audio_event_min_confidence:
            observations.append(f"probable audio event: {event.label} ({event.confidence:.2f} confidence)")

    if not observations:
        return None
    return (
        "Audio observations, not user-stated facts:\n- "
        + "\n- ".join(observations)
        + "\nTreat these observations cautiously. Do not infer identity, intent, consent, health, or truthfulness."
    )


def _torch():
    try:
        import torch
    except Exception as exc:
        raise ConfigurationError(
            "Hugging Face audio understanding requires a working PyTorch installation."
        ) from exc
    return torch


def _torch_device(device: str) -> str:
    normalized = device.strip().lower()
    if normalized.startswith("cuda"):
        return normalized if ":" in normalized else "cuda:0"
    return "cpu"


def _model_device(model: Any) -> Any:
    try:
        return next(model.parameters()).device
    except Exception:
        return _torch_device(settings.audio_analysis_device)


def _move_inputs(inputs: Any, device: Any) -> Any:
    if hasattr(inputs, "to"):
        return inputs.to(device)
    if isinstance(inputs, dict):
        return {
            key: value.to(device) if hasattr(value, "to") else value
            for key, value in inputs.items()
        }
    return inputs
