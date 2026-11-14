"""Synthetic filesystem/SQLite captures. No real drain or production capture."""
from contextlib import closing
import getpass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import socket
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from gamma.coordination import legacy_bundle as bundle
from gamma.coordination.core import Core
from gamma.coordination.personal_import import reconcile_snapshot
from gamma.coordination.store import Store
from gamma.schemas.response import AssistantResponse


def write(root, relative, value):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding='utf-8')
    return path


@pytest.fixture
def source(tmp_path):
    root = tmp_path / 'legacy'
    root.mkdir()
    for name in bundle.LEGACY_CODE_REQUIRED:
        write(root, name, '# synthetic legacy source\n')
    write(root, 'pyproject.toml', '[project]\nname="fixture"\n')
    write(root, 'config/app.toml', 'database_url="sqlite:///./data/memory/gamma.db"\n')
    write(root, 'config/persona.yaml', 'speech_style: "Fictional authored style"\n')
    write(root, 'data/assistant_state.json', '{"note":"PRIVATE_AUXILIARY_FIXTURE"}')
    write(root, 'src/gamma/persona/style.json', '{"tone":"Fictional"}')
    write(root, 'data/coding/autonomous/work-fixture/state.json', '{"goal":"PRIVATE_WORK_FIXTURE","status":"needs_checker"}')
    write(root, 'data/coding/lessons/lesson-fixture.json', '{"text":"PRIVATE_LESSON_FIXTURE","active":true}')
    write(root, 'data/runtime/presence/state.json', '{"mode":"wake","activity":{"current_turn_id":"fictional"}}')
    write(root, 'data/memory/history/private-backup.bin', 'PRIVATE_HISTORICAL_FIXTURE')
    path = root / bundle.DATABASE
    with closing(sqlite3_connect(path)) as db:
        db.execute('CREATE TABLE profilefact (id INTEGER PRIMARY KEY AUTOINCREMENT, fact_text TEXT, subject_type TEXT, confidence REAL)')
        db.execute("INSERT INTO profilefact (fact_text,subject_type,confidence) VALUES ('Fictional current memory','primary_user',0.4)")
        db.execute('CREATE TABLE conversationjournalowner (id INTEGER, payload TEXT)')
        db.execute("INSERT INTO conversationjournalowner VALUES (1,'PRIVATE_OWNER_FIXTURE')")
        db.commit()
    return root


def sqlite3_connect(path, **kwargs):
    return sqlite3.connect(path, **kwargs)


def drained(_target):
    return {'inactive_observed': True}


def source_files(root):
    return {path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in root.rglob('*') if path.is_file() and not path.is_symlink()}


def test_complete_private_bundle_is_portable_and_preserves_archive_only_evidence(source, tmp_path):
    before = source_files(source)
    destination = tmp_path / 'bundle'
    calls = []

    def observe(target):
        calls.append(target)
        return drained(target)

    result = bundle.capture(source, destination, observer=observe)
    assert result['status'] == 'complete' and result['activated'] is False and result['global_atomic'] is False
    assert source_files(source) == before
    expected_targets = set(bundle.UNITS) | set(bundle.MODULE_COMPONENTS)
    assert {item['name'] for item in calls} == expected_targets
    assert len(calls) == 2 * len(expected_targets)
    manifest = json.loads((destination / 'manifest.json').read_text())
    inventory = json.loads((destination / 'inventory.json').read_text())
    assert manifest['database']['snapshot_path'] == 'database/memory.sqlite3'
    assert manifest['bundle_atomic_across_legacy_stores'] is False
    assert manifest['global_os_fence'] is False
    assert manifest['execution_authority_granted'] is False
    assert manifest['captured_by'] == getpass.getuser() + '@' + socket.gethostname()
    assert len(manifest['capture_id']) == 32
    assert manifest['inventory_sha256'] == hashlib.sha256((destination / 'inventory.json').read_bytes()).hexdigest()
    assert inventory['before'] == inventory['after'] and inventory['paired_equal'] is True
    assert inventory['source_code']['files_sha256'] == inventory['source_code_after_sha256']
    assert inventory['execution_authority'] is False
    assert all(not Path(item['snapshot_path']).is_absolute() for item in manifest['copied_files'])
    assert 'data/core_memories.md' in manifest['missing_files']
    assert inventory['before']['entries']['data/improvement'] == {'kind': 'missing'}
    for relative, entry in inventory['before']['entries'].items():
        if entry['kind'] == 'file':
            copied = destination / 'raw' / relative
            assert hashlib.sha256(copied.read_bytes()).hexdigest() == entry['sha256']
    for path in destination.rglob('*'):
        assert not path.is_symlink()
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600)
    metadata = (destination / 'manifest.json').read_text() + (destination / 'inventory.json').read_text()
    assert 'PRIVATE_' not in metadata
    assert 'PRIVATE_' not in json.dumps(bundle.inspect_bundle(destination))
    standalone = destination / manifest['database']['snapshot_path']
    with closing(sqlite3_connect(standalone.as_uri() + '?mode=ro&immutable=1', uri=True)) as copied:
        assert copied.execute('SELECT COUNT(*) FROM profilefact').fetchone()[0] == 1


