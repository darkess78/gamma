"""Qwen TTS backend: HTTP client for the local qwen_tts_server sidecar."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import wave
from io import BytesIO
from pathlib import Path
from typing import Any

from ..config import settings
from ..errors import ConfigurationError, ExternalServiceError
from .expressive_text import build_qwen_instruct
from .tts_base import BaseFileTTSBackend, TTSResult
from .voice_profiles import ResolvedTTSConfig


class QwenTTSBackend(BaseFileTTSBackend):
    """Calls a locally running qwen_tts_server.py over HTTP.
    
    Attributes:
        provider_name: 'qwen-tts'.
        _cfg: Configuration for Qwen TTS service.
    """

    provider_name = "qwen-tts"

    def __init__(self, cfg: ResolvedTTSConfig) -> None:
        """Initialize Qwen TTS backend.
        
        Args:
            cfg: ResolvedTTSConfig configuration.
            
        Raises:
            ConfigurationError: If qwen_tts_endpoint not configured.
        """
        self._cfg = cfg
        if not self._cfg.qwen_tts_endpoint:
            raise ConfigurationError(
                "SHANA_TTS_PROVIDER=qwen-tts requires qwen_tts_endpoint in the voice profile "
                "(e.g. http://127.0.0.1:9882/tts). Start the server with: "
                "python scripts/start_qwen_tts_server.py"
            )

    def synthesize(self, text: str, emotion: str | None = None, styles: list[str] | None = None) -> TTSResult:
        """Synthesize text via Qwen TTS HTTP API.
        
        Args:
            text: Text to synthesize.
            emotion: Optional emotion style.
            styles: Optional list of voice styles.
            
        Returns:
            TTSResult: Generated audio.
        """
        started_at = time.perf_counter()
        payload: dict[str, Any] = {"text": self._normalize_text(text)}

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

        instruct = build_qwen_instruct(base_instruct=self._cfg.qwen_tts_instruct, emotion=emotion, styles=styles)
        if instruct:
            payload["instruct"] = instruct

        extra_params = self._extra_params_for_emotion(emotion, styles=styles)
        if extra_params:
            payload["extra_params"] = extra_params

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
                "styles": styles or [],
                "speed": extra_params.get("speed") if extra_params else None,
                "output_peak": extra_params.get("output_peak") if extra_params else None,
                "timings_ms": {"backend_ms": round((time.perf_counter() - started_at) * 1000, 1)},
            },
        )

    def _extra_params_for_emotion(self, emotion: str | None, styles: list[str] | None = None) -> dict[str, Any]:
        """Get extra params for emotion-based styling.
        
        Args:
            emotion: Optional emotion string.
            styles: Optional voice styles list.
            
        Returns:
            dict[str, Any]: Extra params dict.
        """
        extra = dict(self._cfg.qwen_tts_extra_json or {})
        speed_by_emotion = extra.pop("speed_by_emotion", None)
        if isinstance(speed_by_emotion, dict):
            speed = self._select_emotion_speed(speed_by_emotion, emotion)
            if speed is not None:
                extra["speed"] = speed
        output_peak_by_emotion = extra.pop("output_peak_by_emotion", None)
        if isinstance(output_peak_by_emotion, dict):
            output_peak = self._select_emotion_output_peak(output_peak_by_emotion, emotion)
            if output_peak is not None:
                extra["output_peak"] = output_peak
        self._apply_voice_styles(extra, styles or [])
        return extra

    def _apply_voice_styles(self, extra: dict[str, Any], styles: list[str]) -> None:
        """Apply voice styles to extra params.
        
        Args:
            extra: Extra params dict to modify.
            styles: Styles to apply.
        """
        if not styles:
            return
        speed = self._float_param(extra.get("speed"), default=0.82)
        output_peak = self._float_param(extra.get("output_peak"), default=0.64)
        for style in styles:
            if style == "soft":
                speed -= 0.01
                output_peak -= 0.05
            elif style == "quiet":
                output_peak -= 0.08
            elif style == "firm":
                speed += 0.01
                output_peak += 0.06
            elif style == "fast":
                speed += 0.05
            elif style == "slow":
                speed -= 0.05
            elif style == "bright":
                speed += 0.02
                output_peak += 0.03
            elif style == "deadpan":
                speed -= 0.01
                output_peak -= 0.03
        extra["speed"] = max(0.74, min(0.94, speed))
        extra["output_peak"] = max(0.48, min(0.78, output_peak))

    def _float_param(self, value: Any, *, default: float) -> float:
        """Convert value to float or return default.
        
        Args:
            value: Value to convert.
            default: Default if conversion fails.
            
        Returns:
            float: Converted value or default.
        """
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _select_emotion_speed(self, speed_by_emotion: dict[Any, Any], emotion: str | None) -> float | None:
        """Select emotion-based speed.
        
        Args:
            speed_by_emotion: Emotion to speed mapping.
            emotion: Current emotion.
            
        Returns:
            float | None: Speed for emotion or None.
        """
        keys = []
        if emotion:
            keys.append(str(emotion).strip().lower())
        keys.extend(["default", "neutral"])
        for key in keys:
            if key not in speed_by_emotion:
                continue
            try:
                speed = float(speed_by_emotion[key])
            except (TypeError, ValueError):
                continue
            return max(0.75, min(1.2, speed))
        return None

    def _select_emotion_output_peak(self, output_peak_by_emotion: dict[Any, Any], emotion: str | None) -> float | None:
        """Select emotion-based output peak.
        
        Args:
            output_peak_by_emotion: Emotion to output peak mapping.
            emotion: Current emotion.
            
        Returns:
            float | None: Output peak for emotion or None.
        """
        keys = []
        if emotion:
            keys.append(str(emotion).strip().lower())
        keys.extend(["default", "neutral"])
        for key in keys:
            if key not in output_peak_by_emotion:
                continue
            try:
                output_peak = float(output_peak_by_emotion[key])
            except (TypeError, ValueError):
                continue
            return max(0.48, min(0.78, output_peak))
        return None

    def _validate_wav_payload(self, audio_bytes: bytes) -> None:
        """Validate WAV payload from Qwen backend.
        
        Args:
            audio_bytes: WAV bytes.
            
        Raises:
            ExternalServiceError: If invalid, empty, or silent WAV.
        """
        try:
            with wave.open(BytesIO(audio_bytes), "rb") as wav_file:
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
