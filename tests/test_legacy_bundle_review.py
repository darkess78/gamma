"""Dynamic import/reversal review uses synthetic captured files, never live Shana."""
from contextlib import closing
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys

import pytest

from gamma.coordination import legacy_bundle as capture


@pytest.fixture
def reviewer():
    script = Path(__file__).resolve().parents[1] / 'scripts/verify_legacy_bundle.py'
    spec = importlib.util.spec_from_file_location('isolated_legacy_review', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding='utf-8')
    return path


@pytest.fixture
def captured(tmp_path):
    source = tmp_path / 'synthetic-legacy'
    source.mkdir(mode=0o700)
    for name in capture.LEGACY_CODE_REQUIRED:
        write(source, name, '# Synthetic legacy source\n')
    write(source, 'pyproject.toml', '[project]\nname="fixture"\n')
    write(source, 'config/app.toml', 'database_url="sqlite:///./data/memory/gamma.db"\n')
    write(source, 'config/persona.yaml', 'speech_style: "Fictional style"\n')
    write(source, 'data/assistant_state.json', '{"note":"PRIVATE_AUX_REVIEW_FIXTURE"}')
    write(source, 'src/gamma/persona/style.json', '{}')
    write(source, 'data/coding/autonomous/work-fiction/state.json', '{"status":"running","goal":"PRIVATE_OLD_WORK_REVIEW_FIXTURE"}')
    write(source, 'data/coding/lessons/lesson-fiction.json', '{"active":true,"text":"PRIVATE_OLD_LESSON_REVIEW_FIXTURE"}')
    database = source / capture.DATABASE
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as db:
        db.execute('CREATE TABLE knownperson (id INTEGER PRIMARY KEY, name TEXT, relationship_to_user TEXT, notes TEXT, trust TEXT)')
        db.execute('INSERT INTO knownperson VALUES (?,?,?,?,?)',
                   (1, 'PRIVATE_PERSON_REVIEW_FIXTURE', 'friend', 'Fictional review contact', 'owner'))
        db.execute('CREATE TABLE personidentity (id INTEGER PRIMARY KEY, person_id INTEGER, platform TEXT, platform_user_id TEXT, display_name TEXT)')
        for identity_id, platform in ((11, 'discord'), (12, 'twitch')):
            db.execute('INSERT INTO personidentity VALUES (?,?,?,?,?)',
                       (identity_id, 1, platform, 'fictional-' + platform + '-account', 'PRIVATE_ACCOUNT_REVIEW_FIXTURE'))
        db.execute('CREATE TABLE profilefact (id INTEGER PRIMARY KEY AUTOINCREMENT, fact_text TEXT, subject_type TEXT, confidence REAL)')
        db.execute("INSERT INTO profilefact (fact_text,subject_type,confidence) VALUES ('PRIVATE_FACT_REVIEW_FIXTURE','primary_user',0.4)")
        db.execute('CREATE TABLE conversationjournalentry (id INTEGER, text TEXT, role TEXT, completion_status TEXT)')
        for index in range(3):
            db.execute('INSERT INTO conversationjournalentry VALUES (?,?,?,?)', (index + 1, 'PRIVATE_JOURNAL_REVIEW_FIXTURE_' + str(index), 'user', 'completed'))
        db.execute('CREATE TABLE workingstatecheckpoint (session_id TEXT, commitments_json TEXT)')
        db.execute("INSERT INTO workingstatecheckpoint VALUES ('fictional-session','[]')")
        for table in ('conversationjournalowner', 'conversationinputcursor', 'durableoutputstate', 'stream_viewer_trust'):
            db.execute('CREATE TABLE ' + table + ' (id INTEGER, payload TEXT)')
            db.execute('INSERT INTO ' + table + ' VALUES (?,?)', (1, 'PRIVATE_OLD_OPERATIONAL_REVIEW_FIXTURE'))
        db.execute('CREATE TABLE "future-""table" (id INTEGER, payload TEXT)')
        db.execute('INSERT INTO "future-""table" VALUES (1,?)', ('PRIVATE_UNKNOWN_REVIEW_FIXTURE',))
        db.commit()
    bundle = tmp_path / 'captured-fixture'
    capture.capture(source, bundle, observer=lambda _: {'inactive_observed': True})
    return source, bundle


