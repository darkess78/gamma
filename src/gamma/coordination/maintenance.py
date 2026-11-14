"""Finite local recovery snapshots; never overwrite or activate current state."""
import json
from contextlib import closing
from pathlib import Path
import re
import shutil
import sqlite3
import uuid

from .personal import digest
from .resource_policy import storage_admitted
from .store import private_path
from .transfer import export_snapshot, restore_snapshot


MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
RESERVE_BYTES = 256 * 1024 * 1024


class Maintenance:
    def __init__(self, core):
        self.core, self.store = core, core.store
        self.root = private_path(self.store.root / 'recovery')
        self._operation_lock = core._recovery_lock
        with self.store.transaction(core.token) as db:
            for kind in ('maintenance_backup', 'maintenance_restore'):
                for row in self.store.list(db, kind, None):
                    if row.get('status') == 'preparing' and row.get('generation') != core.token[0]:
                        row['status'] = 'failed_review_required'
                        key = row.get('request_id') if kind == 'maintenance_backup' else row['id']
                        self.store.put(db, kind, key, row)

    def status(self):
        disk = shutil.disk_usage(self.store.root)
        with self.store.transaction(self.core.token) as db:
            pending = db.execute("SELECT COUNT(*) FROM records WHERE kind='turn' AND json_extract(body,'$.status') IN ('pending','queued','recoverable')").fetchone()[0]
            backups = self.store.list(db, 'maintenance_backup', 8)
            return {'scope': 'owner_private', 'free_bytes': disk.free, 'reserve_bytes': RESERVE_BYTES,
                    'storage_warning': disk.free < 1024 * 1024 * 1024,
                    'small_write_admitted': storage_admitted(disk.free, 1024 * 1024, RESERVE_BYTES),
                    'pending_turns': pending,
                    'backups': [{key: row[key] for key in ('id','status','created_at','manifest_sha256','record_count') if key in row} for row in backups],
                    'backup_policy': 'Local protected copies, maximum eight snapshots; no automatic deletion or upload.',
                    'restore_policy': 'Restore to a fresh directory only; current state and post-snapshot experiences are never overwritten.'}

    def check(self):
        with self.store.transaction(self.core.token) as db:
            result = db.execute('PRAGMA quick_check').fetchall()
        return {'ok': result == [('ok',)], 'check': 'sqlite_quick_check', 'status': self.status()}

    def backup(self, request_id):
        if not self._operation_lock.acquire(blocking=False):
            raise ValueError('recovery_operation_in_progress')
        try:
            return self._backup(request_id)
        finally:
            self._operation_lock.release()

    def _backup(self, request_id):
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', request_id):
            raise ValueError('invalid_backup_request_id')
        with self.store.transaction(self.core.token) as db:
            previous = self.store.get(db, 'maintenance_backup', request_id)
            if previous:
                return self._public(previous)
            if len(self.store.list(db, 'maintenance_backup', None)) >= 8:
                raise ValueError('backup_capacity_reached')
            serialized = db.serialize()
            artifacts = list((self.store.root / 'artifacts').glob('*.json'))
            for path in artifacts:
                private_path(path)
            total = len(serialized) + sum(path.stat().st_size for path in artifacts)
            if total > MAX_SNAPSHOT_BYTES:
                raise ValueError('snapshot_size_limit')
            if not storage_admitted(shutil.disk_usage(self.store.root).free, total * 3 + 1024 * 1024, RESERVE_BYTES):
                raise ValueError('insufficient_space_for_recovery_snapshot')
            identifier = 'backup-' + uuid.uuid4().hex
            row = {'id': identifier, 'request_id': request_id, 'status': 'preparing',
                   'created_at': self.core.clock(), 'generation': self.core.token[0],
                   'sequence': self.core._brain(db)['sequence']}
            self.store.put(db, 'maintenance_backup', request_id, row)
        workspace = None
        workspace_created = False
        try:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            workspace = private_path(self.root / (identifier + '-staging'))
            workspace.mkdir(mode=0o700)
            workspace_created = True
            destination = private_path(self.root / identifier)
            snapshot_db = workspace / 'coordination.sqlite3'
            snapshot_db.write_bytes(serialized)
            snapshot_db.chmod(0o600)
            # Only the COPY is closed. The live owner's token and subsequent
            # experiences stay in the live journal throughout this operation.
            with closing(sqlite3.connect(snapshot_db)) as db, db:
                brain = json.loads(db.execute("SELECT body FROM records WHERE kind='brain' AND id='state'").fetchone()[0])
                brain['owner'] = ''
                db.execute("UPDATE records SET body=? WHERE kind='brain' AND id='state'", (json.dumps(brain),))
            if artifacts:
                (workspace / 'artifacts').mkdir(mode=0o700)
            for source in artifacts:
                target = workspace / 'artifacts' / source.name
                shutil.copyfile(source, target)
                target.chmod(0o600)
            manifest = export_snapshot(workspace, destination)
            with self.store.transaction(self.core.token) as db:
                row.update(status='verified_snapshot', manifest_sha256=digest(manifest),
                           record_count=manifest['record_count'])
                self.store.put(db, 'maintenance_backup', request_id, row)
            return self._public(row)
        except BaseException:
            with self.store.transaction(self.core.token) as db:
                row.update(status='failed_review_required')
                self.store.put(db, 'maintenance_backup', request_id, row)
            raise
        finally:
            # This path was freshly created above. Resolve it again before the
            # only recursive cleanup; never remove a user-supplied target.
            if workspace_created:
                resolved = workspace.resolve()
                if resolved.parent == self.root.resolve() and resolved.name == identifier + '-staging':
                    shutil.rmtree(resolved)

    @staticmethod
    def _public(row):
        return {key: row[key] for key in ('id','status','created_at','manifest_sha256','record_count') if key in row}

    def rehearse(self, backup_id):
        if not self._operation_lock.acquire(blocking=False):
            raise ValueError('recovery_operation_in_progress')
        try:
            return self._rehearse(backup_id)
        finally:
            self._operation_lock.release()

    def _rehearse(self, backup_id):
        if not isinstance(backup_id, str) or not re.fullmatch(r'backup-[a-f0-9]{32}', backup_id):
            raise ValueError('invalid_backup_id')
        with self.store.transaction(self.core.token) as db:
            if len(self.store.list(db, 'maintenance_restore', None)) >= 8:
                raise ValueError('restore_rehearsal_capacity_reached')
            row = next((value for value in self.store.list(db, 'maintenance_backup', None) if value['id'] == backup_id), None)
            if not row or row['status'] != 'verified_snapshot':
                raise ValueError('backup_not_verified')
        source = private_path(self.root / backup_id)
        total = sum(private_path(path).stat().st_size for path in source.rglob('*') if path.is_file())
        if not storage_admitted(shutil.disk_usage(self.root).free, total + 1024 * 1024, RESERVE_BYTES):
            raise ValueError('insufficient_space_for_restore_rehearsal')
        target = private_path(self.root / ('restore-' + uuid.uuid4().hex))
        reservation = {'id': target.name, 'status': 'preparing', 'backup_id': backup_id,
                       'created_at': self.core.clock(), 'generation': self.core.token[0]}
        with self.store.transaction(self.core.token) as db:
            if len(self.store.list(db, 'maintenance_restore', None)) >= 8:
                raise ValueError('restore_rehearsal_capacity_reached')
            self.store.put(db, 'maintenance_restore', target.name, reservation)
        try:
            manifest = restore_snapshot(source, target)
            # Read the actual restored database, not only a successful copy result.
            with closing(sqlite3.connect((target / 'coordination.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
                db.execute('PRAGMA query_only=ON')
                ok = db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
                count = db.execute('SELECT COUNT(*) FROM records').fetchone()[0]
                brain = json.loads(db.execute("SELECT body FROM records WHERE kind='brain' AND id='state'").fetchone()[0])
            if not ok or count != manifest['record_count'] or brain['owner']:
                raise ValueError('restored_state_failed_check')
            result = {'ok': True, 'backup_id': backup_id, 'records_restored': count,
                      'identity_id': manifest['identity_id'], 'activated': False,
                      'current_state_overwritten': False}
            with self.store.transaction(self.core.token) as db:
                self.store.put(db, 'maintenance_restore', target.name, {**reservation, **result, 'status': 'verified_restore'})
            return result
        except BaseException:
            with self.store.transaction(self.core.token) as db:
                reservation['status'] = 'failed_review_required'
                self.store.put(db, 'maintenance_restore', target.name, reservation)
            raise
