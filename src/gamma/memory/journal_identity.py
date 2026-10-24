"""Conservative local process checks for internal journal recovery."""
import math
import os
import socket

import psutil


def capture_identity() -> tuple[str, int, float]:
    pid = os.getpid()
    return socket.gethostname(), pid, psutil.Process(pid).create_time()


def owner_is_dead(host: str, pid: int, process_started: float) -> bool:
    try:
        if host != socket.gethostname():
            return False
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            return False
        if isinstance(process_started, bool) or not math.isfinite(process_started) or process_started <= 0:
            return False
        process = psutil.Process(pid)
        # A reused PID is unverifiable ownership, not permission to recover.
        if process.create_time() != process_started:
            return False
        return process.status() in {psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD}
    except psutil.NoSuchProcess:
        return True
    except (psutil.Error, OSError, TypeError, ValueError, OverflowError):
        return False
