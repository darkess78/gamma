from __future__ import annotations

import argparse
import json
import sys
import time

from .voice.tts import TTSService


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("text", nargs="*", help="Text to synthesize")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Print raw JSON-style output")
    parser.add_argument("--compact", action="store_true", help="Print a one-line timing summary")
    return parser.parse_args()


def _print_pretty(result: dict) -> None:
    timings = result.get("timings_ms", {}) if isinstance(result.get("timings_ms"), dict) else {}
    print("TTS Smoke Test")
    print(f"Provider: {result.get('provider', 'n/a')}")
    print(f"Audio: {result.get('audio_path', '')}")
    print(f"Type: {result.get('content_type', 'n/a')}")
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
    text = " ".join(args.text).strip() or "Hello from Gamma. This is a local TTS pipeline smoke test."
    started_at = time.perf_counter()
    result = TTSService().synthesize(text)
    total_ms = round((time.perf_counter() - started_at) * 1000, 1)
    payload = {
        "provider": result.provider,
        "audio_path": result.audio_path,
        "content_type": result.content_type,
        "text": result.text,
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
