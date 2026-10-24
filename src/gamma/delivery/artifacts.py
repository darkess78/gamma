"""Stdlib-only artifact snapshot and integrity verification."""
import hashlib
import os
import stat
from pathlib import Path

_MAX = 33554432

def _no_symlink(p: Path):
    if os.path.islink(str(p)):
        raise ValueError("symlink not allowed")

def _open_rd(p: Path) -> int:
    fl = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        fl |= os.O_NOFOLLOW
    else:
        _no_symlink(p)
    fd = os.open(str(p), fl | getattr(os, "O_NONBLOCK", 0))
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ValueError("regular artifact file required")
    return fd

def _read_full(fd: int, n: int) -> bytes:
    parts = []
    remain = n
    while remain > 0:
        chunk = os.read(fd, remain)
        if not chunk:
            break
        parts.append(chunk)
        remain -= len(chunk)
    return b"".join(parts)

def verified_bytes(path: Path, digest: str, size: int, max_bytes: int = _MAX) -> bytes:
    """Read *size* bytes from *path*, verify SHA-256 and size, return bytes."""
    _no_symlink(path)
    if not 1 <= size <= max_bytes:
        raise ValueError("invalid size")
    fd = _open_rd(path)
    try:
        data = _read_full(fd, size + 1)
    finally:
        os.close(fd)
    if len(data) != size or hashlib.sha256(data).hexdigest() != digest.lower():
        raise ValueError("integrity check failed")
    return data

def snapshot(source: Path, root: Path, max_bytes: int = _MAX) -> tuple:
    """Bounded-read *source*, store immutable copy under *root*, return (path, digest, size)."""
    _no_symlink(source)
    fd = _open_rd(source)
    try:
        data = _read_full(fd, max_bytes + 1)
    finally:
        os.close(fd)
    if not data:
        raise ValueError("source is empty")
    if len(data) > max_bytes:
        raise ValueError("source exceeds max_bytes")
    sha = hashlib.sha256(data).hexdigest()
    size = len(data)
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    dest = root / f"{sha}.bin"
    if os.path.lexists(str(dest)):
        _no_symlink(dest)
        verified_bytes(dest, sha, size, max_bytes)
        return dest, sha, size
    fd = os.open(str(dest), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        remaining = data
        while remaining:
            written = os.write(fd, remaining)
            if written == 0:
                raise OSError("artifact write made no progress")
            remaining = remaining[written:]
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        if os.path.lexists(str(dest)) and not os.path.islink(str(dest)):
            os.unlink(str(dest))
        raise
    os.close(fd)
    return dest, sha, size