def test_actual_importer_consumes_portable_bundle_without_importing_old_controls(source, tmp_path):
    destination = tmp_path / 'bundle'
    bundle.capture(source, destination, observer=drained)
    core = Core(Store(tmp_path / 'canonical'), runner=object(), clock=lambda: 100,
                draft=lambda *_: AssistantResponse(display_text='Fixture', delivery_mode='text_only'))
    try:
        report = reconcile_snapshot(core, destination)
        assert report['records_added'] == 1
        assert report['table_census']['conversationjournalowner']['disposition'] == 'operational_quarantine'
        with core.store.transaction() as db:
            rows = json.dumps(db.execute('SELECT kind,id,body FROM records').fetchall())
            assert 'PRIVATE_WORK_FIXTURE' not in rows and 'PRIVATE_LESSON_FIXTURE' not in rows
            assert 'PRIVATE_OWNER_FIXTURE' not in rows and 'PRIVATE_HISTORICAL_FIXTURE' not in rows
            assert not core.store.list(db, 'task', None)
            assert not core.store.list(db, 'phone_device', None)
    finally:
        core.close()


def test_default_status_is_read_only_and_does_not_fingerprint_files(source, tmp_path, monkeypatch):
    before = source_files(source)
    monkeypatch.setattr(bundle, 'fingerprint', lambda *_args, **_kwargs: pytest.fail('status must not hash/copy file payloads'))
    result = bundle.status(source, observer=drained)
    assert result['action'] == 'read_only_status'
    assert result['status'] == 'drained_observed'
    assert result['activation_authority'] is False
    assert source_files(source) == before
    assert sorted(path.name for path in tmp_path.iterdir()) == ['legacy']


def test_active_or_unknown_writer_retains_content_free_failure_before_raw_copy(source, tmp_path):
    destination = tmp_path / 'failed'
    calls = []

    def active(target):
        calls.append(target['name'])
        return {'inactive_observed': False, 'reason': 'PRIVATE_DIAGNOSTIC_MUST_NOT_ESCAPE'}

    with pytest.raises(bundle.CaptureError, match='writers_not_drained'):
        bundle.capture(source, destination, observer=active)
    assert set(calls) == set(bundle.UNITS) | set(bundle.MODULE_COMPONENTS)
    status = json.loads((destination / 'capture-status.json').read_text())
    assert status['status'] == 'failed' and status['partial_evidence_retained'] is True
    assert 'PRIVATE_' not in json.dumps(status)
    assert not (destination / 'raw').exists() and not (destination / 'manifest.json').exists()


def test_writer_appearing_after_copy_blocks_completion_without_deleting_evidence(source, tmp_path):
    destination = tmp_path / 'partial'
    calls = 0
    count = len(bundle.UNITS) + len(bundle.MODULE_COMPONENTS)

    def observer(_target):
        nonlocal calls
        calls += 1
        return {'inactive_observed': calls <= count}

    with pytest.raises(bundle.CaptureError, match='writer_appeared'):
        bundle.capture(source, destination, observer=observer)
    assert (destination / 'raw' / bundle.DATABASE).is_file()
    assert (destination / 'database/memory.sqlite3').is_file()
    assert not (destination / 'manifest.json').exists()
    assert bundle.inspect_bundle(destination)['status'] == 'failed'


