from __future__ import annotations

import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

from ..config import settings


@dataclass(frozen=True, slots=True)
class NormalizedAudio:
    samples: tuple[float, ...]
    sample_rate: int
    source_path: Path
    decoder: str

    @property
    def duration_seconds(self) -> float:
        if self.sample_rate <= 0:
            return 0.0
        return len(self.samples) / float(self.sample_rate)


class AudioDecodeError(RuntimeError):
    pass


class AudioInputDecoder:
    """Decode supported voice uploads to bounded mono 16 kHz float PCM."""

    sample_rate = 16_000

    def __init__(
        self,
        *,
        ffmpeg_executable: str | None = None,
        max_seconds: int | None = None,
        timeout_ms: int | None = None,
    ) -> None:
        self._ffmpeg = ffmpeg_executable or shutil.which("ffmpeg")
        self._max_seconds = max(1, max_seconds or settings.audio_analysis_max_seconds)
        self._timeout_seconds = max(0.1, (timeout_ms or settings.audio_analysis_timeout_ms) / 1000.0)

    def decode_path(self, path: Path | str) -> NormalizedAudio:
        audio_path = Path(path)
        if not audio_path.exists():
            raise AudioDecodeError(f"audio file not found: {audio_path}")

        if self._ffmpeg:
            try:
                return self._decode_with_ffmpeg(audio_path)
            except AudioDecodeError:
                if audio_path.suffix.lower() not in {".wav", ".wave"}:
                    raise

        return self._decode_wav(audio_path)

    def _decode_with_ffmpeg(self, path: Path) -> NormalizedAudio:
        command = [
            self._ffmpeg or "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-t",
            str(self._max_seconds),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(self.sample_rate),
            "-f",
            "s16le",
            "pipe:1",
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                check=False,
                timeout=self._timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise AudioDecodeError(f"audio decode timed out after {self._timeout_seconds:.1f}s") from exc
        except OSError as exc:
            raise AudioDecodeError(f"audio decoder failed to start: {exc}") from exc

        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise AudioDecodeError(f"ffmpeg audio decode failed: {detail or f'exit {result.returncode}'}")
        samples = self._s16le_samples(result.stdout)
        if not samples:
            raise AudioDecodeError("audio decode returned no samples")
        return NormalizedAudio(
            samples=tuple(samples),
            sample_rate=self.sample_rate,
            source_path=path,
            decoder="ffmpeg",
        )

    def _decode_wav(self, path: Path) -> NormalizedAudio:
        try:
            with wave.open(str(path), "rb") as wav_file:
                channels = wav_file.getnchannels()
                sample_width = wav_file.getsampwidth()
                source_rate = wav_file.getframerate()
                max_frames = max(1, int(source_rate * self._max_seconds))
                raw = wav_file.readframes(min(wav_file.getnframes(), max_frames))
        except Exception as exc:
            raise AudioDecodeError(f"native WAV decode failed: {exc}") from exc

        source_samples = self._pcm_samples(raw, sample_width=sample_width, channels=channels)
        if not source_samples or source_rate <= 0:
            raise AudioDecodeError("native WAV decode returned no samples")
        samples = self._resample(source_samples, source_rate=source_rate, target_rate=self.sample_rate)
        return NormalizedAudio(
            samples=tuple(samples),
            sample_rate=self.sample_rate,
            source_path=path,
            decoder="wave",
        )

    @staticmethod
    def _s16le_samples(raw: bytes) -> list[float]:
        return [
            int.from_bytes(raw[offset:offset + 2], byteorder="little", signed=True) / 32768.0
            for offset in range(0, len(raw) - 1, 2)
        ]

    @staticmethod
    def _pcm_samples(raw: bytes, *, sample_width: int, channels: int) -> list[float]:
        if sample_width not in {1, 2, 3, 4} or channels <= 0:
            return []
        frame_width = sample_width * channels
        max_abs = float(128 if sample_width == 1 else 2 ** (8 * sample_width - 1))
        samples: list[float] = []
        for offset in range(0, len(raw) - frame_width + 1, frame_width):
            channel_values: list[float] = []
            for channel in range(channels):
                start = offset + channel * sample_width
                chunk = raw[start:start + sample_width]
                if sample_width == 1:
                    value = int(chunk[0]) - 128
                else:
                    value = int.from_bytes(chunk, byteorder="little", signed=True)
                channel_values.append(value / max_abs)
            samples.append(sum(channel_values) / len(channel_values))
        return samples

    @staticmethod
    def _resample(samples: list[float], *, source_rate: int, target_rate: int) -> list[float]:
        if source_rate == target_rate:
            return samples
        target_count = max(1, round(len(samples) * target_rate / source_rate))
        if target_count == 1 or len(samples) == 1:
            return [samples[0]]
        scale = (len(samples) - 1) / float(target_count - 1)
        output: list[float] = []
        for index in range(target_count):
            source_position = index * scale
            lower = int(source_position)
            upper = min(lower + 1, len(samples) - 1)
            fraction = source_position - lower
            output.append(samples[lower] * (1.0 - fraction) + samples[upper] * fraction)
        return output
