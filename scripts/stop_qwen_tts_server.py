"""Stop the Qwen3-TTS local server by terminating the process on its port."""
from __future__ import annotations

import os
from pathlib import Path

import psutil


def _listening_pid(port: int) -> int | None:
    for connection in psutil.net_connections(kind="tcp"):
        laddr = getattr(connection, "laddr", None)
        if not laddr:
            continue
        if laddr.port == port and connection.status == psutil.CONN_LISTEN:
            return connection.pid
    return None


def main() -> int:
    port = int(os.getenv("QWEN_TTS_PORT", "9882"))

    pid = _listening_pid(port)
    if pid is None:
        print(f"No Qwen3-TTS process is listening on port {port}.")
        return 0

    try:
        process = psutil.Process(pid)
    except psutil.Error:
        print(f"No accessible process found for port {port}.")
        return 0

    process.terminate()
    try:
        process.wait(timeout=10)
    except psutil.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)

    print(f"Stopped Qwen3-TTS on port {port}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
