"""Reentrant thread/process serialization for CLI and dashboard mutations."""
from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

_guard = threading.Lock()
_locks: dict[tuple[int, str], threading.RLock] = {}
_local = threading.local()


def _after_fork():
    global _guard, _locks, _local
    _guard, _locks, _local = threading.Lock(), {}, threading.local()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


@contextmanager
def lifecycle_lock(path: Path, timeout: float = 30):
    path = path.resolve()
    key = (os.getpid(), str(path))
    deadline = time.monotonic() + max(0, timeout)
    with _guard:
        lock = _locks.setdefault(key, threading.RLock())
    if not lock.acquire(timeout=max(0, timeout)):
        raise TimeoutError("lifecycle control busy")
    depths = getattr(_local, "depths", None)
    if depths is None:
        _local.depths = depths = {}
    handle = None
    acquired = False
    try:
        if depths.get(key, 0):
            depths[key] += 1
            try:
                yield
            finally:
                depths[key] -= 1
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+b")
        if os.name == "nt":
            import msvcrt
            if path.stat().st_size == 0:
                handle.write(b"0")
                handle.flush()
        else:
            import fcntl
        while True:
            try:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except (BlockingIOError, PermissionError):
                if time.monotonic() >= deadline:
                    raise TimeoutError("lifecycle control busy") from None
                time.sleep(min(0.025, max(0, deadline - time.monotonic())))
        depths[key] = 1
        try:
            yield
        finally:
            depths.pop(key, None)
    finally:
        if handle is not None:
            try:
                if acquired:
                    if os.name == "nt":
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
        lock.release()
