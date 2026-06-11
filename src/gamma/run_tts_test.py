from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .voice.tts import TTSService


def _parse_args() -> argparse.Namespace:
    """Parse command line args.
    
    Returns:
        argparse.Namespace: Parsed arguments.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("text", nargs="*".split(), help="Text to synthesize")
    parser.add_argument("--file", dest="input_file", metavar="PATH", 
                        help="Path to .txt file to synthesize (auto-splits by paragraph)")
    parser.add_argument("--emotion", default="neutral", 
                        help="Emotion to pass into TTS; defaults to neutral to match live voice.")
    parser.add_argument("--style", action="append", default=[], help="Hidden voice style to pass into TTS; may be repeated.")
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="Print raw JSON-style output")
    parser.add_argument("--compact", action="store_true",
                        help="Print a one-line timing summary")
    return parser.parse_args()


def _print_pretty(result: dict) -> None:
    """Print pretty result.
    
    Args:
        result: TTS result dict.
    """
    timings = result.get("timings_ms", {}) if isinstance(result.get("timings_ms"), dict) else {}
    print("TTS Smoke Test")
    print(f"Provider: {result.get('provider', 'n/a')}")
    print(f"Audio: {result.get('audio_path', '')}")
    print(f"Type: {result.get('content_type', 'n/a')}")
    print(f"Emotion: {result.get('emotion', 'n/a')}")
    print(f"Styles: {', '.join(result.get('styles') or []) or 'none'}")
    print(f"Text: {result.get('text', '')}")
    if timings:
        print("Timings:")
        if "backend_ms" in timings:
            print(f"  Backend: {timings['backend_ms']} ms")
        if "rvc_ms" in timings:
            print(f"  RVC: {timings['rvc_ms']} ms")
        if "total_tts_pipeline_ms" in timings:
            print(f"  TTS total: {timings['total_tts_pipeline_ms']} ms")
    print(f"Wall clock: {result.get('wall_clock_ms', 'n/a')} ms")


def _print_compact(result: dict) -> None:
    timings = result.get("timings_ms", {}) if isinstance(result.get("timings_ms"), dict) else {}
    parts = [f"Provider {result.get('provider', 'n/a')}"]
    if "backend_ms" in timings:
        parts.append(f"Backend {timings['backend_ms']} ms")
    if "rvc_ms" in timings:
        parts.append(f"RVC {timings['rvc_ms']} ms")
    if "total_tts_pipeline_ms" in timings:
        parts.append(f"Total {timings['total_tts_pipeline_ms']} ms")
    parts.append(f"Wall {result.get('wall_clock_ms', 'n/a')} ms")
    print(" | ".join(parts))


def main() -> None:
    args = _parse_args()
    started_at = time.perf_counter()
    svc = TTSService()
    styles = [str(style).strip().lower() for style in args.style if str(style).strip()]
    emotion = str(args.emotion or "neutral").strip().lower() or "neutral"
    if args.input_file:
        text = Path(args.input_file).read_text(encoding="utf-8").strip()
        if not text:
            print("error: file is empty", file=sys.stderr)
            sys.exit(1)
        result = svc.synthesize_multipart(text, emotion=emotion, styles=styles)
    else:
        text = " ".join(args.text).strip() or "Hello from Gamma. This is a local TTS pipeline smoke test."
        result = svc.synthesize(text, emotion=emotion, styles=styles)
    total_ms = round((time.perf_counter() - started_at) * 1000, 1)
    payload = {
        "provider": result.provider,
        "audio_path": result.audio_path,
        "content_type": result.content_type,
        "text": result.text,
        "emotion": (result.metadata or {}).get("emotion"),
        "styles": (result.metadata or {}).get("hidden_voice_styles", []),
        "tts_metadata": result.metadata or {},
        "timings_ms": (result.metadata or {}).get("timings_ms", {}),
        "wall_clock_ms": total_ms,
    }
    if args.json_output:
        print(json.dumps(payload, ensure_ascii=False))
    elif args.compact:
        _print_compact(payload)
    else:
        _print_pretty(payload)


if __name__ == "__main__":
    main()
