"""Durable owner coding goals: one folder per job with state.json, control.json and proposal.diff."""
from __future__ import annotations

import fcntl
import json
import logging
import math
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
    record = json.loads(path.read_text(encoding='utf-8'))
    if type(record) is not dict or record.get('id') != identifier:
        raise ValueError('Unsafe work record')
    return record


def list_jobs(root):
    root = safe_root(root)
    records = []
    for path in sorted(root.glob('work-*'), key=lambda p: p.name):
        try:
            record = read_job(root, path.name)
            created = record.get('created_at')
            try:
                valid_created = type(created) in (int, float) and math.isfinite(created)
            except OverflowError:
                valid_created = False
            if not valid_created:
                logging.getLogger(__name__).warning('coding_job_ordering_invalid job_id=%s', path.name)
                continue
            records.append(record)
        except (OSError, ValueError, TypeError, OverflowError):
            continue
    return sorted(records, key=lambda r: r['created_at'], reverse=True)[:100]


def create_job(root, goal, model='qwen3.8:27b', budget_seconds=1800):
    if not isinstance(goal, str) or not 12 <= len(goal.strip()) <= 2000:
        raise ValueError('A concrete goal is required')
    if type(budget_seconds) is not int or not 60 <= budget_seconds <= 28800:
        raise ValueError('Invalid time budget')
    if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/+-]{0,199}', model):
        raise ValueError('Invalid local model')
    if any(r['status'] in ACTIVE for r in list_jobs(root)):
        raise ValueError('An improvement goal is already active')
    record = dict(id='work-' + uuid.uuid4().hex, goal=goal.strip(), model=model, budget_seconds=budget_seconds,
                  status='queued', stage='queued', created_at=time.time(), events=[], tasks=[])
    path = job_path(root, record['id'])
    path.mkdir(mode=0o700)
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


PROPOSAL_LIMIT_BYTES = 2_000_000


def read_proposal(root, identifier):
    """Return the verified diff a job saved for owner review."""
    record = read_job(root, identifier)
    if record.get('proposal') != 'proposal.diff':
        raise ValueError('No proposal for this work')
    path = job_path(root, identifier) / 'proposal.diff'
    if path.is_symlink() or not path.is_file() or path.stat().st_size > PROPOSAL_LIMIT_BYTES:
        raise ValueError('Proposal unavailable')
    return path.read_text(encoding='utf-8', errors='replace')


@contextmanager
def execution_lock(root):
    """The OS releases this lock after process exit; uncertain work is not replayed."""
    path = safe_root(root) / '.executor.lock'
    if path.is_symlink():
        raise ValueError('Unsafe executor lock')
    with path.open('a+b') as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
