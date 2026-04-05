from __future__ import annotations

import json
import platform
import shutil
import subprocess
import wave
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from time import perf_counter
from typing import Any

from ..config import settings
from ..conversation.service import ConversationService
from .stt import STTService


class AudioBackendError(RuntimeError):
    pass


class MissingBinaryError(AudioBackendError):
    pass


class VoiceMode(StrEnum):
    TURN_BASED = "turn-based"
    STREAMING = "streaming"
    ALWAYS_LISTENING = "always-listening"
    INTERRUPTIBLE = "interruptible"


@dataclass(slots=True)
class VoiceModePolicy:
    mode: VoiceMode
    description: str
    requires_streaming_input: bool = False
    allows_background_listening: bool = False
    supports_barge_in: bool = False
    half_duplex: bool = True
    auto_rearm_after_reply: bool = False


VOICE_MODE_POLICIES: dict[VoiceMode, VoiceModePolicy] = {
    VoiceMode.TURN_BASED: VoiceModePolicy(
        mode=VoiceMode.TURN_BASED,
        description="Explicit press-enter / record / respond turns using the validated file STT/TTS path.",
        half_duplex=True,
        auto_rearm_after_reply=False,
    ),
    VoiceMode.STREAMING: VoiceModePolicy(
        mode=VoiceMode.STREAMING,
        description="Target architecture for continuous low-latency duplex audio with partial transcripts.",
        requires_streaming_input=True,
        half_duplex=False,
        auto_rearm_after_reply=True,
    ),
    VoiceMode.ALWAYS_LISTENING: VoiceModePolicy(
        mode=VoiceMode.ALWAYS_LISTENING,
        description="Wake-and-hold listener that continuously rearms after each completed turn.",
        allows_background_listening=True,
        auto_rearm_after_reply=True,
    ),
    VoiceMode.INTERRUPTIBLE: VoiceModePolicy(
        mode=VoiceMode.INTERRUPTIBLE,
        description="Playback can be interrupted by new user speech; ideal end-state for barge-in.",
        allows_background_listening=True,
        supports_barge_in=True,
        half_duplex=False,
        auto_rearm_after_reply=True,
        requires_streaming_input=True,
    ),
}


@dataclass(slots=True)
class VoiceLoopConfig:
    mode: VoiceMode = VoiceMode.TURN_BASED
    record_seconds: int = 6
    max_record_seconds: int = 20
    silence_stop_seconds: float = 1.2
    speech_threshold: float = 0.015
    speech_start_seconds: float = 0.3
    device: str | None = None
    sample_rate: int = 16_000
    keep_inputs: bool = False
    synthesize_speech: bool = True
    playback_enabled: bool = True
    input_prompt: str | None = None


@dataclass(slots=True)
class VoiceTurnResult:
    mode: str
    policy: dict[str, Any]
    input_audio: str
    transcript: str
    reply_text: str
    reply_audio: str | None
    playback_attempted: bool
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


class ArecordCaptureBackend:
    def __init__(self, binary: str = "arecord") -> None:
        self._binary = self._require_binary(binary)

    def capture_once(self, *, seconds: int, sample_rate: int, device: str | None = None) -> Path:
        capture_path = _new_capture_path()
        command = [
            self._binary,
            "-q",
            "-d",
            str(seconds),
            "-f",
            "S16_LE",
            "-r",
            str(sample_rate),
            "-c",
            "1",
            str(capture_path),
        ]
        if device:
            command[1:1] = ["-D", device]
        subprocess.run(command, check=True)
        return capture_path

    def _require_binary(self, name: str) -> str:
        path = shutil.which(name)
        if not path:
            raise MissingBinaryError(f"required binary not found: {name}")
        return path


