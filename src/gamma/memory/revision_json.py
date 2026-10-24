"""Versioned JSON writes under a caller-owned cross-process lock."""
import json
import os
import tempfile
from pathlib import Path


class StaleRevision(ValueError):
    """Raised when an explicit revision does not match the current revision."""


def _strict_nonneg_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _current_revision(path: Path) -> int:
    if not path.exists():
        return 0
    data = json.loads(path.read_bytes())
    if not isinstance(data, dict):
        raise ValueError("stored JSON must be an object")
    if "revision" in data and not _strict_nonneg_int(data["revision"]):
        raise ValueError("stored revision must be a non-negative int")
    return data.get("revision", 0)


def write_state(path: Path, state: dict) -> dict:
    cur = _current_revision(path)
    if "revision" in state:
        if not _strict_nonneg_int(state["revision"]):
            raise ValueError("revision must be a non-negative int")
        if state["revision"] != cur:
            raise StaleRevision(f"expected {cur}, got {state['revision']}")
    out = dict(state)
    out["revision"] = cur + 1
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(out, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return out
