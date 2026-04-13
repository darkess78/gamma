from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from .voice.rvc_support import discover_rvc_project_root, discover_rvc_python, resolve_rvc_index_path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("text", nargs="*", help="Text to synthesize")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Print raw JSON-style output")
    parser.add_argument("--compact", action="store_true", help="Print a one-line timing summary")
    return parser.parse_args()


def _print_pretty(result: dict) -> None:
    timings = result.get("timings_ms", {}) if isinstance(result.get("timings_ms"), dict) else {}
    print("Henya TTS Test")
    print(f"Provider: {result.get('provider', 'n/a')}")
    print(f"Audio: {result.get('audio_path', '')}")
    print(f"Type: {result.get('content_type', 'n/a')}")
    print(f"Text: {result.get('text', '')}")
    if timings:
        print("Timings:")
        if "backend_ms" in timings:
            print(f"  Piper: {timings['backend_ms']} ms")
        if "rvc_ms" in timings:
            print(f"  RVC: {timings['rvc_ms']} ms")
        if "total_tts_pipeline_ms" in timings:
            print(f"  Total pipeline: {timings['total_tts_pipeline_ms']} ms")
    print(f"Wall clock: {result.get('wall_clock_ms', 'n/a')} ms")


def _print_compact(result: dict) -> None:
    timings = result.get("timings_ms", {}) if isinstance(result.get("timings_ms"), dict) else {}
    parts = [f"Provider {result.get('provider', 'n/a')}"]
    if "backend_ms" in timings:
        parts.append(f"Piper {timings['backend_ms']} ms")
    if "rvc_ms" in timings:
        parts.append(f"RVC {timings['rvc_ms']} ms")
    if "total_tts_pipeline_ms" in timings:
        parts.append(f"Total {timings['total_tts_pipeline_ms']} ms")
    parts.append(f"Wall {result.get('wall_clock_ms', 'n/a')} ms")
    print(" | ".join(parts))


def _setdefault(name: str, value: str) -> None:
    if not os.getenv(name):
        os.environ[name] = value


def _configure_henya_rvc() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    rvc_root = discover_rvc_project_root(os.getenv("SHANA_RVC_PROJECT_ROOT"))
    if rvc_root is None:
        raise SystemExit(
            "Could not find the RVC project root. Set SHANA_RVC_PROJECT_ROOT or install RVC in an expected location."
        )
    rvc_python = discover_rvc_python(os.getenv("SHANA_RVC_PYTHON"), rvc_root)
    if rvc_python is None:
        raise SystemExit(
            "Could not find the RVC Python interpreter. Set SHANA_RVC_PYTHON or create the expected RVC .venv."
        )
    model_name = os.getenv("SHANA_RVC_MODEL_NAME", "HenyaTheGeniusV2.pth")
    try:
        index_path = resolve_rvc_index_path(rvc_root, os.getenv("SHANA_RVC_INDEX_PATH"), model_name)
    except Exception as exc:
        raise SystemExit(str(exc)) from exc

    _setdefault("SHANA_TTS_PROVIDER", "piper")
    _setdefault("SHANA_PIPER_EXE", "piper")
    _setdefault("SHANA_PIPER_MODEL_PATH", str(repo_root / "data" / "piper" / "en_US-lessac-medium.onnx"))
    _setdefault("SHANA_PIPER_CONFIG_PATH", str(repo_root / "data" / "piper" / "en_US-lessac-medium.onnx.json"))
    os.environ["SHANA_RVC_ENABLED"] = "true"
    _setdefault("SHANA_RVC_PYTHON", str(rvc_python))
    _setdefault("SHANA_RVC_PROJECT_ROOT", str(rvc_root))
    _setdefault("SHANA_RVC_MODEL_NAME", model_name)
    _setdefault("SHANA_RVC_INDEX_PATH", str(index_path))
    _setdefault("SHANA_RVC_PITCH", "12")
    _setdefault("SHANA_RVC_FORMANT", "0.15")
    _setdefault("SHANA_RVC_F0_METHOD", "rmvpe")
    _setdefault("SHANA_RVC_INDEX_RATE", "0.15")
    _setdefault("SHANA_RVC_FILTER_RADIUS", "3")
    _setdefault("SHANA_RVC_RMS_MIX_RATE", "0.2")
    _setdefault("SHANA_RVC_PROTECT", "0.33")
    _setdefault("SHANA_RVC_RESAMPLE_SR", "0")


def main() -> None:
    args = _parse_args()
    _configure_henya_rvc()
    from .voice.tts import TTSService

    text = " ".join(args.text).strip() or "Hello from Gamma. This is a Henya RVC TTS smoke test."
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