class SoundDeviceCaptureBackend:
    def __init__(self) -> None:
        try:
            import sounddevice
        except Exception as exc:
            raise MissingBinaryError(
                "sounddevice is required for microphone capture on this platform. "
                "Reinstall the project dependencies and try again."
            ) from exc
        self._sounddevice = sounddevice

    def capture_once(self, *, seconds: int, sample_rate: int, device: str | None = None) -> Path:
        capture_path = _new_capture_path()
        try:
            frame_count = max(int(seconds * sample_rate), 1)
            recording = self._sounddevice.rec(
                frame_count,
                samplerate=sample_rate,
                channels=1,
                dtype="int16",
                device=_normalize_device(device),
            )
            self._sounddevice.wait()
        except Exception as exc:
            raise AudioBackendError(f"microphone capture failed: {exc}") from exc

        _write_wave_capture(capture_path, sample_rate=sample_rate, audio_bytes=recording.tobytes())
        return capture_path

    def capture_phrase(
        self,
        *,
        sample_rate: int,
        device: str | None = None,
        max_record_seconds: int = 20,
        silence_stop_seconds: float = 1.2,
        speech_threshold: float = 0.015,
        speech_start_seconds: float = 0.3,
    ) -> Path:
        capture_path = _new_capture_path()
        chunk_seconds = 0.2
        chunk_frames = max(int(sample_rate * chunk_seconds), 1)
        max_chunks = max(int(max_record_seconds / chunk_seconds), 1)
        silence_chunks_to_stop = max(int(silence_stop_seconds / chunk_seconds), 1)
        speech_chunks_to_start = max(int(speech_start_seconds / chunk_seconds), 1)
        pre_roll_chunks = 3
        ambient_window_chunks = 8

        collected_chunks: list[bytes] = []
        buffered_chunks: deque[bytes] = deque(maxlen=pre_roll_chunks)
        ambient_peaks: deque[float] = deque(maxlen=ambient_window_chunks)
        speech_started = False
        silent_chunks = 0
        voiced_chunks = 0
        active_threshold = speech_threshold

        try:
            with self._sounddevice.InputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="int16",
                blocksize=chunk_frames,
                device=_normalize_device(device),
            ) as stream:
                while True:
                    block, _overflowed = stream.read(chunk_frames)
                    block_bytes = block.tobytes()
                    block_peak = _chunk_peak(block)

                    if speech_started:
                        collected_chunks.append(block_bytes)
                        if block_peak >= active_threshold:
                            silent_chunks = 0
                        else:
                            silent_chunks += 1
                        if silent_chunks >= silence_chunks_to_stop or len(collected_chunks) >= max_chunks:
                            break
                        continue

                    ambient_peaks.append(block_peak)
                    dynamic_threshold = _dynamic_speech_threshold(
                        ambient_peaks,
                        minimum_threshold=speech_threshold,
                    )
                    buffered_chunks.append(block_bytes)
                    if block_peak >= dynamic_threshold:
                        voiced_chunks += 1
                    else:
                        voiced_chunks = 0

                    if voiced_chunks >= speech_chunks_to_start:
                        speech_started = True
                        active_threshold = dynamic_threshold
                        collected_chunks.extend(buffered_chunks)
                        buffered_chunks.clear()
                        silent_chunks = 0
        except Exception as exc:
            raise AudioBackendError(f"microphone capture failed: {exc}") from exc

        _write_wave_capture(capture_path, sample_rate=sample_rate, audio_bytes=b"".join(collected_chunks))
        return capture_path


class AudioPlayback:
    def __init__(self, binary: str | None = None) -> None:
        self._system = platform.system()
        self._binary = binary or self._default_binary()

    def play(self, audio_path: str | None) -> bool:
        if not audio_path:
            return False
        if self._system == "Windows":
            return self._play_windows(audio_path)
        return self._play_with_binary(audio_path)

    def _default_binary(self) -> str | None:
        if self._system == "Darwin":
            return "afplay"
        if self._system == "Windows":
            return None
        return "aplay"

    def _play_windows(self, audio_path: str) -> bool:
        if Path(audio_path).suffix.lower() != ".wav":
            print(f"[warn] Windows playback currently supports WAV files only; synthesized audio saved at {audio_path}")
            return False
        try:
            import winsound

            winsound.PlaySound(audio_path, winsound.SND_FILENAME)
        except Exception as exc:
            print(f"[warn] Windows audio playback failed ({exc}); synthesized audio saved at {audio_path}")
            return False
        return True

    def _play_with_binary(self, audio_path: str) -> bool:
        if not self._binary:
            return False
        player = shutil.which(self._binary)
        if not player:
            print(f"[warn] {self._binary} not found; synthesized audio saved at {audio_path}")
            return False
        subprocess.run([player, audio_path], check=False)
        return True


