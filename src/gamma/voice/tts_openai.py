"""OpenAI hosted TTS backend."""
from __future__ import annotations

import os
import time

from ..config import settings
from ..errors import ConfigurationError, ExternalServiceError
from .tts_base import BaseFileTTSBackend, TTSResult
from .voice_profiles import ResolvedTTSConfig


class OpenAITTSBackend(BaseFileTTSBackend):
    """OpenAI TTS API backend.
    
    Attributes:
        provider_name: 'openai'.
        _cfg: ResolvedTTSConfig configuration.
        _client: OpenAI SDK client.
    """

    provider_name = "openai"

    def __init__(self, cfg: ResolvedTTSConfig) -> None:
        """Initialize OpenAI TTS backend.
        
        Args:
            cfg: ResolvedTTSConfig configuration.
            
        Raises:
            ConfigurationError: If OPENAI_API_KEY not configured.
        """
        self._cfg = cfg
        api_key = settings.openai_api_key or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ConfigurationError("OPENAI_API_KEY is required for SHANA_TTS_PROVIDER=openai.")
        try:
            from openai import OpenAI
        except Exception as exc:
            raise ConfigurationError("The OpenAI SDK is required for SHANA_TTS_PROVIDER=openai.") from exc
        self._client = OpenAI(api_key=api_key)

    def synthesize(self, text: str, emotion: str | None = None) -> TTSResult:
        """Synthesize text via OpenAI TTS API.
        
        Args:
            text: Text to synthesize.
            emotion: Optional emotion style.
            
        Returns:
            TTSResult: Generated audio.
        """
        started_at = time.perf_counter()
        normalized_text = self._normalize_text(text)
        prompt_text = normalized_text if not emotion else f"Emotion: {emotion}.\n\n{normalized_text}"
        fmt = self._cfg.tts_format
        path = self._target_path(f".{fmt}")
        try:
            with self._client.audio.speech.with_streaming_response.create(
                model=self._cfg.tts_model,
                voice=self._cfg.tts_voice,
                input=prompt_text,
                response_format=fmt,
            ) as response:
                response.stream_to_file(path)
        except Exception as exc:
            raise ExternalServiceError(f"OpenAI TTS synthesis failed: {exc}") from exc
        return TTSResult(
            provider=self.provider_name,
            text=text,
            audio_path=str(path),
            content_type=self._content_type_for_suffix(fmt),
            metadata={
                "voice": self._cfg.tts_voice,
                "model": self._cfg.tts_model,
                "emotion": emotion,
                "timings_ms": {"backend_ms": round((time.perf_counter() - started_at) * 1000, 1)},
            },
        )
