#!/usr/bin/env python3
"""Inspect by default; explicitly rehearse a fresh capture in private copied state."""
import sys
if __name__ == '__main__':
    sys.dont_write_bytecode = True

import argparse
from contextlib import ExitStack, closing
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import sqlite3
import time
from unittest.mock import patch
import urllib.request
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from gamma.coordination import legacy_bundle as capture_contract
from gamma.coordination.core import Core
from gamma.coordination.legacy_bundle_inventory import fingerprint, stat_fields
from gamma.coordination.legacy_rows import convert_legacy, _valid_key
from gamma.coordination.personal import digest as personal_digest
from gamma.coordination.personal_import import reconcile_snapshot
from gamma.coordination.store import Store, OwnershipLost, private_path
from gamma.coordination.transfer import export_snapshot, restore_snapshot, identity_lease
from gamma.schemas.response import AssistantResponse


NAMESPACE = 'gamma-main:personal-memory'
MAX_ROWS = 200_000
MAX_IMPORT_BYTES = 256 * 1024 * 1024
MAX_SECONDS = 900
CONVERTED = {'knownperson', 'personidentity', 'profilefact', 'episodicmemory', 'conversationjournalentry'}
CHECKPOINTS = {'rollingsessionsummary', 'workingstatecheckpoint', 'scopedworkingstatecheckpoint',
               'scopedsessionsummary', 'stream_self_goals'}
OPERATIONAL = {'stream_temp_memory', 'durableoutputstate', 'stream_viewer_trust',
               'conversationjournalowner', 'conversationinputcursor', 'conversationcontinuityowner', 'conversationcursor'}
STAT_KEYS = {'size', 'mtime_ns', 'ctime_ns', 'mode', 'device', 'inode'}
IMPORTED_KINDS = {'personal_source', 'personal_record', 'person', 'account', 'personal_quarantine',
                  'personal_migration', 'admin_edit', 'maintenance_proposal', 'personal_identity'}


class ReviewError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def require(condition, code):
    if not condition:
        raise ReviewError('legacy_review_' + code)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def deadline(end):
    require(time.monotonic() <= end, 'deadline_exceeded')


def relative(value):
    require(isinstance(value, str) and 0 < len(value) <= 4096, 'invalid_relative_path')
    path = PurePosixPath(value)
    require(len(path.parts) <= 128 and not path.is_absolute() and '..' not in path.parts and '\\' not in value
            and path.as_posix() == value and value != '.' and not any(ord(c) < 32 for c in value),
            'invalid_relative_path')
    return value


def private_directory(path):
    require(Path(path).is_absolute(), 'bundle_path_must_be_absolute')
    path = private_path(path)
    info = path.lstat()
    require(path.is_dir() and (os.name != 'posix' or info.st_uid == os.getuid() and info.st_mode & 0o077 == 0),
            'bundle_directory_not_private')
    return path


def proof(path, limit):
    value = fingerprint(path, limit=limit)
    require(value['mode'] == 0o600 and path.lstat().st_nlink == 1, 'file_not_private_independent_copy')
    return value


def read_json(path, limit=capture_contract.METADATA_RESERVE):
    path = private_path(path)
    evidence = proof(path, limit)
    raw = private_path(path).read_bytes()
    require(len(raw) <= limit and hashlib.sha256(raw).hexdigest() == evidence['sha256'], 'metadata_changed_during_read')
    value = json.loads(raw)
    require(isinstance(value, dict), 'metadata_object_required')
    return value, evidence


def inspect_bundle(bundle):
    """Read metadata only. No drain probe, SQLite opening, output or Core instance."""
    root = private_directory(bundle)
    status, status_proof = read_json(root / 'capture-status.json', 65_536)
    manifest, manifest_proof = read_json(root / 'manifest.json')
    state = status.get('status') if status.get('status') in {'complete', 'capturing', 'failed'} else 'unknown'
    return {'action': 'read_only_inspect', 'verification_executed': False, 'capture_status': state,
            'manifest_status': 'complete' if manifest.get('status') == 'complete' else 'incomplete',
            'capture_status_sha256': status_proof['sha256'], 'manifest_sha256': manifest_proof['sha256'],
            'activation_authority': False}


