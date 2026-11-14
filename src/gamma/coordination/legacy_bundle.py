"""Fixed, bounded capture of drained legacy Gamma stores into private evidence.

This module never stops services, opens live SQLite, imports live configuration,
restores old controls or grants activation authority.
"""
from contextlib import closing
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import socket
import time
import uuid

from .legacy_bundle_inventory import fingerprint, stat_fields
from .private_release_manifest import UNITS, MODULE_COMPONENTS, observe_target
from .store import private_path


DEFAULT_SOURCE = Path('/home/neety/Documents/gamma-main')
DATABASE = 'data/memory/gamma.db'
AUXILIARY = (
    'data/assistant_state.json', 'data/assistant_emotion_memory.json',
    'src/gamma/persona/relationship_state.json', 'config/persona.yaml',
    'src/gamma/persona/core.md', 'src/gamma/persona/style.json', 'data/core_memories.md',
)
ARCHIVE_ROOTS = ('data/memory', 'data/coding/practice', 'data/coding/lessons',
                 'data/coding/projects', 'data/coding/autonomous',
                 'data/coding/candidates', 'data/improvement')
OPERATIONAL_FILES = ('data/runtime/presence/state.json', 'data/coding/idle-state.json')
LEGACY_CODE_REQUIRED = ('src/gamma/main.py', 'src/gamma/dashboard/main.py',
                        'src/gamma/memory/service.py', 'src/gamma/persona/loader.py')
MAX_FILES = 30_000
MAX_ENTRIES = 60_000
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_FILE_BYTES = 128 * 1024 * 1024
FREE_RESERVE = 512 * 1024 * 1024
METADATA_RESERVE = 64 * 1024 * 1024
MAX_SECONDS = 900


class CaptureError(ValueError):
    """Content-free failure code; a partial private destination is retained."""

    def __init__(self, code, destination=None):
        super().__init__(code)
        self.code = code
        self.destination = str(destination) if destination is not None else None


def _now():
    return datetime.now(timezone.utc).isoformat()


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def _deadline(end):
    if time.monotonic() > end:
        raise CaptureError('legacy_capture_deadline_exceeded')


def _root(value):
    supplied = Path(value)
    if not supplied.is_absolute():
        raise CaptureError('legacy_source_must_be_absolute')
    path = private_path(supplied)
    if path == Path(path.anchor) or not path.is_dir():
        raise CaptureError('legacy_source_must_be_repository')
    for relative in LEGACY_CODE_REQUIRED:
        info = private_path(path / relative).lstat()
        if not stat.S_ISREG(info.st_mode):
            raise CaptureError('legacy_source_required_code_missing')
    return path


def _safe_name(path):
    for part in path.parts:
        lower = part.lower()
        if ('\\' in part or any(ord(c) < 32 for c in part)
                or part in {'.', '..'}):
            raise CaptureError('legacy_member_name_not_portable')
        env_example = lower in {'.env.example', '.env.template', '.env.sample'}
        if ((lower == '.env' or lower.startswith('.env.')) and not env_example
                or lower in {'app.local.toml', 'voices.local.toml', 'id_rsa', 'id_dsa',
                             'id_ecdsa', 'id_ed25519'}
                or lower.endswith(('.pem', '.key', '.p12', '.pfx'))):
            raise CaptureError('legacy_secret_or_local_config_path_present')


def drain_observations(observer=observe_target):
    targets = {name: {'kind': 'user_unit', 'name': name} for name in UNITS}
    targets.update({name: {'kind': 'module_census', 'name': name} for name in MODULE_COMPONENTS})
    observations = {}
    for name, target in targets.items():
        try:
            result = observer(target)
        except Exception:
            result = {}
        # Do not retain arbitrary diagnostics, command lines or environment values.
        entry = {'inactive_observed': isinstance(result, dict) and result.get('inactive_observed') is True}
        if isinstance(result, dict):
            if isinstance(result.get('matching_pids'), list):
                entry['matching_pids'] = [pid for pid in result['matching_pids'] if type(pid) is int and pid > 0][:30_000]
            values = result.get('observed')
            if isinstance(values, dict):
                entry['observed'] = {key: values[key] for key in ('ActiveState', 'SubState', 'LoadState', 'MainPID')
                    if isinstance(values.get(key), str) and len(values[key]) <= 40
                    and all(c.isalnum() or c in '_-' for c in values[key])}
        observations[name] = entry
    return observations


