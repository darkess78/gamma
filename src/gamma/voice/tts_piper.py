"""Piper TTS backend: runs the local piper binary."""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from ..config import settings
from ..errors import ConfigurationError, ExternalServiceError
from .tts_base import BaseFileTTSBackend, TTSResult, validate_wav_file
from .voice_profiles import ResolvedTTSConfig


class PiperTTSBackend(BaseFileTTSBackend):
    """Piper TTS binary backend.
    
    Attributes:
        provider_name: 'piper'.
        _executable: Piper binary path.
        _model_path: Model file path.
        _config_path: Optional config path.
        _speaker_id: Optional speaker ID.
    """

    provider_name = "piper"

    def __init__(self, cfg: ResolvedTTSConfig) -> None:
        """Initialize Piper TTS backend.
        
        Args:
            cfg: ResolvedTTSConfig configuration.
            
        Raises:
            ConfigurationError: If executable or model path not found.
        """
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
        """Synthesize text using Piper binary.
        
        Args:
            text: Text to synthesize.
            emotion: Optional emotion style.
            
        Returns:
            TTSResult: Generated audio.
        """
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
        """Resolve existing path from environment.
        
        Args:
            raw_path: Path string.
            env_name: Environment variable name for resolution.
            
        Returns:
            Path: Resolved existing path.
            
        Raises:
            ConfigurationError: If path does not exist.
        """
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = settings.project_root / path
        path = path.resolve()
        if not path.exists():
            raise ConfigurationError(f"{env_name} does not exist: {path}")
        return path

    def _validate_wav_file(self, path: Path) -> None:
        """Validate WAV output from Piper.
        
        Args:
            path: WAV file path.
            
        Raises:
            ExternalServiceError: If invalid WAV file.
        """
        validate_wav_file(path, provider_name="Piper")