def disposition(name):
    if name in CONVERTED:
        return 'converted'
    if name in CHECKPOINTS:
        return 'checkpoint_provenance'
    if name in OPERATIONAL:
        return 'operational_quarantine'
    return 'sqlite_metadata' if name == 'sqlite_sequence' else 'unsupported_quarantine'


def _allowed_member(name):
    return name in {*capture_contract.AUXILIARY, *capture_contract.OPERATIONAL_FILES} or any(
        name == root or name.startswith(root + '/') for root in capture_contract.ARCHIVE_ROOTS)


def _walk(root, end):
    members = {}
    pending = [(root, '')]
    while pending:
        directory, prefix = pending.pop()
        private_directory(directory)
        with os.scandir(directory) as listing:
            for item in listing:
                deadline(end)
                name = prefix + item.name
                relative(name)
                require(len(members) < capture_contract.MAX_ENTRIES + 30, 'bundle_entry_limit')
                path = private_path(root / name)
                info = path.lstat()
                if item.is_symlink():
                    raise ReviewError('legacy_review_symlink_or_special_member')
                if path.is_dir():
                    private_directory(path)
                    members[name] = 'directory'
                    pending.append((path, name + '/'))
                elif path.is_file():
                    require(info.st_mode & 0o777 == 0o600 and info.st_nlink == 1, 'file_not_private_independent_copy')
                    members[name] = 'file'
                else:
                    raise ReviewError('legacy_review_symlink_or_special_member')
    return members


