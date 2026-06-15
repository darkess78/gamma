from __future__ import annotations

import os

import psutil


def main() -> int:
    port = int(os.getenv("SHANA_AUDIO_UNDERSTANDING_PORT", "9883"))
    pids: set[int] = set()
    for connection in psutil.net_connections(kind="tcp"):
        if connection.status == psutil.CONN_LISTEN and connection.laddr and connection.laddr.port == port and connection.pid:
            pids.add(connection.pid)
    if not pids:
        print(f"No audio-understanding process is listening on port {port}.")
        return 0
    for pid in sorted(pids):
        try:
            process = psutil.Process(pid)
            process.terminate()
            process.wait(timeout=15)
        except psutil.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        except psutil.Error:
            continue
    print(f"Stopped audio-understanding server on port {port}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