@pytest.mark.parametrize('change', ['file', 'membership', 'missing_path', 'code'])
def test_paired_inventory_rejects_changes_and_keeps_partial_copy(source, tmp_path, monkeypatch, change):
    destination = tmp_path / 'changed'
    original = bundle._standalone_database

    def mutate(*args):
        result = original(*args)
        if change == 'file':
            write(source, 'data/assistant_state.json', '{"note":"changed fixture"}')
        elif change == 'membership':
            write(source, 'data/coding/lessons/new.json', '{}')
        elif change == 'missing_path':
            write(source, 'data/core_memories.md', 'New fixture memory')
        else:
            write(source, bundle.LEGACY_CODE_REQUIRED[0], '# changed fixture code\n')
        return result

    monkeypatch.setattr(bundle, '_standalone_database', mutate)
    with pytest.raises(bundle.CaptureError, match='inventory_changed|source_code_changed'):
        bundle.capture(source, destination, observer=drained)
    assert (destination / 'raw').is_dir()
    assert not (destination / 'manifest.json').exists()
    assert bundle.inspect_bundle(destination)['status'] == 'failed'


@pytest.mark.parametrize('kind', ['file_link', 'directory_link', 'fifo', 'secret', 'local_config'])
def test_unreviewed_file_kinds_and_exact_credential_paths_fail_closed(source, tmp_path, kind):
    root = source / 'data/coding/practice'
    root.mkdir()
    outside = write(tmp_path, 'outside.txt', 'PRIVATE_OUTSIDE_PAYLOAD')
    if kind == 'file_link':
        (root / 'item').symlink_to(outside)
    elif kind == 'directory_link':
        (root / 'linked').symlink_to(tmp_path / 'outside-directory', target_is_directory=True)
    elif kind == 'fifo':
        os.mkfifo(root / 'fifo')
    elif kind == 'secret':
        write(root, '.env', 'DO_NOT_READ_OR_COPY=PRIVATE_SECRET')
    else:
        write(root, 'config/app.local.toml', 'secret="PRIVATE_SECRET"')
    destination = tmp_path / 'failed-kind'
    started = time.monotonic()
    with pytest.raises(bundle.CaptureError):
        bundle.capture(source, destination, observer=drained)
    assert time.monotonic() - started < 5
    assert not (destination / 'manifest.json').exists()
    assert 'PRIVATE_' not in (destination / 'capture-status.json').read_text()


def test_machine_configuration_is_not_in_code_pin_or_raw_bundle(source, tmp_path, monkeypatch):
    write(source, '.env', 'PRIVATE_MACHINE_SECRET')
    write(source, 'config/app.local.toml', 'secret="PRIVATE_MACHINE_SECRET"')
    write(source, 'config/voices.local.toml', 'secret="PRIVATE_MACHINE_SECRET"')
    original = bundle.fingerprint

    def guard(path, **kwargs):
        assert Path(path).name not in {'.env', 'app.local.toml', 'voices.local.toml'}
        return original(path, **kwargs)

    monkeypatch.setattr(bundle, 'fingerprint', guard)
    destination = tmp_path / 'bundle'
    bundle.capture(source, destination, observer=drained)
    inventory = json.loads((destination / 'inventory.json').read_text())
    assert not set(inventory['source_code']['files']) & {'.env', 'config/app.local.toml', 'config/voices.local.toml'}
    assert not (destination / 'raw/.env').exists()


