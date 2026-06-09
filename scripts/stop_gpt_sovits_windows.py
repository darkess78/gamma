from __future__ import annotations

import os
import socket
from pathlib import Path

import psutil


def _package_root(repo_root: Path) -> Path:
    env_root = os.getenv("GPT_SOVITS_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    return (repo_root / "data" / "GPT-SoVITS-win" / "GPT-SoVITS-v3lora-20250228").resolve()


def _listening_pid(port: int) -> int | None:
    for connection in psutil.net_connections(kind="tcp"):
        laddr = getattr(connection, "laddr", None)
        if not laddr:
            continue
        if laddr.port == port and connection.status == psutil.CONN_LISTEN:
            return connection.pid
    return None


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    package_root = _package_root(repo_root)
    port = int(os.getenv("GPT_SOVITS_PORT", "9881"))

    pid = _listening_pid(port)
    if pid is None:
        print(f"No GPT-SoVITS process is listening on port {port}.")
        return 0

    try:
        process = psutil.Process(pid)
    except psutil.Error:
        print(f"No accessible process found for port {port}.")
        return 0

    exe_path = Path(process.exe()).resolve()
    if package_root not in exe_path.parents and exe_path != package_root:
        raise SystemExit(f"Port {port} is owned by a different process: {exe_path}")

    process.terminate()
    try:
        process.wait(timeout=10)
    except psutil.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)

    print(f"Stopped GPT-SoVITS on port {port}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
