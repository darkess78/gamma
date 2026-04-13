from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path


def _package_root(repo_root: Path) -> Path:
    env_root = os.getenv("GPT_SOVITS_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    return (repo_root / "data" / "GPT-SoVITS-win" / "GPT-SoVITS-v3lora-20250228").resolve()


def _is_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    package_root = _package_root(repo_root)
    python_exe = package_root / "runtime" / "python.exe"
    stdout_log = package_root / "api-9881.out.log"
    stderr_log = package_root / "api-9881.err.log"
    port = int(os.getenv("GPT_SOVITS_PORT", "9881"))

    if not python_exe.exists():
        raise SystemExit(f"GPT-SoVITS runtime not found at {python_exe}")

    if _is_listening(port):
        print(f"GPT-SoVITS is already listening on port {port}.")
        return 0

    stdout_log.write_text("", encoding="utf-8")
    stderr_log.write_text("", encoding="utf-8")
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    with stdout_log.open("ab") as stdout_handle, stderr_log.open("ab") as stderr_handle:
        subprocess.Popen(
            [
                str(python_exe),
                "api_v2.py",
                "-a",
                "127.0.0.1",
                "-p",
                str(port),
                "-c",
                "GPT_SoVITS/configs/tts_infer.yaml",
            ],
            cwd=package_root,
            env=env,
            stdout=stdout_handle,
            stderr=stderr_handle,
            creationflags=(
                getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            ),
        )

    deadline = time.time() + 25
    while time.time() < deadline:
        if _is_listening(port):
            print(f"GPT-SoVITS API is listening on http://127.0.0.1:{port}/tts")
            return 0
        time.sleep(1)

    if not _is_listening(port):
        raise SystemExit(f"GPT-SoVITS did not start. Check {stdout_log} and {stderr_log}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
