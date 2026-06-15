from __future__ import annotations

import argparse
import json
import math
import os
import socket
import struct
import subprocess
import tempfile
import time
import wave
from pathlib import Path
from typing import Any

import httpx


def _run(command: list[str], *, cwd: Path) -> dict[str, Any]:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    return {
        "command": command,
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def _is_listening(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _gpu_snapshot(repo_root: Path) -> dict[str, Any]:
    gpu_result = _run(
        [
            "nvidia-smi",
            "--query-gpu=index,name,uuid,memory.total,memory.used,memory.free",
            "--format=csv,noheader,nounits",
        ],
        cwd=repo_root,
    )
    process_result = _run(
        [
            "nvidia-smi",
            "--query-compute-apps=pid,process_name,gpu_uuid,used_gpu_memory",
            "--format=csv,noheader,nounits",
        ],
        cwd=repo_root,
    )
    return {
        "gpus": _parse_csv_rows(
            gpu_result["stdout"],
            ("index", "name", "uuid", "memory_total_mib", "memory_used_mib", "memory_free_mib"),
        )
        if gpu_result["returncode"] == 0
        else [],
        "processes": _parse_csv_rows(
            process_result["stdout"],
            ("pid", "process_name", "gpu_uuid", "used_memory_mib"),
        )
        if process_result["returncode"] == 0
        else [],
        "errors": [
            detail
            for detail in (gpu_result["stderr"], process_result["stderr"])
            if detail
        ],
    }


def _parse_csv_rows(output: str, fields: tuple[str, ...]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in output.splitlines():
        values = [value.strip() for value in line.split(",")]
        if len(values) == len(fields):
            rows.append(dict(zip(fields, values, strict=True)))
    return rows


def _write_probe_wav(path: Path, *, seconds: float = 2.0, sample_rate: int = 16_000) -> None:
    frame_count = int(seconds * sample_rate)
    samples = (
        int(0.12 * 32767 * math.sin(2 * math.pi * 220 * index / sample_rate))
        for index in range(frame_count)
    )
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"".join(struct.pack("<h", sample) for sample in samples))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure Gamma audio-understanding placement without changing tracked configuration."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9883)
    parser.add_argument("--emotion-device", default="cuda:1")
    parser.add_argument("--event-device", default="cuda:1")
    parser.add_argument("--requests", type=int, default=3)
    parser.add_argument("--audio-file", type=Path)
    parser.add_argument("--transcript", default="This is an audio-understanding placement probe.")
    parser.add_argument(
        "--start-sidecar",
        action="store_true",
        help="Start the sidecar with Hugging Face providers when it is not already running.",
    )
    parser.add_argument(
        "--keep-running",
        action="store_true",
        help="Leave a sidecar started by this probe running.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/runtime/audio-understanding-gpu-probe.json"),
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    endpoint = f"http://{args.host}:{args.port}"
    result: dict[str, Any] = {
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "endpoint": endpoint,
        "requested_placement": {
            "speaker_emotion_device": args.emotion_device,
            "audio_event_device": args.event_device,
        },
        "sidecar_started_by_probe": False,
        "before": {
            "gpu": _gpu_snapshot(repo_root),
            "ollama": _run(["ollama", "ps"], cwd=repo_root),
            "gamma_services": _run(
                [str(repo_root / ".venv" / "bin" / "python"), "-m", "gamma.supervisor.cli", "status", "all"],
                cwd=repo_root,
            ),
        },
    }

    listening_before = _is_listening(args.host, args.port)
    if not listening_before and not args.start_sidecar:
        result["error"] = "sidecar is not listening; rerun with --start-sidecar"
        _write_result(repo_root, args.output, result)
        return 2

    environment = os.environ.copy()
    environment.update(
        {
            "SHANA_AUDIO_UNDERSTANDING_ENABLED": "true",
            "SHANA_AUDIO_UNDERSTANDING_BIND_HOST": args.host,
            "SHANA_AUDIO_UNDERSTANDING_PORT": str(args.port),
            "SHANA_SPEAKER_EMOTION_PROVIDER": "huggingface",
            "SHANA_AUDIO_EVENT_PROVIDER": "huggingface",
            "SHANA_SPEAKER_EMOTION_DEVICE": args.emotion_device,
            "SHANA_AUDIO_EVENT_DEVICE": args.event_device,
            "SHANA_AUDIO_MODEL_LOCAL_FILES_ONLY": "true",
        }
    )

    try:
        if not listening_before:
            started_at = time.perf_counter()
            start = subprocess.run(
                [str(repo_root / ".venv" / "bin" / "python"), "scripts/start_audio_understanding_server.py"],
                cwd=repo_root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            result["sidecar_start"] = {
                "returncode": start.returncode,
                "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 1),
                "stdout": start.stdout.strip(),
                "stderr": start.stderr.strip(),
            }
            if start.returncode != 0:
                result["error"] = "sidecar failed to start"
                _write_result(repo_root, args.output, result)
                return start.returncode
            result["sidecar_started_by_probe"] = True

        result["health"] = httpx.get(f"{endpoint}/health", timeout=10).json()
        result["after_load_gpu"] = _gpu_snapshot(repo_root)
        request_results: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = args.audio_file.resolve() if args.audio_file else Path(temp_dir) / "probe.wav"
            if not args.audio_file:
                _write_probe_wav(audio_path)
            for request_number in range(1, max(args.requests, 1) + 1):
                started_at = time.perf_counter()
                with audio_path.open("rb") as audio:
                    response = httpx.post(
                        f"{endpoint}/analyze",
                        files={"audio_file": (audio_path.name, audio, "audio/wav")},
                        data={"transcript": args.transcript},
                        timeout=60,
                    )
                request_results.append(
                    {
                        "request": request_number,
                        "status_code": response.status_code,
                        "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 1),
                        "response": response.json(),
                    }
                )
        result["requests"] = request_results
        result["after_requests_gpu"] = _gpu_snapshot(repo_root)
        _write_result(repo_root, args.output, result)
        return 0
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["after_error_gpu"] = _gpu_snapshot(repo_root)
        _write_result(repo_root, args.output, result)
        return 1
    finally:
        if result["sidecar_started_by_probe"] and not args.keep_running:
            result["sidecar_stop"] = _run(
                [str(repo_root / ".venv" / "bin" / "python"), "scripts/stop_audio_understanding_server.py"],
                cwd=repo_root,
            )
            _write_result(repo_root, args.output, result)


def _write_result(repo_root: Path, output_path: Path, result: dict[str, Any]) -> None:
    resolved = output_path if output_path.is_absolute() else repo_root / output_path
    resolved.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(result, indent=2)
    resolved.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    print(f"Probe report: {resolved}")


if __name__ == "__main__":
    raise SystemExit(main())
