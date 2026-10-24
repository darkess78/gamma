"""Per-file writer locking and complete JSON publication for persona state."""
from __future__ import annotations

import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def state_file_lock(path: Path):
    """Hold a persistent sidecar lock for the caller's entire mutation."""
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    # Never unlink this file: every contender must lock the same file identity.
    with target.with_name(target.name + ".lock").open("a+b") as stream:
        if os.name == "nt":
            import msvcrt

            if os.fstat(stream.fileno()).st_size == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def atomic_write_text(path: Path, text: str) -> None:
    """Replace the canonical file only after a complete UTF-8 write."""
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = stat.S_IMODE(target.stat().st_mode)
    except FileNotFoundError:
        mode = None
    descriptor, name = tempfile.mkstemp(prefix=target.name + ".tmp-", dir=target.parent)
    temporary = Path(name)
    try:
        # TextIOWrapper handles short underlying writes; its close completes
        # buffered output before publication.
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = None
            stream.write(text)
        if mode is not None:
            temporary.chmod(mode)
        os.replace(temporary, target)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
