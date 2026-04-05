from __future__ import annotations

import argparse
import subprocess

from .voice.controller import AudioBackendError, MissingBinaryError, VoiceLoopConfig, VoiceMode, VoiceModeController, available_recording_devices


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Gamma with a voice mode controller.")
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in VoiceMode],
        default=VoiceMode.TURN_BASED.value,
        help="Voice interaction mode. Non-turn modes are scaffolded and currently fall back to the validated turn-based path.",
    )
    parser.add_argument("--seconds", type=int, default=6, help="Recording length per turn.")
    parser.add_argument("--max-seconds", type=int, default=20, help="Maximum utterance length for always-listening mode.")
    parser.add_argument("--silence-stop", type=float, default=1.2, help="Stop an always-listening utterance after this many seconds of trailing silence.")
    parser.add_argument("--speech-threshold", type=float, default=0.015, help="Minimum normalized input level to treat as speech.")
    parser.add_argument("--speech-start", type=float, default=0.3, help="How long speech must persist before an always-listening utterance starts.")
    parser.add_argument(
        "--device",
        help="Optional input device. Use a sounddevice name or index on Windows, or an ALSA device such as hw:1,0 on Linux.",
    )
    parser.add_argument("--rate", type=int, default=16000, help="Recording sample rate.")
    parser.add_argument("--keep-inputs", action="store_true", help="Keep captured mic wav files.")
    parser.add_argument("--no-tts", action="store_true", help="Skip reply speech synthesis and test mic/STT/text only.")
    parser.add_argument("--no-playback", action="store_true", help="Do not auto-play synthesized audio.")
    parser.add_argument("--list-devices", action="store_true", help="List sounddevice input devices and exit.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.list_devices:
        devices = available_recording_devices()
        if not devices:
            raise SystemExit("No sounddevice input devices found. Install dependencies and confirm your microphone is available.")
        for entry in devices:
            print(entry)
        return
    config = VoiceLoopConfig(
        mode=VoiceMode(args.mode),
        record_seconds=args.seconds,
        max_record_seconds=args.max_seconds,
        silence_stop_seconds=args.silence_stop,
        speech_threshold=args.speech_threshold,
        speech_start_seconds=args.speech_start,
        device=args.device,
        sample_rate=args.rate,
        keep_inputs=args.keep_inputs,
        synthesize_speech=not args.no_tts,
        playback_enabled=not args.no_playback,
    )
    controller = VoiceModeController()
    try:
        controller.run_cli_loop(config)
    except (AudioBackendError, MissingBinaryError) as exc:
        raise SystemExit(str(exc)) from exc
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"recording failed: {exc}") from exc


if __name__ == "__main__":
    main()
