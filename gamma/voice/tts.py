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

try:
    import numpy as np
    from scipy.signal import stft, istft
    from scipy.ndimage import uniform_filter1d as _uniform_filter1d
    _SCIPY_AVAILABLE = True
except ImportError:
    _SCIPY_AVAILABLE = False

from ..config import settings
from ..errors import ConfigurationError, ExternalServiceError
from .rvc_support import (
    resolve_rvc_index_path,
    resolve_rvc_model_path,
    resolve_rvc_project_root,
    resolve_rvc_python,
)
from .expressive_text import build_qwen_instruct, strip_hidden_style_tags
from .voice_profiles import ResolvedTTSConfig, resolve_tts_config


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
        self._cfg = resolve_tts_config()
        provider = self._cfg.provider.strip().lower()
        if provider == "openai":
            self._backend: TTSBackend = OpenAITTSBackend(self._cfg)
        elif provider == "piper":
            self._backend = PiperTTSBackend(self._cfg)
        elif provider in {"gpt-sovits", "gpt_sovits", "gptsovits"}:
            self._backend = GPTSoVITSTTSBackend(self._cfg)
        elif provider in {"qwen", "qwen-tts", "qwen_tts", "qwentts"}:
            self._backend = QwenTTSBackend(self._cfg)
        elif provider == "local":
            if self._cfg.gpt_sovits_endpoint:
                self._backend = GPTSoVITSTTSBackend(self._cfg)
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
            raise ConfigurationError(f"Unsupported SHANA_TTS_PROVIDER: {self._cfg.provider}")

    _MAX_CHUNK_CHARS = 3800  # conservative limit; keeps all backends happy (OpenAI caps at 4096)

    def synthesize_multipart(self, text: str) -> TTSResult:
        """Split *text* into paragraph/sentence chunks, synthesize each, and stitch WAV frames."""
        chunks = self._split_text(text)
        if not chunks:
            raise ValueError("text is empty after parsing")
        if len(chunks) == 1:
            return self.synthesize(chunks[0])
        results = [self.synthesize(chunk) for chunk in chunks]
        return self._concat_wav_results(results)

    def _split_text(self, text: str) -> list[str]:
        import re
        paragraphs = re.split(r"\n\n+", text.strip())
        chunks: list[str] = []
        for para in paragraphs:
            para = para.strip().replace("\n", " ")
            if not para:
                continue
            if len(para) <= self._MAX_CHUNK_CHARS:
                chunks.append(para)
            else:
                # split long paragraphs at sentence boundaries
                sentences = re.split(r"(?<=[.!?]) +", para)
                current = ""
                for sentence in sentences:
                    sentence = sentence.strip()
                    if not sentence:
                        continue
                    candidate = (current + " " + sentence).strip() if current else sentence
                    if current and len(candidate) > self._MAX_CHUNK_CHARS:
                        chunks.append(current)
                        current = sentence
                    else:
                        current = candidate
                if current:
                    chunks.append(current)
        return chunks

    def _concat_wav_results(self, results: list[TTSResult]) -> TTSResult:
        non_wav = [r for r in results if r.content_type != "audio/wav"]
        if non_wav:
            raise ConfigurationError(
                f"multi-chunk synthesis requires WAV output but got {non_wav[0].content_type}. "
                "Set SHANA_TTS_FORMAT=wav when using the OpenAI provider."
            )
        frames_list: list[bytes] = []
        params = None
        for result in results:
            with wave.open(result.audio_path, "rb") as wf:
                if params is None:
                    params = wf.getparams()
                frames_list.append(wf.readframes(wf.getnframes()))
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        out_path = settings.audio_output_dir / f"tts-{stamp}.wav"
        with wave.open(str(out_path), "wb") as wf:
            wf.setparams(params)
            for frames in frames_list:
                wf.writeframes(frames)
        total_backend_ms = round(
            sum(
                float(((r.metadata or {}).get("timings_ms") or {}).get("backend_ms") or 0)
                for r in results
            ),
            1,
        )
        return TTSResult(
            provider=results[0].provider,
            text=" ".join(r.text for r in results),
            audio_path=str(out_path),
            content_type="audio/wav",
            metadata={
                "timings_ms": {"backend_ms": total_backend_ms},
                "chunk_count": len(results),
            },
        )

    def synthesize(self, text: str, emotion: str | None = None) -> TTSResult:
        started_at = time.perf_counter()
        expressive = strip_hidden_style_tags(text, default_emotion=emotion)
        result = self._backend.synthesize(text=expressive.clean_text, emotion=expressive.emotion)
        result = self._maybe_apply_rvc(result, emotion=expressive.emotion)
        result = self._maybe_apply_denoise(result)
        metadata = dict(result.metadata or {})
        metadata["hidden_style_tags"] = expressive.tags
        metadata["emotion"] = expressive.emotion
        timings = dict(metadata.get("timings_ms", {})) if isinstance(metadata.get("timings_ms"), dict) else {}
        timings["total_tts_pipeline_ms"] = round((time.perf_counter() - started_at) * 1000, 1)
        metadata["timings_ms"] = timings
        result.metadata = metadata
        return result

    def _maybe_apply_denoise(self, result: TTSResult) -> TTSResult:
        if not self._cfg.denoise_enabled:
            return result
        if not _SCIPY_AVAILABLE:
            return result
        path = Path(result.audio_path)
        try:
            with wave.open(str(path), "rb") as wf:
                sr = wf.getframerate()
                sw = wf.getsampwidth()
                ch = wf.getnchannels()
                frames = wf.readframes(wf.getnframes())
            if sw != 2:
                return result
            samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32)
            if ch == 2:
                samples = samples.reshape(-1, 2).mean(axis=1)
            denoised = _spectral_denoise(samples, sr, self._cfg.denoise_strength)
            # preserve original peak level then apply gain
            orig_peak = np.max(np.abs(samples))
            new_peak = np.max(np.abs(denoised))
            if new_peak > 0 and orig_peak > 0:
                denoised = denoised * (orig_peak / new_peak)
            if self._cfg.denoise_gain != 1.0:
                denoised = denoised * self._cfg.denoise_gain
            out = np.clip(denoised, -32767, 32767).astype(np.int16)
            out_path = path.with_name(path.stem + "-dn.wav")
            with wave.open(str(out_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sr)
                wf.writeframes(out.tobytes())
        except Exception:
            return result
        return TTSResult(
            provider=result.provider + "+dn",
            text=result.text,
            audio_path=str(out_path),
            content_type="audio/wav",
            metadata=result.metadata,
        )

    def _maybe_apply_rvc(self, result: TTSResult, *, emotion: str | None) -> TTSResult:
        if not self._cfg.rvc_enabled:
            return result
        started_at = time.perf_counter()
        if result.content_type != "audio/wav":
            raise ConfigurationError("RVC post-processing currently requires WAV input.")

        rvc_root = resolve_rvc_project_root(self._cfg.rvc_project_root)
        rvc_python = resolve_rvc_python(self._cfg.rvc_python, rvc_root)
        infer_cli = rvc_root / "tools" / "infer_cli.py"
        if not infer_cli.exists():
            raise ConfigurationError(f"RVC infer CLI not found: {infer_cli}")

        model_path = resolve_rvc_model_path(rvc_root, self._cfg.rvc_model_name)
        model_name = model_path.name
        index_path = resolve_rvc_index_path(rvc_root, self._cfg.rvc_index_path, model_name)
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
            str(self._cfg.rvc_pitch),
            "--f0method",
            self._cfg.rvc_f0_method,
            "--index_rate",
            str(self._cfg.rvc_index_rate),
            "--filter_radius",
            str(self._cfg.rvc_filter_radius),
            "--resample_sr",
            str(self._cfg.rvc_resample_sr),
            "--rms_mix_rate",
            str(self._cfg.rvc_rms_mix_rate),
            "--protect",
            str(self._cfg.rvc_protect),
            "--formant",
            str(self._cfg.rvc_formant),
        ]
        if (self._cfg.rvc_device or "").strip():
            command.extend(["--device", str(self._cfg.rvc_device)])

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
            "pitch": self._cfg.rvc_pitch,
            "formant": self._cfg.rvc_formant,
            "f0_method": self._cfg.rvc_f0_method,
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


