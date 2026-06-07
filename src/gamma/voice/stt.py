from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import logging

from ..config import settings
from ..errors import ConfigurationError, ExternalServiceError
from ..system.cuda_env import prepend_cuda_library_path
from ..system.torch_devices import resolve_torch_device


log = logging.getLogger(__name__)


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
            prepend_cuda_library_path()
            resolved_device = settings.stt_device
            resolved_index = settings.stt_device_index
            try:
                import torch

                resolved_spec, warning = resolve_torch_device(
                    settings.stt_device,
                    preferred_index=settings.stt_device_index,
                    torch_module=torch,
                )
                if warning:
                    log.warning(warning)
                if resolved_spec == "cpu":
                    resolved_device = "cpu"
                    resolved_index = None
                elif resolved_spec.startswith("cuda:"):
                    resolved_device = "cuda"
                    resolved_index = int(resolved_spec.split(":", 1)[1])
            except Exception:
                pass
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                settings.stt_model,
                device=resolved_device,
                device_index=resolved_index,
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


class OpenAISTTBackend(STTBackend):
    provider_name = "openai"

    def __init__(self) -> None:
        if not settings.openai_api_key:
            raise ConfigurationError("OPENAI_API_KEY is required for SHANA_STT_PROVIDER=openai.")
        try:
            from openai import OpenAI
        except Exception as exc:
            raise ConfigurationError("The OpenAI SDK is required for SHANA_STT_PROVIDER=openai.") from exc
        self._client = OpenAI(api_key=settings.openai_api_key)

    def transcribe_audio(self, source: str) -> str:
        audio_path = Path(source)
        if not audio_path.exists():
            raise ExternalServiceError(f"audio file not found: {audio_path}")

        try:
            with audio_path.open("rb") as audio_file:
                transcription = self._client.audio.transcriptions.create(
                    file=audio_file,
                    model=settings.stt_model,
                    response_format="text",
                )
        except Exception as exc:
            raise ExternalServiceError(f"OpenAI STT transcription failed: {exc}") from exc

        if isinstance(transcription, str):
            return transcription.strip()
        text = getattr(transcription, "text", "").strip()
        if not text:
            raise ExternalServiceError("OpenAI STT returned an empty transcript.")
        return text


class StubSTTBackend(STTBackend):
    provider_name = "stub"

    def transcribe_audio(self, source: str) -> str:
        path = Path(source)
        txt_sidecar = path.with_suffix(".txt")
        if txt_sidecar.exists():
            return txt_sidecar.read_text(encoding="utf-8").strip()
        raise ExternalServiceError(
            "SHANA_STT_PROVIDER=stub expects a sidecar transcript file next to the audio input "
            f"(looked for: {txt_sidecar})."
        )


class STTService:
    def __init__(self) -> None:
        provider = settings.stt_provider.strip().lower()
        if provider in {"faster-whisper", "faster_whisper", "whisper", "local"}:
            self._backend: STTBackend = FasterWhisperSTTBackend()
        elif provider == "openai":
            self._backend = OpenAISTTBackend()
        elif provider == "stub":
            self._backend = StubSTTBackend()
        elif provider == "ollama":
            raise ConfigurationError(
                "SHANA_STT_PROVIDER=ollama is not supported. "
                "Use SHANA_STT_PROVIDER=local for faster-whisper or SHANA_STT_PROVIDER=openai for hosted STT."
            )
        else:
            raise ConfigurationError(f"Unsupported SHANA_STT_PROVIDER: {settings.stt_provider}")

    def transcribe_audio(self, source: str) -> str:
        return self._backend.transcribe_audio(source)
