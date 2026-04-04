from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from ..config import settings
from ..conversation.service import ConversationService
from .stt import STTService


class MissingBinaryError(RuntimeError):
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
    device: str | None = None
    sample_rate: int = 16_000
    keep_inputs: bool = False
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


class AudioCaptureBackend:
    def capture_once(self, *, seconds: int, sample_rate: int, device: str | None = None) -> Path:
        raise NotImplementedError


class ArecordCaptureBackend(AudioCaptureBackend):
    def __init__(self, binary: str = "arecord") -> None:
        self._binary = binary

    def capture_once(self, *, seconds: int, sample_rate: int, device: str | None = None) -> Path:
        arecord = self._require_binary(self._binary)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        capture_path = settings.audio_output_dir / f"mic-input-{stamp}.wav"
        command = [
            arecord,
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


class PowerShellCaptureBackend(AudioCaptureBackend):
    def __init__(self, binary: str = "powershell") -> None:
        self._binary = binary

    def capture_once(self, *, seconds: int, sample_rate: int, device: str | None = None) -> Path:
        del device  # not yet supported in the simple Windows capture path
        powershell = shutil.which(self._binary) or shutil.which("pwsh")
        if not powershell:
            raise MissingBinaryError("required binary not found: powershell or pwsh")

        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        capture_path = settings.audio_output_dir / f"mic-input-{stamp}.wav"
        ps_path = str(capture_path).replace("'", "''")
        script = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Windows.Extensions
$path = '{ps_path}'
$durationMs = {max(1, seconds) * 1000}
$sampleRate = {sample_rate}
$channels = 1
$bitsPerSample = 16
$format = [System.Windows.Media.AudioFormat]::Pcm
$capture = New-Object System.Windows.Media.CaptureSource
$settings = New-Object System.Windows.Media.AudioCaptureDeviceSettings
$settings.AudioFrameSize = [System.TimeSpan]::FromMilliseconds(100)
$capture.AudioCaptureDevice = [System.Windows.Media.CaptureDeviceConfiguration]::GetDefaultAudioCaptureDevice()
if ($null -eq $capture.AudioCaptureDevice) {{ throw 'No default audio capture device found.' }}
$writer = New-Object System.Windows.Media.WaveMediaStreamSource($capture, $sampleRate, $channels)
$file = [System.IO.File]::Create($path)
$writer.Open($file)
$capture.Start()
Start-Sleep -Milliseconds $durationMs
$capture.Stop()
$writer.Close()
$file.Close()
""".strip()
        try:
            subprocess.run([powershell, "-NoProfile", "-Command", script], check=True)
        except subprocess.CalledProcessError as exc:
            raise MissingBinaryError(
                "Windows microphone capture failed. Gamma currently expects PowerShell with .NET audio support; "
                "if that is not available, use the file-based STT/voice roundtrip path instead."
            ) from exc
        return capture_path


class AudioPlayback:
    def play(self, audio_path: str | None) -> bool:
        if not audio_path:
            return False
        player = self._pick_player()
        if player is None:
            print(f"[warn] no supported audio playback command found; synthesized audio saved at {audio_path}")
            return False
        return player(audio_path)

    def _pick_player(self):
        if os.name == "nt":
            powershell = shutil.which("powershell") or shutil.which("pwsh")
            if powershell:
                return lambda audio_path: self._play_with_powershell(powershell, audio_path)
            return None

        for binary in ("aplay", "ffplay", "play"):
            path = shutil.which(binary)
            if path:
                return lambda audio_path, binary=path: self._play_with_binary(binary, audio_path)
        return None

    def _play_with_binary(self, binary: str, audio_path: str) -> bool:
        command = [binary, audio_path]
        if Path(binary).name == "ffplay":
            command = [binary, "-nodisp", "-autoexit", "-loglevel", "error", audio_path]
        subprocess.run(command, check=False)
        return True

    def _play_with_powershell(self, powershell: str, audio_path: str) -> bool:
        ps_path = str(audio_path).replace("'", "''")
        script = (
            "Add-Type -AssemblyName System; "
            f"$player = New-Object System.Media.SoundPlayer '{ps_path}'; "
            "$player.PlaySync();"
        )
        subprocess.run([powershell, "-NoProfile", "-Command", script], check=False)
        return True


class VoiceModeController:
    def __init__(
        self,
        *,
        stt: STTService | None = None,
        conversation: ConversationService | None = None,
        capture_backend: AudioCaptureBackend | None = None,
        playback: AudioPlayback | None = None,
    ) -> None:
        self._stt = stt or STTService()
        self._conversation = conversation or ConversationService()
        self._capture = capture_backend or self._default_capture_backend()
        self._playback = playback or AudioPlayback()

    def _default_capture_backend(self) -> AudioCaptureBackend:
        if os.name == "nt":
            return PowerShellCaptureBackend()
        return ArecordCaptureBackend()

    def run_turn(self, config: VoiceLoopConfig) -> VoiceTurnResult:
        policy = self.policy_for(config.mode)
        effective_mode = self._resolve_runtime_mode(config.mode)
        effective_policy = self.policy_for(effective_mode)

        if effective_mode != config.mode:
            print(
                f"[info] mode '{config.mode}' currently falls back to '{effective_mode}' until live streaming / VAD is implemented."
            )

        capture_path = self._capture.capture_once(
            seconds=config.record_seconds,
            sample_rate=config.sample_rate,
            device=config.device,
        )

        try:
            transcript = self._stt.transcribe_audio(str(capture_path)).strip()
            if not transcript:
                return VoiceTurnResult(
                    mode=str(effective_mode),
                    policy=asdict(effective_policy),
                    input_audio=str(capture_path),
                    transcript="",
                    reply_text="",
                    reply_audio=None,
                    playback_attempted=False,
                    metadata={"empty_transcript": True, "requested_mode": str(config.mode)},
                )

            response = self._conversation.respond(transcript, synthesize_speech=True)
            playback_attempted = False
            if config.playback_enabled and response.audio_path:
                playback_attempted = self._playback.play(response.audio_path)

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
                },
            )
        finally:
            if not config.keep_inputs:
                capture_path.unlink(missing_ok=True)

    def run_cli_loop(self, config: VoiceLoopConfig) -> None:
        policy = self.policy_for(config.mode)
        print(f"gamma voice controller. mode={config.mode} | {policy.description}")
        if config.mode != VoiceMode.TURN_BASED:
            print("[info] non-turn modes are scaffolded now and currently execute through the validated turn-based audio path.")
        if os.name == "nt":
            print("[info] Windows mic capture uses a PowerShell-based fallback path for now.")
        print("press Enter to record, or type 'quit' to stop.")
        while True:
            prompt = config.input_prompt or "voice> "
            command = input(prompt).strip().lower()
            if command in {"quit", "exit", "q"}:
                break
            result = self.run_turn(config)
            if not result.transcript:
                print("[warn] empty transcription")
                print(result.to_json())
                continue
            print(f"you> {result.transcript}")
            print(f"assistant> {result.reply_text}")
            print(result.to_json())
            if not self.policy_for(VoiceMode(result.mode)).auto_rearm_after_reply:
                continue

    def policy_for(self, mode: VoiceMode) -> VoiceModePolicy:
        return VOICE_MODE_POLICIES[mode]

    def _resolve_runtime_mode(self, requested: VoiceMode) -> VoiceMode:
        if requested in {VoiceMode.STREAMING, VoiceMode.INTERRUPTIBLE}:
            return VoiceMode.TURN_BASED
        return requested