@pytest.mark.parametrize('limit', ['files', 'bytes', 'per_file', 'entries', 'free_space', 'deadline'])
def test_resource_limits_fail_honestly_on_small_synthetic_inputs(source, tmp_path, monkeypatch, limit):
    if limit == 'files':
        monkeypatch.setattr(bundle, 'MAX_FILES', 1)
    elif limit == 'bytes':
        monkeypatch.setattr(bundle, 'MAX_TOTAL_BYTES', 1)
    elif limit == 'per_file':
        monkeypatch.setattr(bundle, 'MAX_FILE_BYTES', 1)
    elif limit == 'entries':
        monkeypatch.setattr(bundle, 'MAX_ENTRIES', 1)
    elif limit == 'free_space':
        monkeypatch.setattr(bundle.shutil, 'disk_usage', lambda *_: SimpleNamespace(free=bundle.FREE_RESERVE - 1))
    else:
        monkeypatch.setattr(bundle, 'MAX_SECONDS', -1)
    destination = tmp_path / 'limited'
    with pytest.raises(bundle.CaptureError):
        bundle.capture(source, destination, observer=drained)
    assert not (destination / 'manifest.json').exists()


def test_wal_is_recovered_only_on_scratch_and_produces_standalone_database(source, tmp_path):
    database = source / bundle.DATABASE
    with closing(sqlite3_connect(database)) as writer:
        assert writer.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute("INSERT INTO profilefact (fact_text,subject_type,confidence) VALUES ('Committed WAL fixture','primary_user',0.4)")
        writer.commit()
        assert Path(str(database) + '-wal').stat().st_size > 0
        before = source_files(source)
        destination = tmp_path / 'wal-bundle'
        bundle.capture(source, destination, observer=drained)
        assert source_files(source) == before
        manifest = json.loads((destination / 'manifest.json').read_text())
        assert manifest['tables']['profilefact']['row_count'] == 2
        standalone = destination / 'database/memory.sqlite3'
        import sqlite3
        with closing(sqlite3.connect(standalone.as_uri() + '?mode=ro&immutable=1', uri=True)) as copied:
            assert copied.execute('SELECT COUNT(*) FROM profilefact').fetchone()[0] == 2
            assert copied.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not Path(str(standalone) + '-wal').exists()
        assert (destination / 'raw/data/memory/gamma.db-wal').read_bytes() == before['data/memory/gamma.db-wal'][0]


@pytest.mark.parametrize('destination_kind', ['inside', 'existing', 'parent_link'])
def test_destination_never_overwrites_or_enters_source(source, tmp_path, destination_kind):
    if destination_kind == 'inside':
        destination = source / 'capture'
    elif destination_kind == 'existing':
        destination = tmp_path / 'existing'
        destination.mkdir()
        write(destination, 'keep.txt', 'Unrelated fixture')
    else:
        (tmp_path / 'parent-link').symlink_to(source, target_is_directory=True)
        destination = tmp_path / 'parent-link/capture'
    before = source_files(source)
    with pytest.raises(ValueError):
        bundle.capture(source, destination, observer=drained)
    assert source_files(source) == before
    if destination_kind == 'existing':
        assert (destination / 'keep.txt').read_text() == 'Unrelated fixture'


def test_failed_copy_closes_descriptors_and_retains_partial_target(source, tmp_path, monkeypatch):
    original = bundle.os.write
    count = 0
    baseline = len(list(Path('/proc/self/fd').iterdir()))

    def failed(descriptor, value):
        nonlocal count
        count += 1
        if count == 1:
            raise OSError('PRIVATE_WRITE_EXCEPTION')
        return original(descriptor, value)

    monkeypatch.setattr(bundle.os, 'write', failed)
    with pytest.raises(bundle.CaptureError):
        bundle.capture(source, tmp_path / 'failed-copy', observer=drained)
    assert len(list(Path('/proc/self/fd').iterdir())) == baseline
    assert 'PRIVATE_' not in (tmp_path / 'failed-copy/capture-status.json').read_text()


