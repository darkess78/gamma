from __future__ import annotations

import argparse
import subprocess

from .voice.controller import MissingBinaryError, VoiceLoopConfig, VoiceMode, VoiceModeController


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Gamma with a voice mode controller.")
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in VoiceMode],
        default=VoiceMode.TURN_BASED.value,
        help="Voice interaction mode. Non-turn modes are scaffolded and currently fall back to the validated turn-based path.",
    )
    parser.add_argument("--seconds", type=int, default=6, help="Recording length per turn.")
    parser.add_argument("--device", help="Optional arecord device, e.g. hw:1,0")
    parser.add_argument("--rate", type=int, default=16000, help="Recording sample rate.")
    parser.add_argument("--keep-inputs", action="store_true", help="Keep captured mic wav files.")
    parser.add_argument("--no-playback", action="store_true", help="Do not auto-play synthesized audio.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = VoiceLoopConfig(
        mode=VoiceMode(args.mode),
        record_seconds=args.seconds,
        device=args.device,
        sample_rate=args.rate,
        keep_inputs=args.keep_inputs,
        playback_enabled=not args.no_playback,
    )
    controller = VoiceModeController()
    try:
        controller.run_cli_loop(config)
    except MissingBinaryError as exc:
        raise SystemExit(str(exc)) from exc
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"recording failed: {exc}") from exc


if __name__ == "__main__":
    main()
