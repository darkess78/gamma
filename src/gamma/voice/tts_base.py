"""TTS result type and the backend base classes."""
from __future__ import annotations

import re
import wave
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import settings
from ..errors import ExternalServiceError


@dataclass(slots=True)
class TTSResult:
    """TTS synthesis output result.
    
    Attributes:
        provider: TTS provider name.
        text: Synthesized text.
        audio_path: Path to generated audio file.
        content_type: Audio MIME type.
        metadata: Optional generation metadata.
    """
    provider: str
    text: str
    audio_path: str
    content_type: str
    metadata: dict[str, Any] | None = None


class TTSBackend:
    """Abstract base class for TTS backend implementations.
    
    Attributes:
        provider_name: Name of the TTS provider.
    
    Subclasses:
        OpenAITTSBackend: Uses OpenAI TTS API.
        PiperTTSBackend: Uses Piper TTS binaries.
        QwenTTSBackend: Uses qwen_tts_server HTTP backend.
        StubTTSBackend: Generates simple tone waveform for testing.
    """

    provider_name: str = "unknown"

    def synthesize(self, text: str, emotion: str | None = None) -> TTSResult:
        """Synthesize text to audio.
        
        Args:
            text: Text to synthesize.
            emotion: Optional emotion style.
            
        Returns:
            TTSResult: Generated audio file info.
            
        Raises:
            NotImplementedError: Subclasses must implement.
        """
        raise NotImplementedError


class BaseFileTTSBackend(TTSBackend):
    def _target_path(self, suffix: str) -> Path:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        return settings.audio_output_dir / f"tts-{stamp}{suffix}"

    def _content_type_for_suffix(self, suffix: str) -> str:
        normalized = suffix.lstrip(".").lower()
        if normalized == "wav":
            return "audio/wav"
        return f"audio/{normalized}"

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Apply speech-specific text replacements without changing visible reply text."""
        abbrevs = {
            r"\bTTS\b": "text to speech",
            r"\bSTT\b": "speech to text",
            r"\bLLM\b": "L L M",
            r"\bAI\b": "A I",
            r"\bAPI\b": "A P I",
            r"\bUI\b": "U I",
            r"\bURL\b": "U R L",
            r"\bGPU\b": "G P U",
            r"\bCPU\b": "C P U",
            r"\bRAM\b": "ram",
            r"\bRVC\b": "R V C",
        }
        pronunciation_overrides = {
            r"\bShana\b": "Shawna",
            r"\bNeety\b": "Nee-tee",
        }
        for pattern, replacement in abbrevs.items():
            text = re.sub(pattern, replacement, text)
        for pattern, replacement in pronunciation_overrides.items():
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        return text


def validate_wav_file(path: Path, *, provider_name: str) -> None:
    """Validate WAV file produced by backend.
    
    Args:
        path: WAV file path.
        provider_name: Provider name for error messages.
        
    Raises:
        ExternalServiceError: If invalid, empty, or silent WAV.
    """
    try:
        with wave.open(str(path), "rb") as wav_file:
            frame_count = wav_file.getnframes()
            sample_width = wav_file.getsampwidth()
            frame_bytes = wav_file.readframes(frame_count)
    except Exception as exc:
        raise ExternalServiceError(f"{provider_name} returned invalid WAV data: {exc}") from exc

    if frame_count <= 0 or not frame_bytes:
        raise ExternalServiceError(f"{provider_name} returned an empty WAV payload.")

    if sample_width == 1:
        payload_has_signal = any(byte != 128 for byte in frame_bytes)
    else:
        payload_has_signal = any(byte != 0 for byte in frame_bytes)
    if not payload_has_signal:
        raise ExternalServiceError(f"{provider_name} returned silent audio.")