def test_destination_bytes_are_rechecked_after_write(source, tmp_path, monkeypatch):
    original = bundle.os.fsync
    changed = False

    def corrupt(descriptor):
        nonlocal changed
        original(descriptor)
        name = os.readlink('/proc/self/fd/' + str(descriptor))
        if not changed and name.endswith('/raw/data/assistant_state.json'):
            os.pwrite(descriptor, b'X', 0)
            changed = True

    monkeypatch.setattr(bundle.os, 'fsync', corrupt)
    with pytest.raises(bundle.CaptureError, match='copy_bytes_or_independence'):
        bundle.capture(source, tmp_path / 'corrupt-copy', observer=drained)
    assert changed
    assert not (tmp_path / 'corrupt-copy/manifest.json').exists()


def test_census_budget_uses_verified_size_if_file_changes_before_fingerprint(source, tmp_path, monkeypatch):
    baseline = bundle._census(source, time.monotonic() + 30)['total_bytes']
    original = bundle.fingerprint
    changed = False

    def growing(path, **kwargs):
        nonlocal changed
        if not changed and Path(path) == source / 'data/assistant_state.json':
            Path(path).write_bytes(b'x' * 1000)
            changed = True
        return original(path, **kwargs)

    monkeypatch.setattr(bundle, 'MAX_TOTAL_BYTES', baseline + 10)
    monkeypatch.setattr(bundle, 'fingerprint', growing)
    monkeypatch.setattr(bundle, '_copy_exact', lambda *_: pytest.fail('over-budget evidence must not be copied'))
    with pytest.raises(bundle.CaptureError, match='inventory_size_or_count_limit'):
        bundle.capture(source, tmp_path / 'over-budget', observer=drained)
    assert changed


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='Exact descriptor proof uses Linux /proc')
def test_repeated_successful_sqlite_captures_close_all_descriptors(source, tmp_path):
    program = r'''
import gc
import json
import os
from pathlib import Path
import sys
from gamma.coordination.legacy_bundle import capture

source, destination = map(Path, sys.argv[1:])
destination.mkdir(mode=0o700)

def descriptors():
    result = {}
    for name in os.listdir('/proc/self/fd'):
        try:
            result[int(name)] = os.readlink('/proc/self/fd/' + name)
        except FileNotFoundError:
            # The directory enumeration's own transient descriptor has closed.
            continue
    return result

def owned_descriptors(snapshot):
    result = {}
    for number, target in snapshot.items():
        path = Path(target.removesuffix(' (deleted)'))
        if path.is_absolute() and (path.is_relative_to(source) or path.is_relative_to(destination)):
            result[number] = target
    return result

gc.disable()
before = descriptors()
assert not owned_descriptors(before), before
for attempt in range(20):
    result = capture(source, destination / ('capture-' + str(attempt)),
                     observer=lambda _target: {'inactive_observed': True})
    assert result['status'] == 'complete'
    assert not gc.isenabled()
    current = descriptors()
    assert not owned_descriptors(current), current
after = descriptors()
assert after == before, {'before': before, 'after': after}
print(json.dumps({'captures': 20, 'gc_disabled': not gc.isenabled(),
                  'before_fds': len(before), 'after_fds': len(after),
                  'exact_descriptor_map_unchanged': after == before,
                  'owned_source_or_bundle_fds': len(owned_descriptors(after))}))
'''
    repository = Path(__file__).resolve().parents[1]
    completed = subprocess.run([sys.executable, '-c', program, str(source), str(tmp_path / 'child-captures')],
        cwd=repository, env={**os.environ, 'PYTHONPATH': str(repository / 'src')},
        capture_output=True, text=True, timeout=60,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    (tmp_path / 'descriptor-proof.json').write_text(json.dumps(result, sort_keys=True), encoding='utf-8')
    assert result['captures'] == 20 and result['gc_disabled'] is True
    assert result['before_fds'] == result['after_fds']
    assert result['exact_descriptor_map_unchanged'] is True
    assert result['owned_source_or_bundle_fds'] == 0


def test_missing_entries_obey_same_cap_when_paths_disappear(source, monkeypatch):
    original = Path.lstat
    missing = {source / name for name in (*bundle.ARCHIVE_ROOTS, *bundle.AUXILIARY, *bundle.OPERATIONAL_FILES)}

    def disappearing(path, *args, **kwargs):
        if path in missing:
            raise FileNotFoundError('Synthetic disappearing fixture')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(bundle, 'MAX_ENTRIES', 2)
    monkeypatch.setattr(Path, 'lstat', disappearing)
    with pytest.raises(bundle.CaptureError, match='inventory_entry_limit'):
        bundle._census(source, time.monotonic() + 30)


def test_code_config_listing_is_bounded_before_extension_filter(source, monkeypatch):
    for ordinal in range(10):
        write(source, 'config/unselected-' + str(ordinal) + '.bin', 'fixture')
    monkeypatch.setattr(bundle, 'MAX_ENTRIES', 5)
    with pytest.raises(bundle.CaptureError, match='code_inventory_limit'):
        bundle._code_inventory(source, time.monotonic() + 30)


def test_copied_sqlite_integrity_query_has_actual_deadline_handler(source, tmp_path, monkeypatch):
    destination = tmp_path / 'deadline'
    raw = destination / 'raw' / bundle.DATABASE
    raw.parent.mkdir(parents=True)
    raw.write_bytes((source / bundle.DATABASE).read_bytes())
    raw.chmod(0o600)
    original_connect = sqlite3.connect
    original_clock = time.monotonic
    end = original_clock() + 30
    expired = False
    output_handler_calls = 0

    class Connection:
        def __init__(self, path, **kwargs):
            self.inner = original_connect(path, **kwargs)
            self.output = str(path).endswith('/database/memory.sqlite3')

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def set_progress_handler(self, callback, steps):
            def tracked():
                nonlocal output_handler_calls
                if self.output:
                    output_handler_calls += 1
                return callback()
            self.inner.set_progress_handler(tracked, steps)

        def backup(self, target, **kwargs):
            return self.inner.backup(target.inner, **kwargs)

        def execute(self, sql, *args):
            nonlocal expired
            if self.output and sql == 'PRAGMA integrity_check':
                expired = True
                return self.inner.execute('WITH RECURSIVE count(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM count WHERE n<1000000) SELECT sum(n) FROM count')
            return self.inner.execute(sql, *args)

    monkeypatch.setattr(bundle.sqlite3, 'connect', lambda path, **kwargs: Connection(path, **kwargs))
    monkeypatch.setattr(bundle.time, 'monotonic', lambda: end + 1 if expired else original_clock())
    with pytest.raises(sqlite3.OperationalError, match='interrupted'):
        bundle._standalone_database(destination, end)
    assert output_handler_calls > 0


def test_late_status_failure_keeps_manifest_but_bundle_is_not_complete(source, tmp_path, monkeypatch):
    destination = tmp_path / 'late-failure'
    original = bundle._write_json

    def fail(path, value):
        if path.name == 'capture-status.json' and value['status'] == 'complete':
            raise OSError('PRIVATE_LATE_METADATA_FAILURE')
        return original(path, value)

    monkeypatch.setattr(bundle, '_write_json', fail)
    with pytest.raises(bundle.CaptureError):
        bundle.capture(source, destination, observer=drained)
    assert json.loads((destination / 'manifest.json').read_text())['status'] == 'complete'
    status = json.loads((destination / 'capture-status.json').read_text())
    assert status['status'] == 'failed'
    assert 'PRIVATE_' not in json.dumps(status)
    assert bundle.inspect_bundle(destination)['status'] == 'failed'


def test_cli_default_only_calls_status_and_has_no_assume_drained_option(source, capsys, monkeypatch):
    path = Path(__file__).resolve().parents[1] / 'scripts/capture_legacy_bundle.py'
    spec = importlib.util.spec_from_file_location('fixture_capture_cli', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []
    monkeypatch.setattr(module, 'status', lambda value: calls.append(value) or {'status': 'blocked'})
    monkeypatch.setattr(module, 'capture', lambda *_args: pytest.fail('default CLI cannot capture'))
    assert module.main(['--source', str(source)]) == 2
    assert calls == [source]
    with pytest.raises(SystemExit):
        module.main(['capture', '--destination', '/tmp/not-used', '--assume-drained'])
    capsys.readouterr()