def validate(bundle, end):
    root = private_directory(bundle)
    status, status_proof = read_json(root / 'capture-status.json', 65_536)
    manifest, manifest_proof = read_json(root / 'manifest.json')
    inventory, inventory_proof = read_json(root / 'inventory.json')
    require(status.get('status') == manifest.get('status') == inventory.get('status') == 'complete', 'capture_not_complete')
    identity = manifest.get('capture_id')
    require(isinstance(identity, str) and re.fullmatch('[a-f0-9]{32}', identity)
            and status.get('capture_id') == inventory.get('capture_id') == identity, 'capture_id_mismatch')
    require(manifest.get('format') == inventory.get('format') == 1, 'format_not_supported')
    require(manifest.get('inventory_path') == 'inventory.json'
            and manifest.get('inventory_sha256') == inventory_proof['sha256'], 'inventory_hash_mismatch')
    require(manifest.get('source_namespace') == NAMESPACE, 'namespace_mismatch')
    require(manifest.get('bundle_atomic_across_legacy_stores') is False
            and manifest.get('global_os_fence') is False and manifest.get('production_activated') is False
            and manifest.get('execution_authority_granted') is False
            and manifest.get('enumerated_writers_quiescent_observed') is True, 'capture_scope_claim_invalid')
    expected_targets = set(capture_contract.UNITS) | set(capture_contract.MODULE_COMPONENTS)
    for key in ('drain_observations_before', 'drain_observations_after'):
        values = manifest.get(key)
        require(isinstance(values, dict) and set(values) == expected_targets
                and all(isinstance(value, dict) and value.get('inactive_observed') is True for value in values.values()),
                'recorded_drain_incomplete')
    require(inventory.get('paired_equal') is True and inventory.get('before') == inventory.get('after')
            and inventory.get('execution_authority') is False, 'paired_inventory_mismatch')
    require(inventory.get('archive_only_roots') == list(capture_contract.ARCHIVE_ROOTS)
            and inventory.get('optional_archive_only_files') == list(capture_contract.OPERATIONAL_FILES), 'archive_scope_mismatch')
    before = inventory.get('before')
    require(isinstance(before, dict) and isinstance(before.get('entries'), dict), 'invalid_inventory')
    entries = before['entries']
    require(len(entries) <= capture_contract.MAX_ENTRIES, 'inventory_entry_limit')
    require(set((*capture_contract.ARCHIVE_ROOTS, *capture_contract.AUXILIARY, *capture_contract.OPERATIONAL_FILES)) <= set(entries),
            'fixed_inventory_paths_missing')
    require(all(entries[name].get('kind') in {'file', 'missing'} for name in (
        *capture_contract.AUXILIARY, *capture_contract.OPERATIONAL_FILES))
        and all(entries[name].get('kind') in {'directory', 'missing'} for name in capture_contract.ARCHIVE_ROOTS),
        'fixed_inventory_path_type_mismatch')
    raw_expected = {'raw': 'directory'}
    raw_proofs = {}
    total = files = 0
    for name, value in entries.items():
        deadline(end)
        relative(name)
        capture_contract._safe_name(Path(name))
        require(_allowed_member(name) and isinstance(value, dict), 'undeclared_inventory_member')
        kind = value.get('kind')
        require(kind in {'file', 'directory', 'missing'}, 'invalid_inventory_kind')
        fields = {'kind'} if kind == 'missing' else {'kind', *STAT_KEYS, *({'sha256'} if kind == 'file' else set())}
        require(set(value) == fields, 'invalid_inventory_fields')
        if kind == 'missing':
            continue
        require(all(type(value[key]) is int and value[key] >= 0 for key in STAT_KEYS), 'invalid_inventory_stat')
        raw_name = 'raw/' + name
        raw_expected[raw_name] = kind
        for parent in PurePosixPath(raw_name).parents:
            deadline(end)
            if parent.as_posix() != '.':
                raw_expected.setdefault(parent.as_posix(), 'directory')
                require(len(raw_expected) <= capture_contract.MAX_ENTRIES + 30, 'implied_directory_limit')
        if kind == 'file':
            files += 1
            total += value['size']
            require(files <= capture_contract.MAX_FILES and total <= capture_contract.MAX_TOTAL_BYTES,
                    'source_file_count_or_byte_limit')
            require(value['size'] <= capture_contract.MAX_FILE_BYTES
                    and isinstance(value['sha256'], str) and re.fullmatch('[a-f0-9]{64}', value['sha256']), 'invalid_inventory_file')
            actual = proof(root / raw_name, capture_contract.MAX_FILE_BYTES)
            require(actual['sha256'] == value['sha256'] and actual['size'] == value['size'], 'raw_file_hash_or_size_mismatch')
            require((actual['device'], actual['inode']) != (value['device'], value['inode']), 'raw_file_hardlinked_to_source')
            raw_proofs[name] = actual
    require(files <= capture_contract.MAX_FILES and total <= capture_contract.MAX_TOTAL_BYTES
            and before.get('file_count') == status.get('file_count') == files
            and before.get('total_bytes') == status.get('total_bytes') == total, 'inventory_totals_mismatch')
    require(entries.get(capture_contract.DATABASE, {}).get('kind') == 'file', 'primary_database_missing')
    code = inventory.get('source_code')
    require(isinstance(code, dict) and isinstance(code.get('files'), dict)
            and len(code['files']) <= capture_contract.MAX_FILES
            and set(capture_contract.LEGACY_CODE_REQUIRED) <= set(code['files']), 'code_pin_incomplete')
    for name, sha in code['files'].items():
        relative(name)
        capture_contract._safe_name(Path(name))
        require(isinstance(sha, str) and re.fullmatch('[a-f0-9]{64}', sha), 'invalid_code_pin')
        require(name.startswith('src/gamma/') or name == 'pyproject.toml'
                or name.startswith('config/') and len(PurePosixPath(name).parts) == 2
                and Path(name).suffix in {'.toml', '.yaml', '.json'} and not name.endswith('.local.toml'), 'code_pin_scope_mismatch')
    require(digest(code['files']) == code.get('files_sha256') == inventory.get('source_code_after_sha256')
            == manifest.get('source_code_files_sha256'), 'code_pin_hash_mismatch')
    members = _walk(root, end)
    require({key: value for key, value in members.items() if key == 'raw' or key.startswith('raw/')} == raw_expected,
            'raw_membership_mismatch')
    fixed = {'capture-status.json': 'file', 'manifest.json': 'file', 'inventory.json': 'file',
             'database': 'directory', 'database/memory.sqlite3': 'file', 'scratch': 'directory'}
    scratch = {key: value for key, value in members.items() if key.startswith('scratch/')}
    require('scratch/memory.sqlite3' in scratch and all(value == 'file' and key in {
        'scratch/memory.sqlite3', 'scratch/memory.sqlite3-wal', 'scratch/memory.sqlite3-shm', 'scratch/memory.sqlite3-journal'}
        for key, value in scratch.items()), 'scratch_membership_mismatch')
    require(members == {**raw_expected, **fixed, **scratch}, 'undeclared_bundle_member')
    scratch_proofs = {name: proof(root / name, capture_contract.MAX_FILE_BYTES) for name in scratch}
    db = manifest.get('database')
    require(isinstance(db, dict) and db.get('snapshot_path') == 'database/memory.sqlite3'
            and db.get('integrity_check') == 'ok', 'database_reference_invalid')
    db_proof = proof(root / db['snapshot_path'], capture_contract.MAX_FILE_BYTES)
    require(db_proof['sha256'] == db.get('snapshot_sha256') and db_proof['size'] == db.get('bytes'), 'database_hash_mismatch')
    copied = manifest.get('copied_files')
    require(isinstance(copied, list) and len(copied) <= len(capture_contract.AUXILIARY), 'invalid_auxiliary_manifest')
    expected_aux = {name for name in capture_contract.AUXILIARY if entries[name]['kind'] == 'file'}
    found = set()
    import_bytes = db_proof['size']
    for item in copied:
        require(isinstance(item, dict) and item.get('relative_path') in expected_aux, 'auxiliary_scope_mismatch')
        name = item['relative_path']
        require(name not in found and item.get('snapshot_path') == 'raw/' + name
                and item.get('sha256') == entries[name]['sha256'], 'auxiliary_proof_mismatch')
        found.add(name)
        import_bytes += entries[name]['size']
    require(found == expected_aux and set(manifest.get('missing_files', [])) == {
        name for name in capture_contract.AUXILIARY if entries[name]['kind'] == 'missing'}, 'auxiliary_coverage_mismatch')
    require(import_bytes <= MAX_IMPORT_BYTES, 'canonical_import_byte_limit')
    tables, expected_census = {}, {}
    with closing(sqlite3.connect((root / db['snapshot_path']).as_uri() + '?mode=ro&immutable=1', uri=True)) as connection:
        connection.execute('PRAGMA query_only=ON')
        connection.set_progress_handler(lambda: int(time.monotonic() > end), 1000)
        require(connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'database_integrity_failed')
        names = []
        for name, in connection.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            deadline(end)
            names.append(name)
            require(len(names) <= capture_contract.MAX_FILES, 'database_table_limit')
        require(len(names) <= capture_contract.MAX_FILES and isinstance(manifest.get('tables'), dict)
                and set(names) == set(manifest['tables']), 'database_table_set_mismatch')
        rows = 0
        payload_bytes = 0
        connection.row_factory = sqlite3.Row
        for name in names:
            deadline(end)
            quoted = '"' + name.replace('"', '""') + '"'
            count = connection.execute('SELECT COUNT(*) FROM ' + quoted).fetchone()[0]
            rows += count
            require(rows <= MAX_ROWS and manifest['tables'][name] == {'row_count': count}, 'database_table_count_mismatch_or_limit')
            payload = []
            for row in connection.execute('SELECT * FROM ' + quoted):
                deadline(end)
                item = dict(row)
                payload_bytes += len(json.dumps(item, sort_keys=True, allow_nan=False).encode())
                require(payload_bytes <= MAX_IMPORT_BYTES, 'canonical_payload_byte_limit')
                payload.append(item)
            tables[name] = payload
            expected_census[name] = {'row_count': count, 'disposition': disposition(name), 'payload_sha256': personal_digest(payload)}
    return {'root': root, 'manifest': manifest, 'manifest_proof': manifest_proof, 'status_proof': status_proof,
            'inventory_proof': inventory_proof, 'entries': entries, 'raw_proofs': raw_proofs, 'database_proof': db_proof,
            'tables': tables, 'expected_census': expected_census, 'file_count': files, 'total_bytes': total,
            'import_bytes': import_bytes, 'code_pin_count': len(code['files']), 'members': members,
            'scratch_proofs': scratch_proofs}


