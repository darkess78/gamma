from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
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
from .rvc_support import (
    resolve_rvc_index_path,
    resolve_rvc_model_path,
    resolve_rvc_project_root,
    resolve_rvc_python,
)


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
        elif provider == "piper":
            self._backend = PiperTTSBackend()
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
                "Use SHANA_TTS_PROVIDER=piper, SHANA_TTS_PROVIDER=local with GPT-SoVITS, SHANA_TTS_PROVIDER=stub, or SHANA_TTS_PROVIDER=openai."
            )
        else:
            raise ConfigurationError(f"Unsupported SHANA_TTS_PROVIDER: {settings.tts_provider}")

    def synthesize(self, text: str, emotion: str | None = None) -> TTSResult:
        started_at = time.perf_counter()
        result = self._backend.synthesize(text=text, emotion=emotion)
        result = self._maybe_apply_rvc(result, emotion=emotion)
        metadata = dict(result.metadata or {})
        timings = dict(metadata.get("timings_ms", {})) if isinstance(metadata.get("timings_ms"), dict) else {}
        timings["total_tts_pipeline_ms"] = round((time.perf_counter() - started_at) * 1000, 1)
        metadata["timings_ms"] = timings
        result.metadata = metadata
        return result

    def _maybe_apply_rvc(self, result: TTSResult, *, emotion: str | None) -> TTSResult:
        if not settings.rvc_enabled:
            return result
        started_at = time.perf_counter()
        if result.content_type != "audio/wav":
            raise ConfigurationError("RVC post-processing currently requires WAV input.")

        rvc_root = resolve_rvc_project_root(settings.rvc_project_root)
        rvc_python = resolve_rvc_python(settings.rvc_python, rvc_root)
        infer_cli = rvc_root / "tools" / "infer_cli.py"
        if not infer_cli.exists():
            raise ConfigurationError(f"RVC infer CLI not found: {infer_cli}")

        model_path = resolve_rvc_model_path(rvc_root, settings.rvc_model_name)
        model_name = model_path.name
        index_path = resolve_rvc_index_path(rvc_root, settings.rvc_index_path, model_name)
        converted_path = Path(result.audio_path).with_name(Path(result.audio_path).stem + "-rvc.wav")
        command = [
            str(rvc_python),
            str(infer_cli),
            "--input_path",
            str(result.audio_path),
            "--index_path",
            str(index_path),
            "--opt_path",
            str(converted_path),
            "--model_name",
            model_name,
            "--f0up_key",
            str(settings.rvc_pitch),
            "--f0method",
            settings.rvc_f0_method,
            "--index_rate",
            str(settings.rvc_index_rate),
            "--filter_radius",
            str(settings.rvc_filter_radius),
            "--resample_sr",
            str(settings.rvc_resample_sr),
            "--rms_mix_rate",
            str(settings.rvc_rms_mix_rate),
            "--protect",
            str(settings.rvc_protect),
            "--formant",
            str(settings.rvc_formant),
        ]
        if (settings.rvc_device or "").strip():
            command.extend(["--device", str(settings.rvc_device)])

        run_kwargs: dict[str, Any] = {
            "cwd": str(rvc_root),
            "capture_output": True,
            "text": True,
            "check": True,
        }
        if os.name == "nt":
            run_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            subprocess.run(command, **run_kwargs)
        except FileNotFoundError as exc:
            raise ConfigurationError(f"RVC Python not found: {rvc_python}") from exc
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            stdout = (exc.stdout or "").strip()
            details = stderr or stdout or "rvc conversion failed"
            raise ExternalServiceError(f"RVC conversion failed: {details}") from exc

        if not converted_path.exists():
            raise ExternalServiceError("RVC did not create an output WAV file.")

        self._validate_wav_file(converted_path, provider_name="RVC")
        metadata = dict(result.metadata or {})
        timings = dict(metadata.get("timings_ms", {})) if isinstance(metadata.get("timings_ms"), dict) else {}
        timings["rvc_ms"] = round((time.perf_counter() - started_at) * 1000, 1)
        metadata["timings_ms"] = timings
        metadata["rvc"] = {
            "enabled": True,
            "model_name": model_name,
            "index_path": str(index_path),
            "pitch": settings.rvc_pitch,
            "formant": settings.rvc_formant,
            "f0_method": settings.rvc_f0_method,
        }
        return TTSResult(
            provider=f"{result.provider}+rvc",
            text=result.text,
            audio_path=str(converted_path),
            content_type="audio/wav",
            metadata=metadata,
        )

    def _validate_wav_file(self, path: Path, *, provider_name: str) -> None:
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
        started_at = time.perf_counter()
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
                "timings_ms": {"backend_ms": round((time.perf_counter() - started_at) * 1000, 1)},
            },
        )


