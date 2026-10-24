"""Conflict-aware application with backups and a durable no-replay journal."""
from __future__ import annotations
import hashlib
import os
import re
import uuid
from pathlib import Path
from .autonomous_store import write_json
from .project_policy import source_path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def replace_bytes(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.candidate')
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(value); stream.flush(); os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def apply_verified_changes(root, changes, baseline_hashes, receipt_dir, verify_callback):
    if not isinstance(changes, dict) or not 1 <= len(changes) <= 12 or set(changes) != set(baseline_hashes):
        raise ValueError('Invalid application scope')
    if any(not isinstance(s, str) for s in changes.values()) or sum(len(s.encode()) for s in changes.values()) > 64000:
        raise ValueError('Application exceeds source budget')
    targets = {p:source_path(root, p) for p in changes}
    for value in baseline_hashes.values():
        if value is not None and (not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value)):
            raise ValueError('Invalid baseline hash')
    receipt_dir = Path(receipt_dir).absolute()
    if receipt_dir.exists() or any(p.is_symlink() for p in (receipt_dir, *receipt_dir.parents)):
        raise ValueError('Application receipt cannot be reused')
    receipt_dir.mkdir(parents=True, mode=0o700)
    result = {'passed':False, 'status':'prepared', 'files':{}, 'evidence':{}}
    applied = []
    def journal(status):
        result['status'] = status
        write_json(receipt_dir / 'journal.json', result)
    for path, target in targets.items():
        current = digest(target)
        if current != baseline_hashes[path]:
            result['conflict'] = path; journal('baseline_conflict'); return result
        result['files'][path] = {'before_sha256':current, 'after_sha256':hashlib.sha256(changes[path].encode()).hexdigest()}
    for path, target in targets.items():
        if baseline_hashes[path] is not None:
            replace_bytes(receipt_dir / 'backup' / path, target.read_bytes())
    journal('prepared')
    try:
        journal('applying')
        for path, target in targets.items():
            target = source_path(root, path)
            if digest(target) != baseline_hashes[path]:
                raise RuntimeError('Concurrent source change')
            replace_bytes(target, changes[path].encode()); applied.append(path)
        journal('verifying')
        outcome = verify_callback()
        if not isinstance(outcome, dict) or outcome.get('passed') is not True or not outcome.get('evidence'):
            raise RuntimeError('Installed verification failed')
        if any(digest(targets[p]) != result['files'][p]['after_sha256'] for p in applied):
            raise RuntimeError('Concurrent source change during verification')
        result.update(passed=True, evidence=outcome['evidence']); journal('completed'); return result
    except Exception as exc:
        result['error'] = type(exc).__name__
        conflict = []
        for path in reversed(applied):
            try:
                target = source_path(root, path)
            except ValueError:
                conflict.append(path); continue
            if digest(target) != result['files'][path]['after_sha256']:
                conflict.append(path); continue
            if baseline_hashes[path] is None:
                target.unlink()
            else:
                replace_bytes(target, (receipt_dir / 'backup' / path).read_bytes())
        result['rollback_conflicts'] = conflict
        journal('rollback_conflict' if conflict else 'rolled_back')
        return result