class VoiceModeController:
    def __init__(
        self,
        *,
        stt: STTService | None = None,
        conversation: ConversationService | None = None,
        capture_backend: ArecordCaptureBackend | SoundDeviceCaptureBackend | None = None,
        playback: AudioPlayback | None = None,
    ) -> None:
        self._stt = stt or STTService()
        self._conversation = conversation or ConversationService()
        self._capture = capture_backend or _build_capture_backend()
        self._playback = playback or AudioPlayback()

    def run_turn(self, config: VoiceLoopConfig) -> VoiceTurnResult:
        turn_started_at = perf_counter()
        policy = self.policy_for(config.mode)
        effective_mode = self._resolve_runtime_mode(config.mode)
        effective_policy = self.policy_for(effective_mode)

        if effective_mode != config.mode:
            print(
                f"[info] mode '{config.mode}' currently falls back to '{effective_mode}' until live streaming / VAD is implemented."
            )

        capture_started_at = perf_counter()
        capture_path = self._capture_audio(config)
        capture_seconds = perf_counter() - capture_started_at

        try:
            stt_started_at = perf_counter()
            transcript = self._stt.transcribe_audio(str(capture_path)).strip()
            stt_seconds = perf_counter() - stt_started_at
            if not transcript:
                return VoiceTurnResult(
                    mode=str(effective_mode),
                    policy=asdict(effective_policy),
                    input_audio=str(capture_path),
                    transcript="",
                    reply_text="",
                    reply_audio=None,
                    playback_attempted=False,
                    metadata={
                        "empty_transcript": True,
                        "requested_mode": str(config.mode),
                        "timings": {
                            "capture_seconds": capture_seconds,
                            "stt_seconds": stt_seconds,
                            "turn_seconds": perf_counter() - turn_started_at,
                        },
                    },
                )

            response_started_at = perf_counter()
            response = self._conversation.respond(transcript, synthesize_speech=config.synthesize_speech)
            response_seconds = perf_counter() - response_started_at
            playback_attempted = False
            playback_seconds = 0.0
            if config.playback_enabled and response.audio_path:
                playback_started_at = perf_counter()
                playback_attempted = self._playback.play(response.audio_path)
                playback_seconds = perf_counter() - playback_started_at

            return VoiceTurnResult(
                mode=str(effective_mode),
                policy=asdict(effective_policy),
                input_audio=str(capture_path),
                transcript=transcript,
                reply_text=response.spoken_text,
                reply_audio=response.audio_path,
                playback_attempted=playback_attempted,
                metadata={
                    "requested_mode": str(config.mode),
                    "requested_policy": asdict(policy),
                    "auto_rearm_after_reply": effective_policy.auto_rearm_after_reply,
                    "supports_true_barge_in": effective_policy.supports_barge_in and effective_mode == config.mode,
                    "timings": {
                        "capture_seconds": capture_seconds,
                        "stt_seconds": stt_seconds,
                        "response_seconds": response_seconds,
                        "playback_seconds": playback_seconds,
                        "turn_seconds": perf_counter() - turn_started_at,
                    },
                },
            )
        finally:
            if not config.keep_inputs:
                capture_path.unlink(missing_ok=True)

    def run_cli_loop(self, config: VoiceLoopConfig) -> None:
        policy = self.policy_for(config.mode)
        print(f"gamma voice controller. mode={config.mode} | {policy.description}")
        prompt = config.input_prompt or "voice> "
        if policy.auto_rearm_after_reply:
            print("[info] continuous loop active. Speak naturally; recording stops after trailing silence. Say 'stop listening' or 'quit' to exit.")
            try:
                while True:
                    if not self._run_and_print_turn(config):
                        break
            except KeyboardInterrupt:
                print("\n[info] stopping voice controller.")
            return

        print("press Enter to record, or type 'quit' to stop.")
        while True:
            command = input(prompt).strip().lower()
            if command in {"quit", "exit", "q"}:
                break
            self._run_and_print_turn(config)

    def policy_for(self, mode: VoiceMode) -> VoiceModePolicy:
        return VOICE_MODE_POLICIES[mode]

    def _resolve_runtime_mode(self, requested: VoiceMode) -> VoiceMode:
        if requested in {VoiceMode.STREAMING, VoiceMode.INTERRUPTIBLE}:
            return VoiceMode.TURN_BASED
        return requested

    def _run_and_print_turn(self, config: VoiceLoopConfig) -> bool:
        result = self.run_turn(config)
        if not result.transcript:
            print("[warn] empty transcription")
            print(_format_timing_summary(result.metadata.get("timings")))
            print(result.to_json())
            return True
        if _is_stop_command(result.transcript):
            print(f"you> {result.transcript}")
            print("[info] stopping voice controller.")
            return False
        print(f"you> {result.transcript}")
        print(f"assistant> {result.reply_text}")
        print(_format_timing_summary(result.metadata.get("timings")))
        print(result.to_json())
        return True

    def _capture_audio(self, config: VoiceLoopConfig) -> Path:
        effective_mode = self._resolve_runtime_mode(config.mode)
        if effective_mode == VoiceMode.ALWAYS_LISTENING and hasattr(self._capture, "capture_phrase"):
            return self._capture.capture_phrase(
                sample_rate=config.sample_rate,
                device=config.device,
                max_record_seconds=config.max_record_seconds,
                silence_stop_seconds=config.silence_stop_seconds,
                speech_threshold=config.speech_threshold,
                speech_start_seconds=config.speech_start_seconds,
            )
        return self._capture.capture_once(
            seconds=config.record_seconds,
            sample_rate=config.sample_rate,
            device=config.device,
        )