def files(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file() and not p.is_symlink()}


def change_json(path, transform):
    value = json.loads(path.read_text())
    transform(value)
    path.write_text(json.dumps(value), encoding='utf-8')


def change_inventory(bundle, transform):
    path = bundle / 'inventory.json'
    change_json(path, transform)
    change_json(bundle / 'manifest.json', lambda value: value.update(inventory_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))


def test_dynamic_connected_import_and_current_reader_reversal(reviewer, captured, tmp_path, monkeypatch):
    source, bundle = captured
    before_source, before_bundle = files(source), files(bundle)
    output = tmp_path / 'mechanical-review'
    original_core, original_store = reviewer.Core, reviewer.Store
    locks = []
    def confined_store(path):
        assert Path(path).is_relative_to(output)
        return original_store(path)
    def confined_core(*args, **kwargs):
        assert Path(kwargs['identity_lock_root']).is_relative_to(output)
        locks.append(kwargs['identity_lock_root'])
        return original_core(*args, **kwargs)
    monkeypatch.setattr(reviewer, 'Store', confined_store)
    monkeypatch.setattr(reviewer, 'Core', confined_core)
    report = reviewer.verify_bundle(bundle, output)
    assert report['status'] == 'complete'
    assert report['tables_verified'] == len(json.loads((bundle / 'manifest.json').read_text())['tables'])
    assert report['imported_sources'] > 0 and report['distinct_legacy_refs'] > 0
    assert report['provider_or_effect_calls'] == 0 and report['fixed_synthetic_draft_calls'] == 1
    assert report['repeat_import_idempotent'] and report['current_reader_reopened'] and report['stale_owner_rejected']
    assert report['synthetic_exchange_preserved'] and report['synthetic_unfinished_commitment_preserved']
    assert report['archive_only_retained_by_reference'] and report['no_operational_authority_imported']
    assert report['production_activated'] is False and report['semantic_adoption_accepted'] is False
    assert len(locks) == 2
    assert 'PRIVATE_' not in json.dumps(report)
    assert files(source) == before_source and files(bundle) == before_bundle
    projection = output / 'verified-import-projection'
    manifest = json.loads((projection / 'manifest.json').read_text())
    assert manifest['role'] == 'verified_import_projection' and manifest['complete_capture_claim'] is False
    assert not (projection / 'capture-status.json').exists()
    assert not (projection / 'raw/data/coding').exists()
    restored = output / 'current-reader-restored/coordination.sqlite3'
    with closing(sqlite3.connect(restored.as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
        restored_rows = db.execute('SELECT kind,id,body FROM records').fetchall()
        body = json.dumps(restored_rows)
        assert 'PRIVATE_OLD_WORK_REVIEW_FIXTURE' not in body
        assert 'PRIVATE_OLD_LESSON_REVIEW_FIXTURE' not in body
        assert 'PRIVATE_OLD_OPERATIONAL_REVIEW_FIXTURE' not in body
        assert 'PRIVATE_UNKNOWN_REVIEW_FIXTURE' not in body
    people = [json.loads(body) for kind, _key, body in restored_rows
              if kind == 'person' and json.loads(body).get('legacy_table') == 'knownperson']
    accounts = [json.loads(body) for kind, _key, body in restored_rows if kind == 'account']
    assert len(people) == 1 and len(accounts) == 2
    person = people[0]
    assert person['name'] == 'PRIVATE_PERSON_REVIEW_FIXTURE' and person['legacy_id'] == 1
    assert person['legacy_trust_label'] == 'owner'
    assert person['id'] not in {'owner', 'self'} and person.get('is_owner') is not True and not person.get('permissions')
    assert person['scope'] == 'owner_private'
    assert {(account['legacy_id'], account['platform']) for account in accounts} == {(11, 'discord'), (12, 'twitch')}
    sources = {key: json.loads(body) for kind, key, body in restored_rows if kind == 'personal_source'}
    for account in accounts:
        assert account['person_id'] == person['id'] and account['scope'] == 'owner_private'
        assert account['confirmed'] is True and account['grants_authority'] is False
        source = sources[account['source_id']]
        assert source['legacy_ref'] == {'namespace': reviewer.NAMESPACE, 'table': 'personidentity', 'id': account['legacy_id']}
        assert source['dependencies']['sources'] == [person['source_id']]
    # The verifier's exact current-reader reversal must retain all person/account
    # records, not merely table counts or the imported source hash.
    before_restore = output / 'canonical-review-state/coordination.sqlite3'
    with closing(sqlite3.connect(before_restore.as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
        imported = db.execute("SELECT kind,id,body FROM records WHERE kind IN ('person','account') ORDER BY kind,id").fetchall()
    assert imported == sorted(row for row in restored_rows if row[0] in {'person', 'account'})


def test_inspection_cannot_open_sqlite_core_or_observe_services(reviewer, captured, tmp_path, monkeypatch):
    source, bundle = captured
    before = files(bundle)
    def forbidden(*_args, **_kwargs):
        pytest.fail('Read-only metadata inspection cannot run a Core, SQLite or drain probe')
    monkeypatch.setattr(reviewer, 'Core', forbidden)
    monkeypatch.setattr(reviewer.sqlite3, 'connect', forbidden)
    monkeypatch.setattr(capture, 'drain_observations', forbidden)
    result = reviewer.inspect_bundle(bundle)
    assert result['verification_executed'] is False and result['capture_status'] == 'complete'
    assert files(bundle) == before
    assert sorted(path.name for path in tmp_path.iterdir()) == ['captured-fixture', 'synthetic-legacy']


@pytest.mark.parametrize('verify', [False, True])
def test_bundle_path_must_be_explicitly_absolute(reviewer, captured, tmp_path, monkeypatch, verify):
    _source, bundle = captured
    monkeypatch.chdir(tmp_path)
    with pytest.raises(reviewer.ReviewError, match='bundle_path_must_be_absolute'):
        if verify:
            reviewer.verify_bundle(Path(bundle.name), tmp_path / 'unused-output')
        else:
            reviewer.inspect_bundle(Path(bundle.name))
    assert not (tmp_path / 'unused-output').exists()


@pytest.mark.parametrize('corruption', [
    'status', 'manifest', 'inventory_hash', 'paired_equal', 'raw_bytes', 'raw_mode', 'raw_symlink',
    'raw_hardlink', 'raw_extra_file', 'raw_extra_directory', 'raw_fifo', 'top_extra', 'database_hash', 'table_count',
    'traversal', 'projection_escape', 'capture_id',
])
def test_corrupt_or_incomplete_capture_never_reaches_core(reviewer, captured, tmp_path, monkeypatch, corruption):
    source, bundle = captured
    raw = bundle / 'raw/data/assistant_state.json'
    if corruption == 'status':
        change_json(bundle / 'capture-status.json', lambda value: value.update(status='failed'))
    elif corruption == 'manifest':
        change_json(bundle / 'manifest.json', lambda value: value.update(status='capturing'))
    elif corruption == 'inventory_hash':
        change_json(bundle / 'manifest.json', lambda value: value.update(inventory_sha256='0' * 64))
    elif corruption == 'paired_equal':
        change_inventory(bundle, lambda value: value.update(paired_equal=False))
    elif corruption == 'raw_bytes':
        raw.write_bytes(b'x' * raw.stat().st_size)
    elif corruption == 'raw_mode':
        raw.chmod(0o644)
    elif corruption == 'raw_symlink':
        raw.unlink()
        raw.symlink_to(source / 'data/assistant_state.json')
    elif corruption == 'raw_hardlink':
        raw.unlink()
        os.link(source / 'data/assistant_state.json', raw)
    elif corruption == 'raw_extra_file':
        write(bundle, 'raw/data/coding/unlisted.json', '{}').chmod(0o600)
    elif corruption == 'raw_extra_directory':
        (bundle / 'raw/unlisted').mkdir(mode=0o700)
    elif corruption == 'raw_fifo':
        os.mkfifo(bundle / 'raw/unlisted-fifo')
    elif corruption == 'top_extra':
        write(bundle, 'unlisted.bin', 'PRIVATE_UNDECLARED_PAYLOAD').chmod(0o600)
    elif corruption == 'database_hash':
        change_json(bundle / 'manifest.json', lambda value: value['database'].update(snapshot_sha256='0' * 64))
    elif corruption == 'table_count':
        change_json(bundle / 'manifest.json', lambda value: value['tables']['profilefact'].update(row_count=999))
    elif corruption == 'traversal':
        def mutate(value):
            value['before']['entries']['../outside'] = {'kind': 'missing'}
            value['after']['entries']['../outside'] = {'kind': 'missing'}
        change_inventory(bundle, mutate)
    elif corruption == 'projection_escape':
        change_json(bundle / 'manifest.json', lambda value: value['copied_files'][0].update(snapshot_path='../../outside'))
    else:
        change_json(bundle / 'capture-status.json', lambda value: value.update(capture_id='a' * 32))
    monkeypatch.setattr(reviewer, 'Core', lambda *_args, **_kwargs: pytest.fail('invalid bundle reached Core'))
    output = tmp_path / 'failed-review'
    with pytest.raises(reviewer.ReviewError):
        reviewer.verify_bundle(bundle, output)
    report = json.loads((output / 'report.json').read_text())
    assert report['status'] == 'failed' and report['production_activated'] is False
    assert report['provider_or_effect_calls'] == 0
    assert 'PRIVATE_' not in json.dumps(report)
    assert not (output / 'canonical-review-state').exists()


@pytest.mark.parametrize('kind', ['bundle', 'source', 'existing', 'symlink'])
def test_output_must_be_new_private_and_external(reviewer, captured, tmp_path, kind):
    source, bundle = captured
    if kind == 'bundle':
        output = bundle / 'review'
    elif kind == 'source':
        output = source / 'review'
    elif kind == 'existing':
        output = tmp_path / 'existing'
        output.mkdir()
        write(output, 'keep', 'Unrelated fixture')
    else:
        (tmp_path / 'link').symlink_to(bundle, target_is_directory=True)
        output = tmp_path / 'link/review'
    original = files(bundle)
    with pytest.raises(ValueError):
        reviewer.verify_bundle(bundle, output)
    assert files(bundle) == original
    if kind == 'existing':
        assert (output / 'keep').read_text() == 'Unrelated fixture'


def test_provider_tripwire_blocks_attempt_without_network(reviewer, captured, tmp_path, monkeypatch):
    _source, bundle = captured
    original_core = reviewer.Core
    def tampered(*args, **kwargs):
        kwargs['draft'] = lambda *_: socket.create_connection(('127.0.0.1', 9))
        return original_core(*args, **kwargs)
    monkeypatch.setattr(reviewer, 'Core', tampered)
    output = tmp_path / 'tripwire-review'
    with pytest.raises(reviewer.ReviewError, match='provider_or_effect_attempted'):
        reviewer.verify_bundle(bundle, output)
    report = json.loads((output / 'report.json').read_text())
    assert report['provider_or_effect_calls'] == 1 and report['status'] == 'failed'


def test_work_capability_tripwire_records_and_blocks_attempt(reviewer, captured, tmp_path, monkeypatch):
    _source, bundle = captured
    def tampered(*_args, **kwargs):
        kwargs['runner'].prepare('fictional-unauthorized-effect')
        pytest.fail('The work capability tripwire returned')
    monkeypatch.setattr(reviewer, 'Core', tampered)
    output = tmp_path / 'effect-review'
    with pytest.raises(reviewer.ReviewError, match='provider_or_effect_attempted'):
        reviewer.verify_bundle(bundle, output)
    assert json.loads((output / 'report.json').read_text())['provider_or_effect_calls'] == 1


def test_file_budget_is_checked_before_hashing_next_raw_payload(reviewer, captured, tmp_path, monkeypatch):
    _source, bundle = captured
    original = reviewer.proof
    raw_files = []
    def tracked(path, limit):
        if '/raw/' in str(path):
            raw_files.append(path)
        return original(path, limit)
    monkeypatch.setattr(reviewer, 'proof', tracked)
    monkeypatch.setattr(capture, 'MAX_FILES', 1)
    with pytest.raises(reviewer.ReviewError, match='source_file_count_or_byte_limit'):
        reviewer.verify_bundle(bundle, tmp_path / 'file-limit-review')
    assert len(raw_files) == 1


def test_total_row_budget_fails_before_core_admission(reviewer, captured, tmp_path, monkeypatch):
    _source, bundle = captured
    monkeypatch.setattr(reviewer, 'MAX_ROWS', 1)
    monkeypatch.setattr(reviewer, 'Core', lambda *_args, **_kwargs: pytest.fail('over-budget DB reached Core'))
    with pytest.raises(reviewer.ReviewError, match='database_table_count_mismatch_or_limit'):
        reviewer.verify_bundle(bundle, tmp_path / 'row-limit-review')


def test_raw_evidence_changed_during_rehearsal_prevents_success(reviewer, captured, tmp_path, monkeypatch):
    _source, bundle = captured
    original = reviewer._mechanical_rehearsal
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        (bundle / 'raw/data/assistant_state.json').write_text('{"changed":"fixture"}', encoding='utf-8')
        return result
    monkeypatch.setattr(reviewer, '_mechanical_rehearsal', changed)
    output = tmp_path / 'late-evidence-change'
    with pytest.raises(reviewer.ReviewError, match='raw_evidence_changed_during_rehearsal'):
        reviewer.verify_bundle(bundle, output)
    assert json.loads((output / 'report.json').read_text())['status'] == 'failed'


def test_immutable_sqlite_query_deadline_interrupts_actual_query(reviewer, captured, tmp_path, monkeypatch):
    _source, bundle = captured
    original_connect = sqlite3.connect
    original_clock = reviewer.time.monotonic
    expired = False
    calls = 0
    class Connection:
        def __init__(self, *args, **kwargs):
            self.inner = original_connect(*args, **kwargs)
        def __getattr__(self, name):
            return getattr(self.inner, name)
        def set_progress_handler(self, callback, steps):
            def tracked():
                nonlocal calls
                calls += 1
                return callback()
            self.inner.set_progress_handler(tracked, steps)
        def execute(self, sql, *args):
            nonlocal expired
            if sql == 'PRAGMA integrity_check':
                expired = True
                return self.inner.execute('WITH RECURSIVE count(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM count WHERE n<1000000) SELECT sum(n) FROM count')
            return self.inner.execute(sql, *args)
    monkeypatch.setattr(reviewer.sqlite3, 'connect', lambda *args, **kwargs: Connection(*args, **kwargs))
    monkeypatch.setattr(reviewer.time, 'monotonic', lambda: original_clock() + 100000 if expired else original_clock())
    monkeypatch.setattr(reviewer, 'Core', lambda *_args, **_kwargs: pytest.fail('expired query reached Core'))
    before = files(bundle)
    with pytest.raises(reviewer.ReviewError, match='OperationalError'):
        reviewer.verify_bundle(bundle, tmp_path / 'query-deadline-review')
    assert calls > 0
    assert files(bundle) == before


def test_actual_cli_inspects_by_default_and_verifies_only_explicitly(reviewer, captured, tmp_path):
    _source, bundle = captured
    script = Path(reviewer.__file__)
    common = [sys.executable, str(script), '--bundle', str(bundle)]
    options = {'capture_output': True, 'text': True, 'timeout': 60,
               'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0}
    inspected = subprocess.run(common, **options)
    assert inspected.returncode == 0, inspected.stderr
    assert json.loads(inspected.stdout)['verification_executed'] is False
    verified = subprocess.run([*common, '--verify', '--output', str(tmp_path / 'cli-review')], **options)
    assert verified.returncode == 0, verified.stdout + verified.stderr
    report = json.loads(verified.stdout)
    assert report['status'] == 'complete' and report['provider_or_effect_calls'] == 0
    assert 'PRIVATE_' not in verified.stdout