def _recheck_input(checked, end):
    root = checked['root']
    for name, expected in [('manifest.json', checked['manifest_proof']), ('inventory.json', checked['inventory_proof']),
                           ('capture-status.json', checked['status_proof'])]:
        deadline(end)
        require(proof(root / name, capture_contract.METADATA_RESERVE) == expected,
                'bundle_metadata_changed_during_rehearsal')
    for name, expected in checked['raw_proofs'].items():
        deadline(end)
        require(proof(root / 'raw' / name, capture_contract.MAX_FILE_BYTES) == expected,
                'raw_evidence_changed_during_rehearsal')
    require(proof(root / 'database/memory.sqlite3', capture_contract.MAX_FILE_BYTES) == checked['database_proof'],
            'standalone_database_changed_during_rehearsal')
    for name, expected in checked['scratch_proofs'].items():
        deadline(end)
        require(proof(root / name, capture_contract.MAX_FILE_BYTES) == expected,
                'scratch_evidence_changed_during_rehearsal')
    require(_walk(root, end) == checked['members'], 'bundle_membership_changed_during_rehearsal')


def _records(store):
    with store.transaction() as db:
        return [(kind, key, json.loads(body)) for kind, key, body in db.execute('SELECT kind,id,body FROM records ORDER BY kind,id')]


