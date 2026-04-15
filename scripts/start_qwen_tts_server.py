"""
Start the Qwen3-TTS local server as a detached background process.
Waits up to 90 seconds for the model to load and the server to start listening.

Usage:
    python scripts/start_qwen_tts_server.py

Environment variables forwarded to the server:
    QWEN_TTS_MODEL   – HuggingFace model ID (default Qwen/Qwen3-TTS-12Hz-1.7B-Base)
    QWEN_TTS_DTYPE   – float32 | bfloat16 (default float32)
    QWEN_TTS_DEVICE  – cuda | cpu | auto (default auto)
    QWEN_TTS_PORT    – port (default 9882)
    QWEN_TTS_HOST    – host (default 127.0.0.1)
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path


def _is_listening(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    server_script = repo_root / "scripts" / "qwen_tts_server.py"
    if not server_script.exists():
        raise SystemExit(f"Server script not found: {server_script}")

    port = int(os.getenv("QWEN_TTS_PORT", "9882"))
    host = os.getenv("QWEN_TTS_HOST", "127.0.0.1")
    log_dir = repo_root / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_log = log_dir / "qwen_tts.out.log"
    stderr_log = log_dir / "qwen_tts.err.log"

    if _is_listening(port, host):
        print(f"Qwen3-TTS server is already listening on port {port}.")
        return 0

    # Use the .venv Python — prefer pythonw.exe (no console window)
    venv_scripts = repo_root / ".venv" / "Scripts"
    python_exe = venv_scripts / "pythonw.exe"
    if not python_exe.exists():
        python_exe = venv_scripts / "python.exe"
    if not python_exe.exists():
        # Fall back to the current interpreter
        python_exe = Path(sys.executable)
        pythonw = python_exe.parent / "pythonw.exe"
        if pythonw.exists():
            python_exe = pythonw

    stdout_log.write_text("", encoding="utf-8")
    stderr_log.write_text("", encoding="utf-8")

    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")

    with stdout_log.open("ab") as stdout_handle, stderr_log.open("ab") as stderr_handle:
        subprocess.Popen(
            [str(python_exe), str(server_script)],
            cwd=str(repo_root),
            env=env,
            stdout=stdout_handle,
            stderr=stderr_handle,
            creationflags=(
                getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            ),
        )

    print(f"Qwen3-TTS server started. Waiting for model to load (may take up to 90s)...")
    deadline = time.time() + 90
    last_dot = time.time()
    while time.time() < deadline:
        if _is_listening(port, host):
            print(f"\nQwen3-TTS API is listening on http://{host}:{port}/tts")
            print(f"Logs: {stdout_log}  /  {stderr_log}")
            return 0
        if time.time() - last_dot >= 5:
            print(".", end="", flush=True)
            last_dot = time.time()
        time.sleep(1)

    raise SystemExit(
        f"\nQwen3-TTS did not start within 90 seconds.\n"
        f"Check logs:\n  stdout: {stdout_log}\n  stderr: {stderr_log}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