def _spectral_denoise(samples: "np.ndarray", sr: int, strength: float) -> "np.ndarray":
    """
    Wiener filter denoising with a spectral floor to prevent over-suppression.
    Estimates noise power from quiet frames, then applies a soft gain mask
    H(f) = max(S/(S+N), floor) per bin. The gain floor prevents any bin from
    being fully silenced, eliminating the "underwater/watery" effect that
    results from over-aggressive suppression.
    `strength` controls noise overestimation (higher = more aggressive).
    """
    nperseg = 1024
    freqs, _, Zxx = stft(samples, fs=sr, nperseg=nperseg)
    power = np.abs(Zxx) ** 2

    # Estimate noise from quietest 10% of frames (conservative — avoids
    # treating quiet speech as noise)
    frame_power = np.mean(power, axis=0)
    threshold = np.percentile(frame_power, 10)
    noise_frames = power[:, frame_power <= threshold]
    if noise_frames.shape[1] == 0:
        return samples
    noise_power = np.mean(noise_frames, axis=1, keepdims=True) * strength

    # Wiener gain: H = S_est / (S_est + N)
    # S_est = P - N (estimated signal power, floored at 0)
    signal_power = np.maximum(power - noise_power, 0.0)
    wiener_gain = signal_power / (signal_power + noise_power + 1e-10)

    # Spectral floor: never suppress any bin below this fraction of its
    # original amplitude. Prevents the watery/underwater over-suppression
    # artifact. floor = 0.3 means at most 70% suppression per bin.
    gain_floor = max(0.05, 0.55 - strength * 0.15)
    wiener_gain = np.maximum(wiener_gain, gain_floor)

    # Smooth the gain mask slightly over time to avoid frame-boundary clicks
    wiener_gain = _uniform_filter1d(wiener_gain, size=3, axis=1)

    Zxx_clean = Zxx * wiener_gain
    _, reconstructed = istft(Zxx_clean, fs=sr, nperseg=nperseg)

    n = len(samples)
    if len(reconstructed) >= n:
        return reconstructed[:n].astype(np.float32)
    return np.pad(reconstructed, (0, n - len(reconstructed))).astype(np.float32)