def _build_capture_backend() -> ArecordCaptureBackend | SoundDeviceCaptureBackend:
    if platform.system() == "Windows":
        return SoundDeviceCaptureBackend()
    try:
        return ArecordCaptureBackend()
    except MissingBinaryError as arecord_error:
        try:
            return SoundDeviceCaptureBackend()
        except MissingBinaryError:
            raise arecord_error


def _normalize_device(device: str | None) -> str | int | None:
    if device is None:
        return None
    normalized = device.strip()
    if not normalized:
        return None
    if normalized.isdigit():
        return int(normalized)
    return normalized


def available_recording_devices() -> list[str]:
    try:
        import sounddevice
    except Exception:
        return []

    default_input, _default_output = sounddevice.default.device
    devices: list[str] = []
    for index, info in enumerate(sounddevice.query_devices()):
        max_inputs = int(info.get("max_input_channels", 0))
        if max_inputs <= 0:
            continue
        marker = " (default)" if index == default_input else ""
        devices.append(f"{index}: {info['name']}{marker}")
    return devices


def _new_capture_path() -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return settings.audio_output_dir / f"mic-input-{stamp}.wav"


def _write_wave_capture(path: Path, *, sample_rate: int, audio_bytes: bytes) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(audio_bytes)


def _chunk_peak(block: Any) -> float:
    peak = 0
    for sample in block.flat:
        value = abs(int(sample))
        if value > peak:
            peak = value
    return peak / 32768.0


def _dynamic_speech_threshold(ambient_peaks: deque[float], *, minimum_threshold: float) -> float:
    if not ambient_peaks:
        return minimum_threshold
    ambient_floor = max(ambient_peaks)
    return max(minimum_threshold, ambient_floor * 2.5 + 0.008)


def _is_stop_command(transcript: str) -> bool:
    normalized = " ".join(transcript.lower().strip().split())
    return normalized in {"quit", "exit", "stop listening", "stop recording", "stop"}


def _format_timing_summary(timings: Any) -> str:
    if not isinstance(timings, dict):
        return "[timing] unavailable"

    labels = [
        ("capture", "capture_seconds"),
        ("stt", "stt_seconds"),
        ("response", "response_seconds"),
        ("playback", "playback_seconds"),
        ("total", "turn_seconds"),
    ]
    parts: list[str] = []
    for label, key in labels:
        value = timings.get(key)
        if isinstance(value, (int, float)):
            parts.append(f"{label}={value:.2f}s")
    if not parts:
        return "[timing] unavailable"
    return "[timing] " + " | ".join(parts)
