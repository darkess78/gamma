"""Retain executed feature checks without overwriting owner-owned tests."""
import hashlib
import os
import re
import uuid
from pathlib import Path


def _safe(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Unsafe acceptance storage')
    return path


def rollback_checks(records):
    conflicts = []
    for record in reversed(records):
        if not record['created']:
            continue
        try:
            path = _safe(record['path'])
            if not path.name.startswith('test_verified_') or hashlib.sha256(path.read_bytes()).hexdigest() != record['sha256']:
                conflicts.append(str(path)); continue
            path.unlink()
        except (OSError, ValueError):
            conflicts.append(record['path'])
    return conflicts


def retain_checks(project, workspace, targets, identifier):
    if not re.fullmatch(r'work-[0-9a-f]{32}', identifier) or not 1 <= len(targets) <= 12:
        raise ValueError('Invalid retained acceptance scope')
    root = _safe(Path(project) / 'tests')
    root.mkdir(mode=0o700, exist_ok=True)
    if not root.is_dir():
        raise ValueError('Protected test directory unavailable')
    records = []
    try:
        for relative in targets:
            if not re.fullmatch(r'tests/test_goal_[a-zA-Z0-9_-]{1,150}\.py', relative):
                raise ValueError('Invalid retained acceptance path')
            source = _safe(Path(workspace) / relative)
            raw = source.read_bytes()
            if not 1 <= len(raw) <= 16000:
                raise ValueError('Invalid retained acceptance size')
            name = 'test_verified_' + identifier[5:] + '_' + source.name.replace('-', '_')
            target = _safe(root / name)
            digest = hashlib.sha256(raw).hexdigest()
            created = False
            if target.exists():
                if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                    raise ValueError('Existing owner test must not be replaced')
            else:
                temporary = root / ('.verified-' + uuid.uuid4().hex + '.tmp')
                fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                try:
                    with os.fdopen(fd, 'wb') as stream:
                        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
                    # Link the complete file exclusively; an owner race cannot
                    # cause a replacement, and readers never see partial bytes.
                    os.link(temporary, target, follow_symlinks=False)
                    created = True
                finally:
                    temporary.unlink(missing_ok=True)
            records.append({'path':str(target), 'sha256':digest, 'created':created})
        return records
    except Exception:
        rollback_checks(records)
        raise