def _imported_sources(store):
    return [body for kind, _key, body in _records(store) if kind == 'personal_source'
            and body.get('legacy_ref', {}).get('namespace') == NAMESPACE]


def _source_signature(store):
    return digest(sorted(_imported_sources(store), key=lambda value: value['id']))


def _expected_refs(tables, manifest):
    converted = convert_legacy(NAMESPACE, tables)
    refs = {digest({'namespace': NAMESPACE, 'table': name, 'id': row['id']})
            for name in CONVERTED for row in tables.get(name, []) if _valid_key(row.get('id'))}
    for name in CHECKPOINTS:
        for row in tables.get(name, []):
            pk = row.get('id')
            composite = False
            if pk is None and name != 'stream_self_goals':
                session, scope = row.get('session_id'), row.get('privacy_scope')
                if isinstance(session, str) and _valid_key(session):
                    if name.startswith('scoped') and isinstance(scope, str) and _valid_key(scope):
                        pk, composite = [session, scope], True
                    elif not name.startswith('scoped'):
                        pk = session
            if _valid_key(pk) or composite:
                refs.add(digest({'namespace': NAMESPACE, 'table': name, 'id': pk}))
    refs.update(digest({'namespace': NAMESPACE, 'file': item['relative_path']}) for item in manifest['copied_files'])
    return refs, {value['id'] for value in converted['sources']}


class CapabilityTripwire:
    def __init__(self):
        self.calls = 0

    def forbid(self, *_args, **_kwargs):
        self.calls += 1
        raise ReviewError('legacy_review_provider_or_effect_attempted')

    def __getattr__(self, _name):
        return self.forbid()


