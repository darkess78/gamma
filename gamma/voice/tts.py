from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass
from datetime import UTC, datetime
from math import pi, sin
from pathlib import Path
from struct import pack
from typing import Any

from ..config import settings
from ..errors import ConfigurationError, ExternalServiceError


@dataclass(slots=True)
class TTSResult:
    provider: str
    text: str
    audio_path: str
    content_type: str
    metadata: dict[str, Any] | None = None


class TTSBackend:
    provider_name: str = "unknown"

    def synthesize(self, text: str, emotion: str | None = None) -> TTSResult:
        raise NotImplementedError


class TTSService:
    def __init__(self) -> None:
        provider = settings.tts_provider.strip().lower()
        if provider == "openai":
            self._backend: TTSBackend = OpenAITTSBackend()
        elif provider in {"gpt-sovits", "gpt_sovits", "gptsovits"}:
            self._backend = GPTSoVITSTTSBackend()
        elif provider == "local":
            if settings.gpt_sovits_endpoint:
                self._backend = GPTSoVITSTTSBackend()
            else:
                raise ConfigurationError(
                    "SHANA_TTS_PROVIDER=local requires SHANA_GPT_SOVITS_ENDPOINT to point at a local GPT-SoVITS server. "
                    "Use SHANA_TTS_PROVIDER=stub for local placeholder audio or SHANA_TTS_PROVIDER=openai for hosted TTS."
                )
        elif provider == "stub":
            self._backend = StubTTSBackend()
        elif provider == "ollama":
            raise ConfigurationError(
                "SHANA_TTS_PROVIDER=ollama is not supported. "
                "Use SHANA_TTS_PROVIDER=local with GPT-SoVITS, SHANA_TTS_PROVIDER=stub, or SHANA_TTS_PROVIDER=openai."
            )
        else:
            raise ConfigurationError(f"Unsupported SHANA_TTS_PROVIDER: {settings.tts_provider}")

    def synthesize(self, text: str, emotion: str | None = None) -> TTSResult:
        return self._backend.synthesize(text=text, emotion=emotion)


class BaseFileTTSBackend(TTSBackend):
    def _target_path(self, suffix: str) -> Path:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        return settings.audio_output_dir / f"tts-{stamp}{suffix}"

    def _content_type_for_suffix(self, suffix: str) -> str:
        normalized = suffix.lstrip(".").lower()
        if normalized == "wav":
            return "audio/wav"
        return f"audio/{normalized}"


class OpenAITTSBackend(BaseFileTTSBackend):
    provider_name = "openai"

    def __init__(self) -> None:
        api_key = settings.openai_api_key or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ConfigurationError("OPENAI_API_KEY is required for SHANA_TTS_PROVIDER=openai.")
        try:
            from openai import OpenAI
        except Exception as exc:
            raise ConfigurationError("The OpenAI SDK is required for SHANA_TTS_PROVIDER=openai.") from exc
        self._client = OpenAI(api_key=api_key)

    def synthesize(self, text: str, emotion: str | None = None) -> TTSResult:
        prompt_text = text if not emotion else f"Emotion: {emotion}.\n\n{text}"
        fmt = settings.tts_format
        path = self._target_path(f".{fmt}")
        try:
            with self._client.audio.speech.with_streaming_response.create(
                model=settings.tts_model,
                voice=settings.tts_voice,
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
                "voice": settings.tts_voice,
                "model": settings.tts_model,
                "emotion": emotion,
            },
        )


class GPTSoVITSTTSBackend(BaseFileTTSBackend):
    provider_name = "gpt-sovits"

    def __init__(self) -> None:
        if not settings.gpt_sovits_endpoint:
            raise ConfigurationError("SHANA_GPT_SOVITS_ENDPOINT is required for SHANA_TTS_PROVIDER=gpt-sovits.")

    def synthesize(self, text: str, emotion: str | None = None) -> TTSResult:
        fmt = settings.tts_format.lower()
        if fmt != "wav":
            raise ConfigurationError("GPT-SoVITS backend currently expects SHANA_TTS_FORMAT=wav.")

        payload: dict[str, Any] = {
            "text": text,
            "text_lang": settings.gpt_sovits_text_lang,
        }
        if settings.gpt_sovits_reference_audio:
            payload["ref_audio_path"] = settings.gpt_sovits_reference_audio
        if settings.gpt_sovits_prompt_text:
            payload["prompt_text"] = settings.gpt_sovits_prompt_text
        if settings.gpt_sovits_prompt_lang:
            payload["prompt_lang"] = settings.gpt_sovits_prompt_lang
        if emotion:
            payload["emotion"] = emotion
        if settings.gpt_sovits_extra_json:
            payload.update(settings.gpt_sovits_extra_json)

        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            settings.gpt_sovits_endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        path = self._target_path(".wav")
        try:
            with urllib.request.urlopen(request, timeout=settings.gpt_sovits_timeout_seconds) as response:
                audio_bytes = response.read()
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise ExternalServiceError(f"GPT-SoVITS request failed: HTTP {exc.code}: {details}") from exc
        except urllib.error.URLError as exc:
            raise ExternalServiceError(f"GPT-SoVITS request failed: {exc}") from exc

        path.write_bytes(audio_bytes)
        return TTSResult(
            provider=self.provider_name,
            text=text,
            audio_path=str(path),
            content_type="audio/wav",
            metadata={
                "endpoint": settings.gpt_sovits_endpoint,
                "reference_audio": settings.gpt_sovits_reference_audio,
                "prompt_text": settings.gpt_sovits_prompt_text,
                "prompt_lang": settings.gpt_sovits_prompt_lang,
                "text_lang": settings.gpt_sovits_text_lang,
            },
        )


class StubTTSBackend(BaseFileTTSBackend):
    provider_name = "stub"

    def synthesize(self, text: str, emotion: str | None = None) -> TTSResult:
        path = self._target_path(".wav")
        self._write_tone_wave(path, duration=max(0.18 * max(len(text.split()), 1), 0.35))
        text_path = path.with_suffix(".txt")
        text_path.write_text(text + "\n", encoding="utf-8")
        return TTSResult(
            provider=self.provider_name,
            text=text,
            audio_path=str(path),
            content_type="audio/wav",
            metadata={"emotion": emotion},
        )

    def _write_tone_wave(self, path: Path, duration: float, sample_rate: int = 16_000) -> None:
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
