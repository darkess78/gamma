"""SQLite admission transactions. Timeouts apply only to waiting work."""
from contextlib import closing, contextmanager
import json
import math
import os
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from .process_identity import process_identity
from .admission import AdmissionBusy


class DuplicateRequest(AdmissionBusy):
    pass


class Ledger:
    def __init__(self, path, limits):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for name, pair in limits.items():
            if not isinstance(name, str) or not name or len(name) > 128 or len(pair) != 2:
                raise ValueError("invalid resource policy")
            capacity, reserved = pair
            if type(capacity) is not int or type(reserved) is not int or not 0 <= reserved <= capacity or capacity <= 0:
                raise ValueError("invalid resource capacity")
        with self._connection() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS policy(resource TEXT PRIMARY KEY, capacity INTEGER NOT NULL, reserved INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs(token TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL,
                  state TEXT NOT NULL, priority TEXT NOT NULL, owner_pid INTEGER NOT NULL,
                  owner_identity TEXT NOT NULL, execution_kind TEXT NOT NULL, created REAL NOT NULL,
                  deadline REAL NOT NULL, demands TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS allocations(token TEXT NOT NULL, resource TEXT NOT NULL, units INTEGER NOT NULL,
                  PRIMARY KEY(token,resource));
                CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')
            conn.execute("BEGIN IMMEDIATE")
            if "workload" not in {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}:
                conn.execute("ALTER TABLE jobs ADD COLUMN workload TEXT NOT NULL DEFAULT 'model'")
            for name, pair in limits.items():
                conn.execute("INSERT OR IGNORE INTO policy VALUES (?,?,?)", (name, *pair))
                row = conn.execute("SELECT capacity,reserved FROM policy WHERE resource=?", (name,)).fetchone()
                if tuple(row) != tuple(pair):
                    raise ValueError("shared admission policy conflict: " + name)
            conn.commit()

    @contextmanager
    def _connection(self, timeout=5):
        with closing(sqlite3.connect(self.path, timeout=timeout, isolation_level=None)) as conn:
            conn.row_factory = sqlite3.Row
            try:
                yield conn
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                    raise AdmissionBusy("admission authority is busy") from exc
                raise
            finally:
                if conn.in_transaction:
                    conn.rollback()

    def bind_policy(self, fingerprint):
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT OR IGNORE INTO metadata VALUES ('policy',?)", (fingerprint,))
            if conn.execute("SELECT value FROM metadata WHERE key='policy'").fetchone()[0] != fingerprint:
                raise ValueError("shared admission configuration conflict")
            conn.commit()

    @staticmethod
    def _prune_waiters(conn):
        # Expiration cannot affect active or uncertain jobs.
        conn.execute("UPDATE jobs SET state='expired' WHERE state='waiting' AND deadline < ?", (time.time(),))
        for row in conn.execute("SELECT token,owner_pid,owner_identity FROM jobs WHERE state='waiting'").fetchall():
            identity = process_identity(row['owner_pid'])
            if identity != 'unknown' and identity != row['owner_identity']:
                conn.execute("UPDATE jobs SET state='abandoned' WHERE token=? AND state='waiting'", (row['token'],))

    def acquire(self, demands, priority='interactive', timeout=0.0, request_id=None, execution_kind='remote', workload='model'):
        if priority not in {'interactive', 'background'} or execution_kind not in {'remote', 'local'}:
            raise ValueError('invalid admission class')
        if type(timeout) not in {int,float} or not math.isfinite(timeout) or timeout < 0 or timeout > 300:
            raise ValueError('invalid admission timeout')
        if not isinstance(demands, dict) or not 1 <= len(demands) <= 32 or any(type(v) is not int or v <= 0 for v in demands.values()):
            raise ValueError('invalid resource demands')
        if not isinstance(workload, str) or not workload or len(workload)>64:
            raise ValueError('invalid workload')
        request_id = uuid4().hex if request_id is None else request_id
        if not isinstance(request_id,str) or not request_id or len(request_id) > 128:
            raise ValueError('invalid request identity')
        token, now = uuid4().hex, time.time()
        expires = time.monotonic() + timeout
        with self._connection(timeout=min(.05, timeout)) as conn:
            conn.execute('BEGIN IMMEDIATE')
            if timeout > 0 and time.monotonic() >= expires:
                raise AdmissionBusy('admission deadline exceeded')
            self._prune_waiters(conn)
            for resource, units in demands.items():
                row = conn.execute('SELECT * FROM policy WHERE resource=?',(resource,)).fetchone()
                if row is None or units > row['capacity']:
                    raise ValueError('unconfigured or excessive demand')
                if priority == 'background' and units > row['capacity'] - row['reserved']:
                    raise AdmissionBusy('resource reserved for interactive work: ' + resource)
            if conn.execute("SELECT 1 FROM jobs WHERE request_id=?",(request_id,)).fetchone():
                raise DuplicateRequest('request identity already admitted or attempted')
            if conn.execute("SELECT count(*) FROM jobs WHERE state='waiting'").fetchone()[0] >= 128:
                raise AdmissionBusy('admission queue full')
            if priority == 'background' and conn.execute(
                    "SELECT count(*) FROM jobs WHERE state='waiting' AND priority='background'").fetchone()[0] >= 96:
                raise AdmissionBusy('queue positions reserved for interactive work')
            conn.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                         (token, request_id, 'waiting', priority, os.getpid(), process_identity(os.getpid()),
                          execution_kind, now, now + timeout, json.dumps(demands,sort_keys=True), workload))
            conn.commit()
        try:
            first = True
            while True:
                with self._connection(timeout=min(.05, max(0, expires - time.monotonic()))) as conn:
                    conn.execute('BEGIN IMMEDIATE')
                    # First attempt is allowed even for a zero waiting deadline.
                    if (not first or timeout > 0) and time.monotonic() >= expires:
                        raise AdmissionBusy('admission deadline exceeded')
                    first = False
                    own = conn.execute('SELECT state FROM jobs WHERE token=?',(token,)).fetchone()
                    if own['state'] != 'waiting':
                        raise AdmissionBusy('waiting request no longer eligible')
                    possible = True
                    for resource, units in demands.items():
                        total, reserved = conn.execute('SELECT capacity,reserved FROM policy WHERE resource=?',(resource,)).fetchone()
                        used, background = conn.execute('''SELECT COALESCE(SUM(a.units),0),
                            COALESCE(SUM(CASE WHEN j.priority='background' THEN a.units ELSE 0 END),0)
                            FROM allocations a JOIN jobs j USING(token) WHERE a.resource=?''',(resource,)).fetchone()
                        if used + units > total or priority == 'background' and background + units > total - reserved:
                            possible = False
                    # Earlier same-priority requests sharing resources are FIFO.
                    # Interactive waiters always precede conflicting background work.
                    for other in conn.execute("SELECT * FROM jobs WHERE state='waiting' AND token != ? AND deadline >= ?",(token,time.time())).fetchall():
                        outranks = other['priority'] == 'interactive' and priority == 'background'
                        earlier = other['priority'] == priority and (other['created'],other['token']) < (now,token)
                        if (outranks or earlier) and set(demands).intersection(json.loads(other['demands'])):
                            possible = False
                    if possible:
                        conn.executemany('INSERT INTO allocations VALUES (?,?,?)',[(token,r,n) for r,n in demands.items()])
                        conn.execute("UPDATE jobs SET state='active' WHERE token=?",(token,))
                        conn.commit()
                        return token
                    conn.commit()
                remaining = expires - time.monotonic()
                if remaining <= 0:
                    raise AdmissionBusy('admission deadline exceeded')
                time.sleep(min(.025, remaining))
        except BaseException:
            try:
                with self._connection(timeout=0) as conn:
                    conn.execute("UPDATE jobs SET state='expired' WHERE token=? AND state='waiting'",(token,))
            except AdmissionBusy:
                # Its persisted deadline will prune this nonexecuting waiter.
                pass
            raise

    def release(self, token):
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT state FROM jobs WHERE token=?',(token,)).fetchone()
            if row is None or row['state'] not in {'active','uncertain'}:
                raise ValueError('no active allocation')
            conn.execute('DELETE FROM allocations WHERE token=?',(token,))
            conn.execute("UPDATE jobs SET state='completed' WHERE token=?",(token,))
            conn.commit()

    def uncertain(self, token):
        with self._connection() as conn:
            conn.execute("UPDATE jobs SET state='uncertain' WHERE token=? AND state='active'",(token,))

    def reconcile_local_dead(self, token):
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM jobs WHERE token=?',(token,)).fetchone()
            if row is None or row['state'] not in {'active','uncertain'}:
                return False
            identity = process_identity(row['owner_pid'])
            if identity == 'unknown' or row['owner_identity'] == 'unknown' or identity == row['owner_identity']:
                return False
            if row['execution_kind'] != 'local':
                conn.execute("UPDATE jobs SET state='uncertain' WHERE token=?",(token,))
                conn.commit()
                return False
            conn.execute('DELETE FROM allocations WHERE token=?',(token,))
            conn.execute("UPDATE jobs SET state='completed' WHERE token=?",(token,))
            conn.commit()
            return True

    def snapshot(self):
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._prune_waiters(conn)
            for row in conn.execute("SELECT * FROM jobs WHERE state='active'").fetchall():
                identity = process_identity(row['owner_pid'])
                if identity != 'unknown' and row['owner_identity'] != 'unknown' and identity != row['owner_identity']:
                    conn.execute("UPDATE jobs SET state='uncertain' WHERE token=?",(row['token'],))
            result = {
                'policy': [dict(r) for r in conn.execute('SELECT * FROM policy ORDER BY resource')],
                'allocations': [dict(r) for r in conn.execute('SELECT j.*,a.resource,a.units FROM allocations a JOIN jobs j USING(token) ORDER BY j.created')],
                'waiting': [dict(r) for r in conn.execute("SELECT * FROM jobs WHERE state='waiting' ORDER BY created")],
                'unresolved': [dict(r) for r in conn.execute("SELECT * FROM jobs WHERE state='uncertain' ORDER BY created")],
            }
            conn.commit()
            return result