class QwenTTSBackend(BaseFileTTSBackend):
    """Calls a locally running qwen_tts_server.py over HTTP."""

    provider_name = "qwen-tts"

    def __init__(self, cfg: ResolvedTTSConfig) -> None:
        self._cfg = cfg
        if not self._cfg.qwen_tts_endpoint:
            raise ConfigurationError(
                "SHANA_TTS_PROVIDER=qwen-tts requires qwen_tts_endpoint in the voice profile "
                "(e.g. http://127.0.0.1:9882/tts). Start the server with: "
                "python scripts/start_qwen_tts_server.py"
            )

    def synthesize(self, text: str, emotion: str | None = None) -> TTSResult:
        started_at = time.perf_counter()

        payload: dict[str, Any] = {"text": text}

        if self._cfg.qwen_tts_language:
            payload["language"] = self._cfg.qwen_tts_language

        if self._cfg.qwen_tts_reference_audio:
            ref_path = Path(self._cfg.qwen_tts_reference_audio)
            if not ref_path.is_absolute():
                ref_path = (settings.project_root / ref_path).resolve()
            payload["ref_audio_path"] = str(ref_path)

        if self._cfg.qwen_tts_reference_text:
            payload["ref_text"] = self._cfg.qwen_tts_reference_text

        if self._cfg.qwen_tts_speaker:
            payload["speaker"] = self._cfg.qwen_tts_speaker

        instruct = build_qwen_instruct(base_instruct=self._cfg.qwen_tts_instruct, emotion=emotion)
        if instruct:
            payload["instruct"] = instruct

        if self._cfg.qwen_tts_extra_json:
            payload["extra_params"] = self._cfg.qwen_tts_extra_json

        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self._cfg.qwen_tts_endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        path = self._target_path(".wav")
        try:
            with urllib.request.urlopen(request, timeout=self._cfg.qwen_tts_timeout_seconds) as response:
                audio_bytes = response.read()
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise ExternalServiceError(f"Qwen TTS request failed: HTTP {exc.code}: {details}") from exc
        except urllib.error.URLError as exc:
            raise ExternalServiceError(f"Qwen TTS request failed: {exc}") from exc

        self._validate_wav_payload(audio_bytes)
        path.write_bytes(audio_bytes)
        return TTSResult(
            provider=self.provider_name,
            text=text,
            audio_path=str(path),
            content_type="audio/wav",
            metadata={
                "endpoint": self._cfg.qwen_tts_endpoint,
                "reference_audio": self._cfg.qwen_tts_reference_audio,
                "reference_text": self._cfg.qwen_tts_reference_text,
                "language": self._cfg.qwen_tts_language,
                "speaker": self._cfg.qwen_tts_speaker,
                "emotion": emotion,
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
            raise ExternalServiceError(f"Qwen TTS returned invalid WAV data: {exc}") from exc
        if frame_count <= 0 or not frame_bytes:
            raise ExternalServiceError("Qwen TTS returned an empty WAV payload.")
        if sample_width == 1:
            has_signal = any(b != 128 for b in frame_bytes)
        else:
            has_signal = any(b != 0 for b in frame_bytes)
        if not has_signal:
            raise ExternalServiceError("Qwen TTS returned silent audio.")


class OpenAITTSBackend(BaseFileTTSBackend):
    provider_name = "openai"

    def __init__(self, cfg: ResolvedTTSConfig) -> None:
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
        started_at = time.perf_counter()
        prompt_text = text if not emotion else f"Emotion: {emotion}.\n\n{text}"
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


class GPTSoVITSTTSBackend(BaseFileTTSBackend):
    provider_name = "gpt-sovits"

    def __init__(self, cfg: ResolvedTTSConfig) -> None:
        self._cfg = cfg
        if not self._cfg.gpt_sovits_endpoint:
            raise ConfigurationError("SHANA_GPT_SOVITS_ENDPOINT is required for SHANA_TTS_PROVIDER=gpt-sovits.")

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Expand common abbreviations so SoVITS doesn't spell them out letter-by-letter."""
        import re
        _ABBREVS = {
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
        for pattern, replacement in _ABBREVS.items():
            text = re.sub(pattern, replacement, text)
        return text

    def synthesize(self, text: str, emotion: str | None = None) -> TTSResult:
        started_at = time.perf_counter()
        fmt = self._cfg.tts_format.lower()
        if fmt != "wav":
            raise ConfigurationError("GPT-SoVITS backend currently expects SHANA_TTS_FORMAT=wav.")

        payload: dict[str, Any] = {
            "text": self._normalize_text(text),
            "text_lang": self._cfg.gpt_sovits_text_lang,
        }
        if self._cfg.gpt_sovits_reference_audio:
            ref_path = Path(self._cfg.gpt_sovits_reference_audio)
            if not ref_path.is_absolute():
                ref_path = (settings.project_root / ref_path).resolve()
            payload["ref_audio_path"] = str(ref_path)
        if self._cfg.gpt_sovits_prompt_text:
            payload["prompt_text"] = self._cfg.gpt_sovits_prompt_text
        if self._cfg.gpt_sovits_prompt_lang:
            payload["prompt_lang"] = self._cfg.gpt_sovits_prompt_lang
        if emotion:
            payload["emotion"] = emotion
        if self._cfg.gpt_sovits_extra_json:
            payload.update(self._cfg.gpt_sovits_extra_json)

        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self._cfg.gpt_sovits_endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        path = self._target_path(".wav")
        try:
            with urllib.request.urlopen(request, timeout=self._cfg.gpt_sovits_timeout_seconds) as response:
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
                "endpoint": self._cfg.gpt_sovits_endpoint,
                "reference_audio": self._cfg.gpt_sovits_reference_audio,
                "prompt_text": self._cfg.gpt_sovits_prompt_text,
                "prompt_lang": self._cfg.gpt_sovits_prompt_lang,
                "text_lang": self._cfg.gpt_sovits_text_lang,
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

    def __init__(self, cfg: ResolvedTTSConfig) -> None:
        self._cfg = cfg
        executable = (self._cfg.piper_executable or "").strip()
        if not executable:
            raise ConfigurationError("SHANA_PIPER_EXE is required for SHANA_TTS_PROVIDER=piper.")
        self._executable = executable
        if not shutil.which(self._executable):
            raise ConfigurationError(
                f"Piper executable not found: {self._executable}. Set SHANA_PIPER_EXE to the Piper binary."
            )

        model_path = (self._cfg.piper_model_path or "").strip()
        if not model_path:
            raise ConfigurationError("SHANA_PIPER_MODEL_PATH is required for SHANA_TTS_PROVIDER=piper.")
        self._model_path = self._resolve_existing_path(
            model_path,
            env_name="SHANA_PIPER_MODEL_PATH",
        )

        config_path = (self._cfg.piper_config_path or "").strip()
        self._config_path = (
            self._resolve_existing_path(config_path, env_name="SHANA_PIPER_CONFIG_PATH")
            if config_path
            else None
        )
        self._speaker_id = (self._cfg.piper_speaker_id or "").strip() or None

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
