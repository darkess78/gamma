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

import importlib.util
import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

from gamma.system.python_runtime import resolve_python_executable


def _is_listening(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _health_ok(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=2) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


def _missing_runtime_modules() -> list[str]:
    required = ["torch", "qwen_tts"]
    return [name for name in required if importlib.util.find_spec(name) is None]


def _tail_text(path: Path, *, limit_bytes: int = 4096) -> str:
    if not path.exists():
        return ""
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > limit_bytes:
            handle.seek(-limit_bytes, os.SEEK_END)
        return handle.read().decode("utf-8", errors="replace").strip()


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    load_dotenv(repo_root / ".env")
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

    missing_modules = _missing_runtime_modules()
    if missing_modules:
        missing_text = ", ".join(missing_modules)
        raise SystemExit(
            "Qwen3-TTS runtime dependencies are missing in the active environment: "
            f"{missing_text}\n"
            "Install them in the repo virtualenv before starting Qwen3-TTS."
        )

    python_exe = resolve_python_executable(repo_root, prefer_windowless=True)

    stdout_log.write_text("", encoding="utf-8")
    stderr_log.write_text("", encoding="utf-8")

    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")

    with stdout_log.open("ab") as stdout_handle, stderr_log.open("ab") as stderr_handle:
        kwargs = {
            "cwd": str(repo_root),
            "env": env,
            "stdout": stdout_handle,
            "stderr": stderr_handle,
        }
        if os.name == "nt":
            kwargs["creationflags"] = (
                getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
        else:
            kwargs["start_new_session"] = True
        process = subprocess.Popen([python_exe, str(server_script)], **kwargs)

    print(f"Qwen3-TTS server started. Waiting for model to load (may take up to 90s)...")
    deadline = time.time() + 90
    last_dot = time.time()
    while time.time() < deadline:
        returncode = process.poll()
        if returncode is not None:
            stderr_tail = _tail_text(stderr_log)
            stdout_tail = _tail_text(stdout_log)
            detail_parts = [
                f"Qwen3-TTS exited early with code {returncode}.",
                f"stdout log: {stdout_log}",
                f"stderr log: {stderr_log}",
            ]
            if stderr_tail:
                detail_parts.append(f"stderr tail:\n{stderr_tail}")
            elif stdout_tail:
                detail_parts.append(f"stdout tail:\n{stdout_tail}")
            raise SystemExit("\n".join(detail_parts))
        if _health_ok(port, host):
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
