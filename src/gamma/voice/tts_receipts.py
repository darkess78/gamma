import json as _json
import os as _os
import re as _re
import tempfile as _tmp
from pathlib import Path as _Path

_JOB = _re.compile(r"[0-9a-f]{32}\Z")
_MAX = 8192


def _check(jid):
    if not isinstance(jid, str) or not _JOB.fullmatch(jid):
        raise ValueError("job_id must be 32 lowercase hex")
    return jid


def save_receipt(root, job_id, record):
    jid = _check(job_id)
    blob = _json.dumps({**record, "job_id": jid}).encode("utf-8")
    if len(blob) > _MAX:
        raise ValueError("receipt exceeds 8192 bytes")
    r = _Path(root)
    r.mkdir(parents=True, exist_ok=True)
    fd, tmp = _tmp.mkstemp(dir=r)
    try:
        with _os.fdopen(fd, "wb") as f:
            f.write(blob)
            f.flush()
            _os.fsync(f.fileno())
        _os.replace(tmp, str(r / f"{jid}.json"))
    finally:
        try:
            if _os.path.lexists(tmp):
                _os.unlink(tmp)
        except OSError:
            pass


def load_receipt(root, job_id):
    jid = _check(job_id)
    tgt = _Path(root) / f"{jid}.json"
    if _os.path.islink(tgt):
        raise ValueError("receipt is a symlink")
    st = _os.stat(tgt)
    import stat as _stat
    if not _stat.S_ISREG(st.st_mode):
        raise IOError("receipt is not a regular file")
    fd = _os.open(tgt, _os.O_RDONLY | getattr(_os, "O_NOFOLLOW", 0) | getattr(_os, "O_NONBLOCK", 0))
    with _os.fdopen(fd, "rb") as f:
        if not _stat.S_ISREG(_os.fstat(f.fileno()).st_mode):
            raise IOError("receipt is not a regular file")
        blob = f.read(_MAX + 1)
    if len(blob) > _MAX:
        raise ValueError("receipt exceeds 8192 bytes")
    obj = _json.loads(blob.decode("utf-8"))
    if not isinstance(obj, dict) or obj.get("job_id") != jid:
        raise ValueError("receipt job_id mismatch")
    return obj