class GPTSoVITSTTSBackend(BaseFileTTSBackend):
    provider_name = "gpt-sovits"

    def __init__(self) -> None:
        if not settings.gpt_sovits_endpoint:
            raise ConfigurationError("SHANA_GPT_SOVITS_ENDPOINT is required for SHANA_TTS_PROVIDER=gpt-sovits.")

    def synthesize(self, text: str, emotion: str | None = None) -> TTSResult:
        started_at = time.perf_counter()
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

        self._validate_wav_payload(audio_bytes)
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
                "timings_ms": {"backend_ms": round((time.perf_counter() - started_at) * 1000, 1)},
            },
        )

    def _validate_wav_payload(self, audio_bytes: bytes) -> None:
        try:
            with wave.open(_BytesReader(audio_bytes), "rb") as wav_file:
                frame_count = wav_file.getnframes()
                sample_width = wav_file.getsampwidth()
                frame_bytes = wav_file.readframes(frame_count)
        except Exception as exc:
            raise ExternalServiceError(f"GPT-SoVITS returned invalid WAV data: {exc}") from exc

        if frame_count <= 0 or not frame_bytes:
            raise ExternalServiceError("GPT-SoVITS returned an empty WAV payload.")

        if sample_width == 1:
            payload_has_signal = any(byte != 128 for byte in frame_bytes)
        else:
            payload_has_signal = any(byte != 0 for byte in frame_bytes)

        if not payload_has_signal:
            raise ExternalServiceError(
                "GPT-SoVITS returned silent audio. Check the GPT-SoVITS server logs for inference errors."
            )


class _BytesReader:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._offset = 0

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = len(self._payload) - self._offset
        chunk = self._payload[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self._offset = offset
        elif whence == 1:
            self._offset += offset
        elif whence == 2:
            self._offset = len(self._payload) + offset
        else:
            raise ValueError(f"unsupported whence: {whence}")
        return self._offset

    def tell(self) -> int:
        return self._offset

    def close(self) -> None:
        return None


class PiperTTSBackend(BaseFileTTSBackend):
    provider_name = "piper"

    def __init__(self) -> None:
        executable = (settings.piper_executable or "").strip()
        if not executable:
            raise ConfigurationError("SHANA_PIPER_EXE is required for SHANA_TTS_PROVIDER=piper.")
        self._executable = executable
        if not shutil.which(self._executable):
            raise ConfigurationError(
                f"Piper executable not found: {self._executable}. Set SHANA_PIPER_EXE to the Piper binary."
            )

        model_path = (settings.piper_model_path or "").strip()
        if not model_path:
            raise ConfigurationError("SHANA_PIPER_MODEL_PATH is required for SHANA_TTS_PROVIDER=piper.")
        self._model_path = self._resolve_existing_path(
            model_path,
            env_name="SHANA_PIPER_MODEL_PATH",
        )

        config_path = (settings.piper_config_path or "").strip()
        self._config_path = (
            self._resolve_existing_path(config_path, env_name="SHANA_PIPER_CONFIG_PATH")
            if config_path
            else None
        )
        self._speaker_id = (settings.piper_speaker_id or "").strip() or None

    def synthesize(self, text: str, emotion: str | None = None) -> TTSResult:
        started_at = time.perf_counter()
        path = self._target_path(".wav")
        command = [
            self._executable,
            "--model",
            str(self._model_path),
            "--output_file",
            str(path),
        ]
        if self._config_path is not None:
            command.extend(["--config", str(self._config_path)])
        if self._speaker_id is not None:
            command.extend(["--speaker", self._speaker_id])

        run_kwargs: dict[str, Any] = {
            "input": text,
            "text": True,
            "capture_output": True,
            "check": True,
        }
        if os.name == "nt":
            run_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            subprocess.run(command, **run_kwargs)
        except FileNotFoundError as exc:
            raise ConfigurationError(
                f"Piper executable not found: {self._executable}. Set SHANA_PIPER_EXE to the Piper binary."
            ) from exc
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            stdout = (exc.stdout or "").strip()
            details = stderr or stdout or "piper synthesis failed"
            raise ExternalServiceError(f"Piper synthesis failed: {details}") from exc

        if not path.exists():
            raise ExternalServiceError("Piper did not create an output WAV file.")

        self._validate_wav_file(path)
        return TTSResult(
            provider=self.provider_name,
            text=text,
            audio_path=str(path),
            content_type="audio/wav",
            metadata={
                "model_path": str(self._model_path),
                "config_path": str(self._config_path) if self._config_path is not None else None,
                "speaker_id": self._speaker_id,
                "emotion": emotion,
                "timings_ms": {"backend_ms": round((time.perf_counter() - started_at) * 1000, 1)},
            },
        )

    def _resolve_existing_path(self, raw_path: str, *, env_name: str) -> Path:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = settings.project_root / path
        path = path.resolve()
        if not path.exists():
            raise ConfigurationError(f"{env_name} does not exist: {path}")
        return path

    def _validate_wav_file(self, path: Path) -> None:
        TTSService._validate_wav_file(self, path, provider_name="Piper")


class StubTTSBackend(BaseFileTTSBackend):
    provider_name = "stub"

    def synthesize(self, text: str, emotion: str | None = None) -> TTSResult:
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
