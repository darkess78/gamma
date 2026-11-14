"""Optional post-processing of synthesized WAV: spectral denoise and RVC voice conversion."""
from __future__ import annotations

import subprocess
import time
import wave
from pathlib import Path
from typing import Any

from ..errors import ConfigurationError, ExternalServiceError
from .rvc_support import resolve_rvc_index_path, resolve_rvc_model_path, resolve_rvc_project_root, resolve_rvc_python
from .tts_base import TTSResult, validate_wav_file


try:
    import numpy as np
    from scipy.signal import stft, istft
    from scipy.ndimage import uniform_filter1d as _uniform_filter1d
    _SCIPY_AVAILABLE = True
except ImportError:
    _SCIPY_AVAILABLE = False


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


class TTSPostProcessingMixin:
    """Optional post-processing of synthesized WAV: spectral denoise and RVC voice conversion."""

    def _maybe_apply_denoise(self, result: TTSResult) -> TTSResult:
        """Apply spectral denoising if enabled.
        
        Args:
            result: TTSResult to process.
            
        Returns:
            TTSResult: Denoised result or original.
        """
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
        """Apply RVC voice conversion if enabled.
        
        Args:
            result: TTSResult to process.
            emotion: Emotion for RVC.
            
        Returns:
            TTSResult: RVC-processed result or original.
        """
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

        validate_wav_file(converted_path, provider_name="RVC")
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
