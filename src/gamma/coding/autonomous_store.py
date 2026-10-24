"""Durable owner goals for autonomous repository work."""
from __future__ import annotations

import json
import hashlib
import os
import re
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

ACTIVE = {'queued', 'running', 'deferred', 'paused'}


def safe_root(root):
    root = Path(root).absolute()
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError('Unsafe work storage')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def job_path(root, identifier):
    if not isinstance(identifier, str) or not re.fullmatch(r'work-[0-9a-f]{32}', identifier):
        raise ValueError('Invalid work ID')
    path = safe_root(root) / identifier
    if path.is_symlink():
        raise ValueError('Unsafe work record')
    return path


def write_json(path, value):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Unsafe work record')
    data = json.dumps(value, ensure_ascii=False, indent=2).encode()
    if len(data) > 2_000_000:
        raise ValueError('Work record too large')
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_job(root, identifier):
    path = job_path(root, identifier) / 'state.json'
    if path.is_symlink() or path.stat().st_size > 2_000_000:
        raise ValueError('Unsafe work record')
    return json.loads(path.read_text(encoding='utf-8'))


def list_jobs(root):
    root = safe_root(root)
    records = []
    for path in sorted(root.glob('work-*'), key=lambda p: p.name):
        try:
            records.append(read_job(root, path.name))
        except (OSError, ValueError, TypeError):
            continue
    return sorted(records, key=lambda r: r['created_at'], reverse=True)[:100]


def create_job(root, goal, model='qwen3.8:27b', checker_model='gpt-oss:20b', budget_seconds=1800, *, acceptance_tests=None, acceptance_mode='checker'):
    if not isinstance(goal, str) or not 12 <= len(goal.strip()) <= 2000:
        raise ValueError('A concrete goal is required')
    if type(budget_seconds) is not int or not 60 <= budget_seconds <= 28800:
        raise ValueError('Invalid time budget')
    for name in (model, checker_model):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/+-]{0,199}', name):
            raise ValueError('Invalid local model')
    if model == checker_model:
        raise ValueError('The checker must be a different model')
    contracts = {} if acceptance_tests is None else acceptance_tests
    if not isinstance(contracts, dict) or len(contracts) > 3:
        raise ValueError('Invalid owner acceptance checks')
    from .project_verification import acceptance_source
    for name, source in contracts.items():
        if not isinstance(name, str) or not re.fullmatch(r'test_goal_owner_[a-z0-9_]{1,60}\.py', name):
            raise ValueError('Invalid owner acceptance filename')
        acceptance_source(source)
    if len(json.dumps(contracts, ensure_ascii=False).encode()) > 64000:
        raise ValueError('Owner acceptance checks exceed the storage budget')
    if not isinstance(acceptance_mode, str) or acceptance_mode not in {'checker', 'owner_contract'} or (acceptance_mode == 'owner_contract' and not contracts):
        raise ValueError('Owner-contract mode requires frozen independent checks')
    if any(r['status'] in ACTIVE for r in list_jobs(root)):
        raise ValueError('An improvement goal is already active')
    record = dict(id='work-' + uuid.uuid4().hex, goal=goal.strip(), model=model,
                  checker_model=checker_model, budget_seconds=budget_seconds,
                  status='queued', stage='queued', created_at=time.time(), events=[],
                  tasks=[], model_weights_changed=False,
                  acceptance_mode=acceptance_mode,
                  owner_checks_sha256={name:hashlib.sha256(source.encode()).hexdigest() for name,source in contracts.items()})
    path = job_path(root, record['id'])
    path.mkdir(mode=0o700)
    if contracts:
        write_json(path / 'owner-checks.json', contracts)
    write_json(path / 'state.json', record)
    return record


def control_job(root, identifier, action):
    if action not in {'pause', 'resume', 'stop'}:
        raise ValueError('Invalid work control')
    record = read_job(root, identifier)
    if record['status'] not in ACTIVE:
        raise ValueError('Work is already terminal')
    path = job_path(root, identifier) / 'control.json'
    write_json(path, {'action': action, 'at': time.time()})
    return {'id': identifier, 'requested': action}


@contextmanager
def execution_lock(root):
    """The OS releases this lock after process exit; uncertain work is not replayed."""
    path = safe_root(root) / '.executor.lock'
    if path.is_symlink():
        raise ValueError('Unsafe executor lock')
    with path.open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            if path.stat().st_size == 0:
                stream.write(b'0'); stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            try:
                yield
            finally:
                stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
