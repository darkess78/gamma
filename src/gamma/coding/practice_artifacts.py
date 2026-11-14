import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

_REF_RE = re.compile(r'^[0-9a-f]{64}$')
_MAX_BYTES = 262144


def _canonical_bytes(obj):
    if not isinstance(obj, dict):
        raise ValueError('artifact must be a dict')
    data = json.dumps(
        obj,
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=False,
        allow_nan=False,
    ).encode('utf-8')
    if len(data) > _MAX_BYTES:
        raise ValueError('artifact exceeds maximum size')
    return data


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_ref(ref: str) -> None:
    if not isinstance(ref, str) or not _REF_RE.fullmatch(ref):
        raise ValueError('invalid artifact ref')


def _fsync_dir(path: Path) -> None:
    if os.name == 'posix':
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class ArtifactStore:
    def __init__(self, root: Path):
        self.root = Path(root).absolute()
        if self.root.is_symlink():
            raise ValueError('root must not be a symlink')
        # Walk ancestors to ensure none are symlinks
        p = self.root
        while p != p.parent:
            if p.is_symlink():
                raise ValueError('root ancestor must not be a symlink')
            p = p.parent
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _target_path(self, ref: str) -> Path:
        _validate_ref(ref)
        if any(p.is_symlink() for p in (self.root, *self.root.parents)):
            raise ValueError('artifact storage changed to a symlink')
        return self.root / (ref + '.json')

    def put(self, obj: dict) -> str:
        data = _canonical_bytes(obj)
        ref = _sha256(data)
        target = self._target_path(ref)

        if target.is_symlink():
            raise ValueError('target must not be a symlink')
        if target.exists():
            self.get(ref)
            return ref

        # Write to a unique temp file in the same directory, then atomically rename
        fd, tmp_path = tempfile.mkstemp(dir=str(self.root), prefix='.tmp_', suffix='.json')
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            try:
                # Link publication is atomic and never overwrites an existing
                # immutable artifact, including one another writer just created.
                os.link(tmp_path, target)
            except FileExistsError:
                self.get(ref)
            os.unlink(tmp_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        _fsync_dir(self.root)
        return ref

    def get(self, ref: str) -> dict:
        _validate_ref(ref)
        target = self._target_path(ref)
        if not target.exists():
            raise ValueError('artifact not found')
        if target.is_symlink():
            raise ValueError('artifact target must not be a symlink')
        fd = os.open(target, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as stream:
            data = stream.read(_MAX_BYTES + 1)
        if len(data) > _MAX_BYTES:
            raise ValueError('artifact exceeds maximum size')
        if _sha256(data) != ref:
            raise ValueError('artifact hash mismatch (tamper detected)')
        def reject_constant(_value):
            raise ValueError('nonfinite artifact JSON')
        obj = json.loads(data.decode('utf-8'), parse_constant=reject_constant)
        if not isinstance(obj, dict):
            raise ValueError('artifact must be a dict')
        return obj