def _projection(checked, output, end, author):
    projection = output / 'verified-import-projection'
    projection.mkdir(mode=0o700)
    root, manifest = checked['root'], checked['manifest']
    snapshot = manifest['database']['snapshot_path']
    capture_contract._copy_exact(root / snapshot, projection / snapshot, checked['database_proof'], end)
    for item in manifest['copied_files']:
        source = item['snapshot_path']
        capture_contract._copy_exact(root / source, projection / source, checked['raw_proofs'][item['relative_path']], end)
    capture_contract._write_json(projection / 'manifest.json', {
        'role': 'verified_import_projection', 'author': author, 'source_namespace': NAMESPACE,
        'database': manifest['database'], 'copied_files': manifest['copied_files'],
        'original_bundle': str(root), 'original_manifest_sha256': checked['manifest_proof']['sha256'],
        'original_inventory_sha256': checked['inventory_proof']['sha256'],
        'execution_authority_granted': False, 'complete_capture_claim': False,
    })
    return projection


def _mechanical_rehearsal(checked, output, projection, tripwire, end):
    state = output / 'canonical-review-state'
    lock_root = output / 'identity-locks'
    draft_calls = 0

    def fixed_reply(*_args):
        nonlocal draft_calls
        draft_calls += 1
        return AssistantResponse(display_text='Authored synthetic mechanical review reply. No action occurred.', delivery_mode='text_only')

    core = Core(Store(state), runner=tripwire, work_runner=tripwire, draft=fixed_reply,
                identity_lock_root=lock_root)
    old_token = core.token
    try:
        baseline_kinds = {kind for kind, _key, _body in _records(core.store)}
        first = reconcile_snapshot(core, projection, namespace=NAMESPACE)
        require(first['table_census'] == checked['expected_census'], 'import_table_census_mismatch')
        sources = _imported_sources(core.store)
        expected_refs, expected_source_ids = _expected_refs(checked['tables'], checked['manifest'])
        actual_refs = {digest(source['legacy_ref']) for source in sources}
        require(actual_refs == expected_refs and expected_source_ids <= {source['id'] for source in sources}, 'legacy_ref_coverage_mismatch')
        rows = _records(core.store)
        require({kind for kind, _key, _body in rows} - baseline_kinds <= IMPORTED_KINDS, 'operational_records_imported')
        brain = core.store.read('brain', 'state')
        require(brain.get('permit') is None and (brain['generation'], brain['owner']) == core.token
                and core.future is None and core.dialogue_future is None, 'import_granted_execution_authority')
        for kind, _key, body in rows:
            if kind == 'account':
                require(body.get('grants_authority') is False, 'imported_account_authority')
            if kind == 'personal_record' and body.get('legacy_ref'):
                require(body.get('execution_authority') is not True, 'imported_record_authority')
                require(body.get('scope') == body.get('source_scope') == 'owner_private', 'imported_record_not_private')
            if kind == 'person' and _key not in {'self', 'owner'}:
                require(body.get('is_owner') is not True and not body.get('permissions'), 'imported_person_authority')
        with core.store.transaction() as db:
            require(first['quarantined'] == len(core.store.list(db, 'personal_quarantine', None)), 'quarantine_reference_count_mismatch')
        require(all(source.get('scope') == 'owner_private' for source in sources), 'imported_source_not_private')
        shared_checks = 0
        with core.store.transaction() as db:
            for source in sources:
                if source.get('text'):
                    shared_checks += 1
                    require(core.personal.recall(db, source['text'][:120], audience='shared') == [], 'private_recall_escaped')
        signature = _source_signature(core.store)
        stable_kinds = IMPORTED_KINDS | {'personal_meta'}
        before_repeat = digest([(kind, key, body) for kind, key, body in _records(core.store) if kind in stable_kinds])
        repeated = reconcile_snapshot(core, projection, namespace=NAMESPACE)
        require(repeated['migration_id'] == first['migration_id'] and repeated['table_census'] == first['table_census']
                and all(repeated[name] == 0 for name in ('sources_added', 'records_added', 'people_added', 'accounts_added'))
                and before_repeat == digest([(kind, key, body) for kind, key, body in _records(core.store) if kind in stable_kinds]),
                'repeat_import_not_idempotent')
        deadline(end)
        core.input('Authored synthetic mechanical review fixture alpha. This sentence records an isolated verification exchange.',
                   'synthetic-mechanical-review', 'synthetic-mechanical-review-exchange')
        require(core.store.read('turn', 'synthetic-mechanical-review-exchange').get('status') == 'completed',
                'synthetic_exchange_not_completed')
        commitment = core.personal_control('synthetic-mechanical-review-commitment', 'memory', {
            'op': 'remember', 'kind': 'commitment', 'subject_id': 'owner', 'topic': 'authored synthetic verifier fixture',
            'text': 'Authored synthetic mechanical review fixture: this commitment remains unfinished only in the disposable verification state.'})['id']
        require(core.store.read('personal_record', commitment)['status'] == 'active', 'synthetic_commitment_not_unfinished')
        identity = core.personal_state()['identity_id']
        require(_source_signature(core.store) == signature, 'actual_imported_sources_changed_by_fixture')
    finally:
        core.close()
    closed_rows = _records(Store(state))
    closed_nonbrain = digest([(kind, key, body) for kind, key, body in closed_rows if kind != 'brain'])
    with identity_lease(state, lock_root):
        snapshot = export_snapshot(state, output / 'latest-canonical-snapshot')
        restore_snapshot(output / 'latest-canonical-snapshot', output / 'current-reader-restored')
    restored = Core(Store(output / 'current-reader-restored'), runner=tripwire, work_runner=tripwire,
                    draft=fixed_reply, identity_lock_root=lock_root, expected_identity_id=identity)
    try:
        require(restored.personal_state()['identity_id'] == identity, 'identity_not_preserved')
        require(_source_signature(restored.store) == signature, 'imported_sources_not_preserved')
        restored_rows = _records(restored.store)
        require(digest([(kind, key, body) for kind, key, body in restored_rows if kind != 'brain']) == closed_nonbrain,
                'latest_canonical_state_not_preserved')
        require(restored.store.read('turn', 'synthetic-mechanical-review-exchange').get('status') == 'completed'
                and restored.store.read('personal_record', commitment).get('status') == 'active', 'later_fixture_not_preserved')
        stale_rejected = False
        try:
            with restored.store.transaction(old_token) as db:
                restored.store.put(db, 'stale_verifier_probe', 'must-not-write', {'unexpected': True})
        except OwnershipLost:
            stale_rejected = True
        require(stale_rejected, 'stale_owner_not_rejected')
        require(tripwire.calls == 0 and draft_calls == 1, 'unexpected_capability_call')
        return {'imported_sources': len(sources), 'distinct_legacy_refs': len(actual_refs),
            'imported_sources_sha256': signature, 'legacy_refs_sha256': digest(sorted(actual_refs)),
            'identity_sha256': hashlib.sha256(identity.encode()).hexdigest(), 'shared_recall_sources_checked': shared_checks,
            'repeat_import_idempotent': True, 'no_operational_authority_imported': True,
            'synthetic_exchange_preserved': True, 'synthetic_unfinished_commitment_preserved': True,
            'current_reader_reopened': True, 'latest_nonbrain_records_sha256': closed_nonbrain,
            'canonical_snapshot_records': snapshot['record_count'], 'stale_owner_rejected': True,
            'fixed_synthetic_draft_calls': draft_calls, 'provider_or_effect_calls': tripwire.calls}
    finally:
        restored.close()


