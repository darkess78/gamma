"""Durable owner goals for autonomous repository work."""
from __future__ import annotations

import json
import logging
import math
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


_HEX_RE = re.compile(r'^[0-9a-f]{64}$')


def _valid_completed_receipt(value):
    """Pure shape check only. Caller checks fixed paths and current hashes."""
    if type(value) is not dict:
        return False
    if set(value.keys()) != {'passed', 'status', 'files', 'evidence'}:
        return False
    if value['passed'] is not True:
        return False
    if value['status'] != 'completed' or not isinstance(value['status'], str):
        return False
    files = value['files']
    if type(files) is not dict or not (1 <= len(files) <= 12):
        return False
    for k, v in files.items():
        if not isinstance(k, str):
            return False
        if type(v) is not dict:
            return False
        if set(v.keys()) != {'before_sha256', 'after_sha256'}:
            return False
        before = v['before_sha256']
        if before is not None:
            if not isinstance(before, str) or not _HEX_RE.fullmatch(before):
                return False
        after = v['after_sha256']
        if not isinstance(after, str) or not _HEX_RE.fullmatch(after):
            return False
    evidence = value['evidence']
    if type(evidence) is not dict or len(evidence) == 0:
        return False
    return True


def _recovery_bytes(path, maximum):
    """Bound regular-file reads; do not follow a receipt/source symlink."""
    import stat
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('invalid_application_evidence')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise ValueError('invalid_application_evidence')
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError('invalid_application_evidence')
    return raw


def _receipt_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('invalid_application_evidence')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('invalid_application_evidence')


def _interrupted_application(path, project):
    """Read fixed local evidence. No callback, replay or whole-job completion."""
    from .project_policy import source_path
    try:
        raw = _recovery_bytes(path / 'application' / 'journal.json', 2_000_000)
    except FileNotFoundError:
        return 'missing', None
    except (OSError, ValueError):
        return 'invalid', None
    try:
        receipt = json.loads(raw, object_pairs_hook=_receipt_object, parse_constant=_invalid_constant)
        if type(receipt) is not dict:
            return 'invalid', None
        if _valid_completed_receipt(receipt):
            # Validate every scope before reading any installed source.
            targets = [(source_path(project, name), item['after_sha256'])
                       for name, item in receipt['files'].items()]
            for target, expected in targets:
                try:
                    current = _recovery_bytes(target, 64000)
                except FileNotFoundError:
                    return 'source_conflict', None
                if hashlib.sha256(current).hexdigest() != expected:
                    return 'source_conflict', None
            return 'completed_current', receipt
        known = {'prepared', 'applying', 'verifying', 'rolled_back', 'rollback_conflict', 'baseline_conflict'}
        if (type(receipt.get('status')) is not str or receipt['status'] not in known
                or receipt.get('passed') is not False
                or not {'passed', 'status', 'files', 'evidence'} <= set(receipt)
                or not set(receipt) <= {'passed', 'status', 'files', 'evidence', 'error', 'conflict', 'rollback_conflicts'}
                or type(receipt['files']) is not dict or len(receipt['files']) > 12
                or type(receipt['evidence']) is not dict):
            return 'invalid', None
        shape = {**receipt, 'passed': True, 'status': 'completed', 'evidence': {'shape_check': True}}
        for key in ('error', 'conflict', 'rollback_conflicts'):
            shape.pop(key, None)
        if receipt['files'] and not _valid_completed_receipt(shape):
            return 'invalid', None
        for name in receipt['files']:
            source_path(project, name)
        if 'conflict' in receipt:
            source_path(project, receipt['conflict'])
        if 'rollback_conflicts' in receipt:
            conflicts = receipt['rollback_conflicts']
            if type(conflicts) is not list or len(conflicts) > 12:
                return 'invalid', None
            for name in conflicts:
                source_path(project, name)
        if 'error' in receipt and (type(receipt['error']) is not str or len(receipt['error']) > 80):
            return 'invalid', None
        status = receipt['status']
        return (status if status in {'rolled_back', 'rollback_conflict', 'baseline_conflict'} else 'incomplete'), None
    except (OSError, ValueError, TypeError, RecursionError):
        return 'invalid', None


def reconcile_interrupted_applications(root, project):
    """Called under the executor lock. Keep interrupted work and effects intact."""
    for record in list_jobs(root):
        if record.get('status') not in {'running', 'paused', 'interrupted'}:
            continue
        before = dict(record)
        path = job_path(root, record['id'])
        status, receipt = _interrupted_application(path, project)
        if record['status'] in {'running', 'paused'}:
            record.update(status='interrupted', reason='Previous worker exited during execution; review saved evidence')
        record['application_recovery'] = {'status': status}
        if receipt is not None:
            record['application'] = receipt
        else:
            record.pop('application', None)
        try:
            if len(json.dumps(record, ensure_ascii=False, indent=2).encode()) > 2_000_000:
                record.pop('application', None)
                record['application_recovery'] = {'status': 'record_budget'}
            if record != before:
                write_json(path / 'state.json', record)
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            # Keep the original state if even the minimal update cannot persist.
            # A failure for one record must not stop recovery of other jobs.
            logging.getLogger(__name__).warning(
                'coding_recovery_persistence_failed job_id=%s exception=%s',
                record['id'], type(exc).__name__,
            )
            continue
