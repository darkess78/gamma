"""Transactional authority for the isolated coordinator and its authorized runs."""
from contextlib import contextmanager
import json
import os
import re
from pathlib import Path
import sqlite3
import uuid


class OwnershipLost(RuntimeError):
    pass


def private_path(path):
    path = Path(path)
    if '..' in path.parts:
        raise ValueError('coordination storage cannot contain parent traversal')
    path = path.absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('coordination storage cannot contain symlinks')
    return path


@contextmanager
def process_lock(path):
    """Nonblocking OS lease. A dead process cannot leave a live lease behind."""
    path = private_path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'r+b', buffering=0) as stream:
        if os.fstat(fd).st_size == 0:
            stream.write(b'0')
        if os.name == 'nt':
            import msvcrt
            stream.seek(0)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)


class Store:
    def __init__(self, root):
        self.root = private_path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = private_path(self.root / 'coordination.sqlite3')
        with self.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, body TEXT NOT NULL, PRIMARY KEY(kind,id))')
            if self.get(db, 'brain', 'state') is None:
                self.put(db, 'brain', 'state', {
                    'generation': 0, 'owner': '', 'awake': True, 'focus': 'relaxed',
                    'mood': 'neutral', 'sequence': 0, 'reconsider_at': 0,
                    'permit': None, 'working_notes': {}, 'last_input_at': 0,
                })
        if os.name != 'nt':
            self.path.chmod(0o600)

    @contextmanager
    def transaction(self, token=None):
        private_path(self.path)
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            if token is not None:
                self.require_owner(db, token)
            yield db
            if token is not None:
                self.require_owner(db, token)
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def get(db, kind, identifier):
        row = db.execute('SELECT body FROM records WHERE kind=? AND id=?', (kind, identifier)).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def put(db, kind, identifier, body):
        raw = json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        if len(raw.encode()) > 524288:
            raise ValueError('coordination record too large')
        db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?)', (kind, identifier, raw))

    @staticmethod
    def list(db, kind, limit=100):
        if limit is None:
            return [json.loads(r[0]) for r in db.execute(
                'SELECT body FROM records WHERE kind=? ORDER BY rowid DESC', (kind,))]
        return [json.loads(r[0]) for r in db.execute(
            'SELECT body FROM records WHERE kind=? ORDER BY rowid DESC LIMIT ?', (kind, limit))]

    def require_owner(self, db, token):
        brain = self.get(db, 'brain', 'state')
        if (brain['generation'], brain['owner']) != token:
            raise OwnershipLost('coordinator generation no longer owns effects')

    def claim(self, expected_generation, *, expected_identity_id=None):
        """CAS handover. Call only while holding the process lease at startup."""
        if expected_identity_id is not None and (not isinstance(expected_identity_id, str)
            or not re.fullmatch('[a-f0-9]{32}', expected_identity_id)):
            raise ValueError('invalid_expected_identity_id')
        with self.transaction() as db:
            if expected_identity_id is not None:
                meta = self.get(db, 'personal_meta', 'state')
                if not isinstance(meta, dict) or meta.get('identity_id') != expected_identity_id:
                    raise ValueError('release_identity_changed_before_claim')
            brain = self.get(db, 'brain', 'state')
            if brain['generation'] != expected_generation:
                raise OwnershipLost('handover generation changed')
            brain.update(generation=expected_generation + 1, owner=uuid.uuid4().hex)
            for turn in self.list(db, 'turn', None):
                if turn['status'] == 'pending':
                    turn['status'] = 'recoverable' if self.get(db,'personal_meta','state') else 'interrupted'
                    self.put(db, 'turn', turn['id'], turn)
            self.put(db, 'brain', 'state', brain)
            return brain['generation'], brain['owner']

    def read(self, kind, identifier):
        with self.transaction() as db:
            return self.get(db, kind, identifier)

    def release(self, token):
        """Invalidate outstanding decision threads before releasing the OS lease."""
        with self.transaction() as db:
            self.require_owner(db, token)
            brain = self.get(db, 'brain', 'state')
            brain['owner'] = ''
            self.put(db, 'brain', 'state', brain)
            for turn in self.list(db, 'turn', 10000):
                if turn['status'] == 'pending':
                    turn['status'] = 'recoverable' if self.get(db,'personal_meta','state') else 'interrupted'
                    self.put(db, 'turn', turn['id'], turn)

    def update_run(self, identifier, checkpoint_ref, *, status=None, reason=None):
        """Worker evidence is retained across owner changes, without deciding for Shana."""
        with self.transaction() as db:
            run = self.get(db, 'run', identifier)
            if run is None:
                raise ValueError('unknown authorized practice run')
            run['checkpoint_ref'] = checkpoint_ref
            run['revision'] += 1
            if status:
                run['status'] = status
            if reason:
                run['reason'] = reason
            self.put(db, 'run', identifier, run)
            return run
