"""Cooperative, process-safe admission for a configured local inference origin."""
from contextlib import contextmanager
import errno
import hashlib
import os
from pathlib import Path
import time
from urllib.parse import urlsplit
import uuid


def _coordination_root():
    # Different Gamma checkouts/processes of this user must use the same root.
    return Path.home() / '.cache' / 'gamma' / 'inference-admission'


def _resource_key(endpoint):
    url = urlsplit(endpoint)
    identity = (url.scheme.lower(), (url.hostname or '').lower(),
                url.port or (443 if url.scheme.lower() == 'https' else 80), url.path.rstrip('/'))
    return hashlib.sha256(repr(identity).encode()).hexdigest()


def _open(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    stream = os.fdopen(fd, 'r+b', buffering=0)
    if os.fstat(fd).st_size == 0:
        stream.write(b'0')
    return stream


def _try_lock(stream):
    try:
        if os.name == 'nt':
            import msvcrt
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            return False
        raise


def _unlock(stream):
    if os.name == 'nt':
        import msvcrt
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _foreground_waiting(root, key):
    waiting = False
    for path in root.glob(key + '.wait-*'):
        try:
            with _open(path) as marker:
                stale = _try_lock(marker)
                waiting = waiting or not stale
            if stale:
                # Close first on Windows. UUID markers are never reused.
                path.unlink(missing_ok=True)
        except FileNotFoundError:
            pass
    return waiting


@contextmanager
def backend_slot(endpoint, *, foreground, timeout, checkpoint=None, ready=None):
    """Hold admission through response consumption; never cancel or replay HTTP."""
    root = _coordination_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink():
        raise ValueError('Invalid inference admission storage')
    if os.name != 'nt' and (root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077):
        raise ValueError('Inference admission storage must be private')
    key = _resource_key(endpoint)
    deadline = time.monotonic() + max(0, float(timeout))
    marker = marker_path = None
    acquired = False
    try:
        with _open(root / (key + '.gate')) as gate, _open(root / (key + '.active')) as active:
            try:
                while True:
                    if checkpoint:
                        checkpoint()  # No gate or active lease held during a pause.
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Inference admission deadline exceeded')
                    if _try_lock(gate):
                        try:
                            if foreground and marker is None:
                                marker_path = root / (key + '.wait-' + uuid.uuid4().hex)
                                marker = _open(marker_path)
                                if not _try_lock(marker):
                                    raise RuntimeError('Inference waiter registration failed')
                            if foreground or not _foreground_waiting(root, key):
                                acquired = _try_lock(active)
                        finally:
                            _unlock(gate)
                    if acquired:
                        if ready is None or ready():
                            if time.monotonic() >= deadline:
                                raise TimeoutError('Inference admission deadline exceeded')
                            yield
                            return
                        _unlock(active)
                        acquired = False
                    time.sleep(min(.05, max(0, deadline - time.monotonic())))
            finally:
                if acquired:
                    _unlock(active)
    finally:
        if marker is not None:
            # Close before unlink for Windows. A stale-scan can safely remove it.
            marker.close()
        if marker_path is not None:
            try:
                marker_path.unlink(missing_ok=True)
            except PermissionError:
                # A Windows stale-scan may briefly have it open. The closed
                # marker owns nothing; the next scan can discard it safely.
                pass