def status(source=DEFAULT_SOURCE, *, observer=observe_target):
    """Observe named writers and fixed paths. No bundle or inventory is created."""
    root = _root(source)
    observations = drain_observations(observer)
    return {'status': 'drained_observed' if all(x['inactive_observed'] for x in observations.values()) else 'blocked',
            'action': 'read_only_status', 'source_root': str(root), 'observations': observations,
            'fixed_database': DATABASE, 'auxiliary_paths': list(AUXILIARY),
            'archive_only_roots': list(ARCHIVE_ROOTS), 'optional_archive_only_files': list(OPERATIONAL_FILES),
            'activation_authority': False, 'global_os_fence': False}


def _census(root, end):
    entries = {}
    files = 0
    total = 0

    def visit(relative):
        nonlocal files, total
        _deadline(end)
        if relative in entries:
            return
        if len(entries) >= MAX_ENTRIES:
            raise CaptureError('legacy_inventory_entry_limit')
        _safe_name(Path(relative))
        path = private_path(root / relative)
        try:
            info = path.lstat()
        except FileNotFoundError:
            entries[relative] = {'kind': 'missing'}
            return
        if stat.S_ISREG(info.st_mode):
            files += 1
            if files > MAX_FILES or total + info.st_size > MAX_TOTAL_BYTES:
                raise CaptureError('legacy_inventory_size_or_count_limit')
            proof = fingerprint(path, limit=MAX_FILE_BYTES)
            total += proof['size']
            if total > MAX_TOTAL_BYTES:
                raise CaptureError('legacy_inventory_size_or_count_limit')
            entries[relative] = {'kind': 'file', **proof}
        elif stat.S_ISDIR(info.st_mode):
            expected = stat_fields(info)
            entries[relative] = {'kind': 'directory', **expected}
            with os.scandir(path) as listing:
                names = []
                for item in listing:
                    names.append(item.name)
                    if len(names) > MAX_ENTRIES:
                        raise CaptureError('legacy_inventory_entry_limit')
            for name in sorted(names):
                visit((Path(relative) / name).as_posix())
            if stat_fields(private_path(path).lstat()) != expected:
                raise CaptureError('legacy_directory_changed_during_census')
        else:
            raise CaptureError('legacy_symlink_or_special_file')

    for relative in (*ARCHIVE_ROOTS, *AUXILIARY, *OPERATIONAL_FILES):
        visit(relative)
    if entries.get(DATABASE, {}).get('kind') != 'file':
        raise CaptureError('legacy_primary_database_missing')
    return {'entries': entries, 'file_count': files, 'total_bytes': total}


def _code_inventory(root, end):
    """Legacy counterpart of the release pin; no release-only files are required."""
    files = {}
    total = 0
    directories = 0

    def add(path):
        nonlocal total
        _deadline(end)
        relative = path.relative_to(root).as_posix()
        _safe_name(Path(relative))
        proof = fingerprint(path, limit=MAX_FILE_BYTES)
        files[relative] = proof['sha256']
        total += proof['size']
        if len(files) > MAX_FILES or total > MAX_TOTAL_BYTES:
            raise CaptureError('legacy_code_inventory_limit')

    def walk(path):
        nonlocal directories
        _deadline(end)
        directories += 1
        if directories > MAX_ENTRIES:
            raise CaptureError('legacy_code_inventory_limit')
        info = private_path(path).lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise CaptureError('legacy_code_directory_invalid')
        with os.scandir(path) as listing:
            items = []
            for item in listing:
                items.append(item)
                if len(items) > MAX_ENTRIES:
                    raise CaptureError('legacy_code_inventory_limit')
        items.sort(key=lambda x: x.name)
        for item in items:
            _deadline(end)
            candidate = path / item.name
            if item.name == '__pycache__' or candidate.suffix == '.pyc':
                continue
            info = candidate.lstat()
            if stat.S_ISDIR(info.st_mode):
                walk(candidate)
            elif stat.S_ISREG(info.st_mode):
                add(candidate)
            else:
                raise CaptureError('legacy_code_symlink_or_special_file')

    walk(root / 'src/gamma')
    add(root / 'pyproject.toml')
    config = private_path(root / 'config')
    with os.scandir(config) as listing:
        items = []
        for item in listing:
            items.append(item)
            if len(items) > MAX_ENTRIES:
                raise CaptureError('legacy_code_inventory_limit')
    items.sort(key=lambda x: x.name)
    for item in items:
        path = config / item.name
        if path.name.endswith('.local.toml') or path.name.startswith('.env'):
            continue
        if path.suffix in {'.toml', '.yaml', '.json'}:
            add(path)
    if not set(LEGACY_CODE_REQUIRED) <= set(files):
        raise CaptureError('legacy_source_required_code_missing')
    return {'files': files, 'files_sha256': _digest(files)}


