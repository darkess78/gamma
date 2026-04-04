from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import settings
from ..errors import ConfigurationError, ExternalServiceError


@dataclass(slots=True)
class STTResult:
    text: str


class STTBackend:
    provider_name: str = "unknown"

    def transcribe_audio(self, source: str) -> str:
        raise NotImplementedError


class FasterWhisperSTTBackend(STTBackend):
    provider_name = "faster-whisper"

    def __init__(self) -> None:
        try:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                settings.stt_model,
                device=settings.stt_device,
                compute_type=settings.stt_compute_type,
            )
        except Exception as exc:
            raise ConfigurationError(f"Failed to initialize faster-whisper STT: {exc}") from exc

    def transcribe_audio(self, source: str) -> str:
        try:
            segments, _info = self._model.transcribe(source)
        except Exception as exc:
            raise ExternalServiceError(f"STT transcription failed: {exc}") from exc
        return " ".join(segment.text.strip() for segment in segments).strip()


class StubSTTBackend(STTBackend):
    provider_name = "stub"

    def transcribe_audio(self, source: str) -> str:
        path = Path(source)
        txt_sidecar = path.with_suffix(".txt")
        if txt_sidecar.exists():
            return txt_sidecar.read_text(encoding="utf-8").strip()
        raise ExternalServiceError(
            "RIKO_STT_PROVIDER=stub expects a sidecar transcript file next to the audio input "
            f"(looked for: {txt_sidecar})."
        )


class STTService:
    def __init__(self) -> None:
        provider = settings.stt_provider.strip().lower()
        if provider in {"faster-whisper", "faster_whisper", "whisper"}:
            self._backend: STTBackend = FasterWhisperSTTBackend()
        elif provider == "stub":
            self._backend = StubSTTBackend()
        else:
            raise ConfigurationError(f"Unsupported RIKO_STT_PROVIDER: {settings.stt_provider}")

    def transcribe_audio(self, source: str) -> str:
        return self._backend.transcribe_audio(source)