def verify_bundle(bundle, output):
    root = private_directory(bundle)
    initial_manifest, initial_proof = read_json(root / 'manifest.json')
    source_value = initial_manifest.get('source_root')
    require(isinstance(source_value, str) and Path(source_value).is_absolute(), 'source_reference_invalid')
    source = private_path(source_value)
    supplied = Path(output)
    require(supplied.is_absolute(), 'output_must_be_absolute')
    destination = private_path(supplied)
    require(not destination.exists() and destination.parent.is_dir(), 'output_must_be_fresh')
    require(not destination.is_relative_to(root) and not root.is_relative_to(destination)
            and not destination.is_relative_to(source) and not source.is_relative_to(destination), 'output_inside_bundle_or_source')
    destination.mkdir(mode=0o700)
    author, review_id = getpass.getuser() + '@' + socket.gethostname(), uuid.uuid4().hex
    started, end = datetime.now(timezone.utc).isoformat(), time.monotonic() + MAX_SECONDS
    tripwire = CapabilityTripwire()
    try:
        with ExitStack() as guards:
            for target in ('socket.create_connection', 'socket.getaddrinfo', 'socket.socket.connect',
                           'socket.socket.connect_ex', 'socket.socket.send', 'socket.socket.sendall',
                           'socket.socket.sendto', 'urllib.request.urlopen', 'subprocess.Popen', 'os.system'):
                guards.enter_context(patch(target, tripwire.forbid))
            checked = validate(root, end)
            require(checked['manifest_proof']['sha256'] == initial_proof['sha256'], 'manifest_changed_before_verification')
            require(shutil.disk_usage(destination).free > checked['import_bytes'] * 4 + capture_contract.FREE_RESERVE,
                    'private_output_capacity_insufficient')
            projection = _projection(checked, destination, end, author)
            result = _mechanical_rehearsal(checked, destination, projection, tripwire, end)
            deadline(end)
            _recheck_input(checked, end)
        report = {'status': 'complete', 'author': author, 'verification_id': review_id, 'started_utc': started,
            'finished_utc': datetime.now(timezone.utc).isoformat(), 'scope': 'isolated_mechanical_legacy_import_rehearsal',
            'original_bundle': str(root), 'capture_id': checked['manifest']['capture_id'],
            'original_manifest_sha256': checked['manifest_proof']['sha256'],
            'original_inventory_sha256': checked['inventory_proof']['sha256'],
            'original_capture_status_sha256': checked['status_proof']['sha256'],
            'archive_only_retained_by_reference': True, 'raw_files_verified': checked['file_count'],
            'raw_bytes_verified': checked['total_bytes'], 'tables_verified': len(checked['tables']),
            'legacy_code_files_pinned': checked['code_pin_count'], 'table_census_sha256': digest(checked['expected_census']),
            'verified_import_projection': True, 'full_capture_claim_for_projection': False,
            'production_activated': False, 'semantic_adoption_accepted': False, 'human_acceptance': False,
            **result}
        capture_contract._write_json(destination / 'report.json', report)
        return report
    except Exception as exc:
        code = exc.code if isinstance(exc, ReviewError) else 'legacy_review_' + type(exc).__name__
        capture_contract._write_json(destination / 'report.json', {'status': 'failed', 'author': author,
            'verification_id': review_id, 'failure_code': code, 'private_partial_state_retained': True,
            'provider_or_effect_calls': tripwire.calls, 'production_activated': False})
        raise ReviewError(code) from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', required=True, type=Path)
    parser.add_argument('--verify', action='store_true', help='Explicit isolated copied-state mechanical rehearsal.')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    if args.verify != bool(args.output):
        parser.error('--verify requires --output; --output is unavailable for read-only inspection')
    try:
        result = verify_bundle(args.bundle, args.output) if args.verify else inspect_bundle(args.bundle)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        code = exc.code if isinstance(exc, ReviewError) else 'legacy_review_' + type(exc).__name__
        print(json.dumps({'status': 'failed', 'failure_code': code}, sort_keys=True))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
