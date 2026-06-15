from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

from dotenv import load_dotenv


def _is_listening(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _health(host: str, port: int) -> dict[str, object] | None:
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=2) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def _audio_python(repo_root: Path) -> Path:
    configured = os.getenv("SHANA_AUDIO_UNDERSTANDING_PYTHON", "").strip()
    candidates = [
        Path(configured).expanduser() if configured else None,
        repo_root / ".venv-audio" / "bin" / "python",
        repo_root / ".venv-audio" / "Scripts" / "python.exe",
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    raise SystemExit(
        "Audio-understanding Python was not found. Run "
        ".venv/bin/python scripts/setup_audio_understanding_env.py first."
    )


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    load_dotenv(repo_root / ".env")
    host = os.getenv("SHANA_AUDIO_UNDERSTANDING_BIND_HOST", "127.0.0.1")
    port = int(os.getenv("SHANA_AUDIO_UNDERSTANDING_PORT", "9883"))
    if _is_listening(host, port):
        print(f"Audio-understanding server is already listening on {host}:{port}.")
        return 0

    python_executable = _audio_python(repo_root)
    check = subprocess.run(
        [
            str(python_executable),
            "-c",
            (
                "import torch, transformers, fastapi, uvicorn; "
                "print(torch.__version__, torch.version.cuda, torch.cuda.is_available(), torch.cuda.device_count())"
            ),
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if check.returncode != 0:
        raise SystemExit(f"Audio-understanding runtime preflight failed:\n{check.stderr or check.stdout}")
    print(f"Runtime preflight: {check.stdout.strip()}")

    runtime_dir = repo_root / "data" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    stdout_log = runtime_dir / "audio-understanding.stdout.log"
    stderr_log = runtime_dir / "audio-understanding.stderr.log"
    stdout_log.write_text("", encoding="utf-8")
    stderr_log.write_text("", encoding="utf-8")
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")

    with stdout_log.open("ab") as stdout_handle, stderr_log.open("ab") as stderr_handle:
        process = subprocess.Popen(
            [
                str(python_executable),
                "-m",
                "uvicorn",
                "gamma.audio_understanding_server:app",
                "--host",
                host,
                "--port",
                str(port),
                "--no-access-log",
            ],
            cwd=repo_root,
            env=env,
            stdout=stdout_handle,
            stderr=stderr_handle,
            start_new_session=os.name != "nt",
            creationflags=(
                getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if os.name == "nt"
                else 0
            ),
        )

    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if process.poll() is not None:
            detail = stderr_log.read_text(encoding="utf-8", errors="replace")[-4000:]
            raise SystemExit(f"Audio-understanding server exited early with code {process.returncode}:\n{detail}")
        payload = _health(host, port)
        if payload and payload.get("ok"):
            print(f"Audio-understanding server ready at http://{host}:{port}.")
            print(json.dumps(payload, indent=2))
            return 0
        time.sleep(1)

    raise SystemExit(f"Audio-understanding server did not become healthy. Check {stderr_log}.")


if __name__ == "__main__":
    raise SystemExit(main())
