from __future__ import annotations

from pathlib import Path
import logging
import re

from ..config import settings
from ..errors import ConfigurationError, ExternalServiceError
from ..system.cuda_env import preload_cuda12_libraries
from ..system.torch_devices import resolve_torch_device


def _ctranslate2_device(device: str, index) -> tuple[str, int | None]:
    """("cuda", index) when a CUDA device is asked for and ctranslate2 sees it, else ("cpu", None)."""
    wanted = str(device or "cpu").strip().lower()
    if not wanted.startswith("cuda") and wanted != "auto":
        return "cpu", None
    try:
        import ctranslate2
        count = ctranslate2.get_cuda_device_count()
    except Exception:
        count = 0
    if wanted.startswith("cuda:"):
        index = int(wanted.split(":", 1)[1])
    index = int(index) if str(index or "").strip() not in {"", "None"} else 0
    if count and 0 <= index < count:
        return "cuda", index
    if count and wanted == "auto":
        return "cuda", 0
    log.warning(f"STT asked for {device} (index {index}) but ctranslate2 sees {count} CUDA device(s); using CPU.")
    return "cpu", None


log = logging.getLogger(__name__)
_STT_NAME_PROMPT = "The assistant is named Shana. The owner is named Neety."


def normalize_transcript(text: str) -> str:
    """Normalize ASR transcript text.
    
    Args:
        text: Raw transcript string from ASR.
        
    Returns:
        str: Normalized text with casing and name corrections.
        
    Example:
        >>> normalize_transcript("hey china shannon, what is gamma")
        'Hey Shana, what is Gamma'
    """
    normalized = " ".join(text.strip().split())
    if not normalized:
        return ""
    normalized = re.sub(r"\b(?:shawna|shauna|shanna|shayna)\b", "Shana", normalized, flags=re.IGNORECASE)
    normalized = re.sub(
        r"\b(hey|hi|okay|ok)([,\s]+)(?:china|shannon)\b",
        lambda match: f"{match.group(1)}{match.group(2)}Shana",
        normalized,
        flags=re.IGNORECASE,
    )
    return normalized


class STTBackend:
    """Abstract base class for STT backend implementations.
    
    Attributes:
        provider_name: Name of the STT provider.
    
    Subclasses:
        FasterWhisperSTTBackend: Uses faster-whisper locally.
        OpenAISTTBackend: Uses OpenAI for STT.
        StubSTTBackend: Stub backend reading sidecar text files.
    """

    provider_name: str = "unknown"

    def transcribe_audio(self, source: str) -> str:
        """Transcribe audio source to text.
        
        Args:
            source: Audio file path or URL.
            
        Returns:
            str: Transcribed text.
            
        Raises:
            NotImplementedError: Subclasses must implement.
        """
        raise NotImplementedError


class FasterWhisperSTTBackend(STTBackend):
    """faster-whisper STT backend.
    
    Attributes:
        provider_name: 'faster-whisper'.
        _model: Loaded Whisper model instance.
    """

    provider_name = "faster-whisper"

    def __init__(self) -> None:
        """Initialize faster-whisper model.
        
        Sets up Whisper model with configured device and model settings.
        Uses configured CUDA device if available.
        """
        try:
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
                else:
                    # Keep original config (cpu, auto, or non-cuda device)
                    resolved_device = resolved_spec or "cpu"
                    resolved_index = None
            except ImportError:
                # The main venv has no torch; faster-whisper runs on ctranslate2, which can
                # report CUDA devices itself.
                resolved_device, resolved_index = _ctranslate2_device(settings.stt_device, settings.stt_device_index)
            except Exception:
                log.warning(
                    f"Failed to resolve device '{settings.stt_device}'. Falling back to CPU."
                )
                resolved_device = "cpu"
                resolved_index = None
            if resolved_device == "cuda":
                preload_cuda12_libraries()
            from faster_whisper import WhisperModel

            # For CPU STT, omit device_index (default is 0). For GPU, pass explicit index.
            if resolved_device == "cpu":
                self._model = WhisperModel(
                    settings.stt_model,
                    device=resolved_device,
                    compute_type=settings.stt_compute_type,
                )
            else:
                self._model = WhisperModel(
                    settings.stt_model,
                    device=resolved_device,
                    device_index=resolved_index if resolved_index is not None else 0,
                    compute_type=settings.stt_compute_type,
                )
        except Exception as exc:
            raise ConfigurationError(f"Failed to initialize faster-whisper STT: {exc}") from exc

    def transcribe_audio(self, source: str) -> str:
        """Transcribe audio file to text.
        
        Args:
            source: Local audio file path.
            
        Returns:
            str: Concatenated transcript text.
            
        Raises:
            ExternalServiceError: If transcription fails.
        """
        try:
            try:
                segments, _info = self._model.transcribe(
                    source,
                    initial_prompt=_STT_NAME_PROMPT,
                    hotwords="Shana Neety",
                )
            except TypeError:
                segments, _info = self._model.transcribe(source, initial_prompt=_STT_NAME_PROMPT)
        except Exception as exc:
            raise ExternalServiceError(f"STT transcription failed: {exc}") from exc
        return " ".join(segment.text.strip() for segment in segments).strip()