def _write_json(path, value):
    temporary = path.with_name(path.name + '.next-' + uuid.uuid4().hex)
    data = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if len(data) > METADATA_RESERVE:
        raise CaptureError('legacy_metadata_limit')
    descriptor = os.open(private_path(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL
                         | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(descriptor, 'wb') as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def _mkdir(path):
    path = private_path(path)
    missing = []
    parent = path
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
    if os.name == 'posix' and (path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077):
        raise CaptureError('legacy_destination_directory_not_private')


def _copy_exact(source, destination, expected, end):
    _deadline(end)
    source = private_path(source)
    _mkdir(destination.parent)
    read_fd = write_fd = None
    try:
        read_fd = os.open(source, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        opened = os.fstat(read_fd)
        expected_stat = {key: value for key, value in expected.items() if key not in {'sha256', 'kind'}}
        if not stat.S_ISREG(opened.st_mode) or stat_fields(opened) != expected_stat:
            raise CaptureError('legacy_source_changed_before_copy')
        write_fd = os.open(private_path(destination), os.O_WRONLY | os.O_CREAT | os.O_EXCL
                           | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        hashed, copied = hashlib.sha256(), 0
        while chunk := os.read(read_fd, 65536):
            _deadline(end)
            copied += len(chunk)
            if copied > MAX_FILE_BYTES or copied > expected['size']:
                raise CaptureError('legacy_source_grew_during_copy')
            if shutil.disk_usage(destination.parent).free < FREE_RESERVE + len(chunk):
                raise CaptureError('legacy_destination_free_reserve_lost')
            hashed.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(write_fd, view)
                if written <= 0:
                    raise CaptureError('legacy_destination_write_failed')
                view = view[written:]
        os.fsync(write_fd)
        named = private_path(source).lstat()
        if (stat_fields(os.fstat(read_fd)) != expected_stat or not stat.S_ISREG(named.st_mode)
                or stat_fields(named) != expected_stat or copied != expected['size']
                or hashed.hexdigest() != expected['sha256']):
            raise CaptureError('legacy_source_changed_during_copy')
        copied_proof = fingerprint(destination, limit=MAX_FILE_BYTES)
        if (copied_proof['sha256'] != expected['sha256'] or copied_proof['size'] != expected['size']
                or copied_proof['mode'] != 0o600
                or (copied_proof['device'], copied_proof['inode']) == (expected['device'], expected['inode'])):
            raise CaptureError('legacy_copy_bytes_or_independence_failed')
    finally:
        if write_fd is not None:
            os.close(write_fd)
        if read_fd is not None:
            os.close(read_fd)


def _standalone_database(destination, end):
    scratch = destination / 'scratch'
    _mkdir(scratch)
    for suffix in ('', '-wal', '-journal'):
        raw = destination / 'raw' / (DATABASE + suffix)
        if raw.exists():
            proof = fingerprint(raw, limit=MAX_FILE_BYTES)
            _copy_exact(raw, scratch / ('memory.sqlite3' + suffix), proof, end)
    output = destination / 'database/memory.sqlite3'
    _mkdir(output.parent)
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    # SQLite may recover WAL/journal files only here, never at the source/raw paths.
    with closing(sqlite3.connect((scratch / 'memory.sqlite3').as_uri() + '?mode=rw', uri=True, timeout=1)) as source:
        source.set_progress_handler(lambda: int(time.monotonic() > end), 1000)
        with closing(sqlite3.connect(output, timeout=1)) as copied:
            copied.set_progress_handler(lambda: int(time.monotonic() > end), 1000)
            def progress(*_):
                _deadline(end)
                if output.stat().st_size > MAX_FILE_BYTES:
                    raise CaptureError('legacy_standalone_database_limit')
                if shutil.disk_usage(destination).free < FREE_RESERVE:
                    raise CaptureError('legacy_destination_free_reserve_lost')
            source.backup(copied, pages=256, progress=progress, sleep=.01)
            copied.execute('PRAGMA journal_mode=DELETE')
            copied.execute('PRAGMA query_only=ON')
            if copied.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise CaptureError('legacy_standalone_integrity_failed')
            tables = {}
            for name, in copied.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                _deadline(end)
                if len(tables) >= MAX_FILES:
                    raise CaptureError('legacy_schema_table_limit')
                quoted = '"' + name.replace('"', '""') + '"'
                tables[name] = {'row_count': copied.execute('SELECT COUNT(*) FROM ' + quoted).fetchone()[0]}
    proof = fingerprint(output, limit=MAX_FILE_BYTES)
    return {'source_path': DATABASE, 'snapshot_path': 'database/memory.sqlite3',
            'snapshot_sha256': proof['sha256'], 'bytes': proof['size'],
            'integrity_check': 'ok', 'online_backup_consistent': True,
            'recovery_scope': 'private_scratch_only'}, tables


def capture(source, destination, *, observer=observe_target):
    """Explicit capture only. The observer injection is for controlled fixture tests."""
    root = _root(source)
    supplied = Path(destination)
    if not supplied.is_absolute():
        raise CaptureError('legacy_destination_must_be_absolute')
    target = private_path(supplied)
    if target.is_relative_to(root) or root.is_relative_to(target):
        raise CaptureError('legacy_destination_must_be_outside_source')
    if not target.parent.is_dir():
        raise CaptureError('legacy_destination_parent_missing')
    if target.exists():
        raise CaptureError('legacy_destination_already_exists')
    if shutil.disk_usage(target.parent).free < FREE_RESERVE + METADATA_RESERVE:
        raise CaptureError('legacy_destination_free_reserve_missing')
    target.mkdir(mode=0o700)
    started, end = _now(), time.monotonic() + MAX_SECONDS
    capture_id, captured_by = uuid.uuid4().hex, getpass.getuser() + '@' + socket.gethostname()
    try:
        _write_json(target / 'capture-status.json', {'status': 'capturing', 'started_utc': started,
                    'capture_id': capture_id, 'captured_by': captured_by})
        before_drain = drain_observations(observer)
        if not all(item['inactive_observed'] for item in before_drain.values()):
            raise CaptureError('legacy_writers_not_drained')
        before = _census(root, end)
        code_before = _code_inventory(root, end)
        # Leave room for raw evidence, independent recovery copies and metadata.
        db_working = sum(before['entries'].get(DATABASE + suffix, {}).get('size', 0)
                         for suffix in ('', '-wal', '-journal'))
        required = before['total_bytes'] + db_working + MAX_FILE_BYTES + FREE_RESERVE + METADATA_RESERVE
        if shutil.disk_usage(target).free < required:
            raise CaptureError('legacy_destination_capacity_insufficient')
        _write_json(target / 'inventory.json', {'format': 1, 'status': 'copying', 'before': before,
                    'capture_id': capture_id, 'captured_by': captured_by,
                    'source_code': code_before, 'archive_only_roots': list(ARCHIVE_ROOTS),
                    'optional_archive_only_files': list(OPERATIONAL_FILES), 'execution_authority': False})
        for relative, entry in sorted(before['entries'].items()):
            if entry['kind'] == 'directory':
                _mkdir(target / 'raw' / relative)
            elif entry['kind'] == 'file':
                _copy_exact(root / relative, target / 'raw' / relative, entry, end)
        database, tables = _standalone_database(target, end)
        after = _census(root, end)
        code_after = _code_inventory(root, end)
        after_drain = drain_observations(observer)
        if before != after:
            raise CaptureError('legacy_runtime_inventory_changed')
        if code_before != code_after:
            raise CaptureError('legacy_source_code_changed')
        if not all(item['inactive_observed'] for item in after_drain.values()):
            raise CaptureError('legacy_writer_appeared_during_capture')
        _deadline(end)
        inventory = {'format': 1, 'status': 'complete', 'before': before, 'after': after,
            'capture_id': capture_id, 'captured_by': captured_by,
            'paired_equal': True, 'source_code': code_before, 'source_code_after_sha256': code_after['files_sha256'],
            'archive_only_roots': list(ARCHIVE_ROOTS), 'optional_archive_only_files': list(OPERATIONAL_FILES),
            'execution_authority': False}
        _write_json(target / 'inventory.json', inventory)
        copied_files = [{'relative_path': relative, 'snapshot_path': 'raw/' + relative,
            'sha256': before['entries'][relative]['sha256']} for relative in AUXILIARY
            if before['entries'][relative]['kind'] == 'file']
        manifest = {'format': 1, 'status': 'complete', 'source_root': str(root),
            'capture_id': capture_id, 'captured_by': captured_by,
            'captured_started_utc': started, 'captured_finished_utc': _now(),
            'source_namespace': 'gamma-main:personal-memory', 'privacy_scope': 'owner_private',
            'database': database, 'tables': tables, 'copied_files': copied_files,
            'missing_files': [relative for relative in AUXILIARY if before['entries'][relative]['kind'] == 'missing'],
            'inventory_path': 'inventory.json', 'inventory_sha256': fingerprint(target / 'inventory.json', limit=METADATA_RESERVE)['sha256'],
            'source_code_files_sha256': code_before['files_sha256'],
            'drain_observations_before': before_drain, 'drain_observations_after': after_drain,
            'bundle_atomic_across_legacy_stores': False, 'enumerated_writers_quiescent_observed': True,
            'global_os_fence': False, 'production_activated': False, 'execution_authority_granted': False,
            'coherence_limit': 'Paired bounded inventory and enumerated drain observations; no global OS atomicity or unknown-writer fence.'}
        _write_json(target / 'manifest.json', manifest)
        _write_json(target / 'capture-status.json', {'status': 'complete', 'started_utc': started,
            'capture_id': capture_id, 'captured_by': captured_by,
            'finished_utc': _now(), 'file_count': before['file_count'], 'total_bytes': before['total_bytes']})
        return {'status': 'complete', 'destination': str(target), 'file_count': before['file_count'],
            'total_bytes': before['total_bytes'], 'table_count': len(tables),
            'global_atomic': False, 'activated': False}
    except BaseException as exc:
        code = exc.code if isinstance(exc, CaptureError) else 'legacy_capture_' + type(exc).__name__
        try:
            _write_json(target / 'capture-status.json', {'status': 'failed', 'started_utc': started,
                'capture_id': capture_id, 'captured_by': captured_by,
                'finished_utc': _now(), 'failure_code': code, 'partial_evidence_retained': True})
        except Exception:
            pass
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise CaptureError(code, target) from None


def inspect_bundle(destination):
    """Read declared status only; this does not verify every archived file."""
    root = private_path(destination)
    path = private_path(root / 'capture-status.json')
    if path.stat().st_size > 65_536:
        raise CaptureError('legacy_status_file_limit')
    value = json.loads(private_path(path).read_text(encoding='utf-8'))
    return {'status': value.get('status'), 'destination': str(root),
            'file_count': value.get('file_count'), 'total_bytes': value.get('total_bytes'),
            'failure_code': value.get('failure_code'), 'archive_hashes_reverified': False,
            'activation_authority': False}