class OpenAISTTBackend(STTBackend):
    """OpenAI-hosted STT backend.
    
    Attributes:
        provider_name: 'openai'.
        _client: OpenAI SDK client.
    """

    provider_name = "openai"

    def __init__(self) -> None:
        """Initialize OpenAI client.
        
        Raises:
            ConfigurationError: If OPENAI_API_KEY not configured.
        """
        if not settings.openai_api_key:
            raise ConfigurationError("OPENAI_API_KEY is required for SHANA_STT_PROVIDER=openai.")
        try:
            from openai import OpenAI
        except Exception as exc:
            raise ConfigurationError("The OpenAI SDK is required for SHANA_STT_PROVIDER=openai.") from exc
        self._client = OpenAI(api_key=settings.openai_api_key)

    def transcribe_audio(self, source: str) -> str:
        """Transcribe audio file to text via OpenAI.
        
        Args:
            source: Local audio file path.
            
        Returns:
            str: Transcript text.
            
        Raises:
            ExternalServiceError: If transcription fails or returns empty.
        """
        audio_path = Path(source)
        if not audio_path.exists():
            raise ExternalServiceError(f"audio file not found: {audio_path}")

        try:
            with audio_path.open("rb") as audio_file:
                transcription = self._client.audio.transcriptions.create(
                    file=audio_file,
                    model=settings.stt_model,
                    response_format="text",
                    prompt=_STT_NAME_PROMPT,
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
    """Stub STT backend for testing.
    
    Reads transcript from sidecar .txt files next to audio input.
    
    Attributes:
        provider_name: 'stub'.
    """

    provider_name = "stub"

    def transcribe_audio(self, source: str) -> str:
        """Read transcript from sidecar .txt file.
        
        Args:
            source: Audio file path (sidecar .txt read from same directory).
            
        Returns:
            str: Transcript from sidecar file.
            
        Raises:
            ExternalServiceError: If sidecar file not found.
        """
        path = Path(source)
        txt_sidecar = path.with_suffix(".txt")
        if txt_sidecar.exists():
            return txt_sidecar.read_text(encoding="utf-8").strip()
        raise ExternalServiceError(
            "SHANA_STT_PROVIDER=stub expects a sidecar transcript file next to the audio input "
            f"(looked for: {txt_sidecar})."
        )


class STTService:
    """STT service that selects backend by configuration.
    
    Attributes:
        _backend: Selected STT backend instance.
    """

    def __init__(self) -> None:
        """Initialize STT service.
        
        Selects backend based on SHANA_STT_PROVIDER configuration.
        Raises ConfigurationError for unsupported providers.
        """
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
        """Transcribe audio source to text.
        
        Args:
            source: Audio file path or URL.
            
        Returns:
            str: Normalized transcript text.
        """
        return normalize_transcript(self._backend.transcribe_audio(source))
