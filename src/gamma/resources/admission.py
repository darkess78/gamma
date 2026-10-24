import contextlib
from pathlib import Path
import sqlite3
import uuid


class AdmissionBusy(RuntimeError):
    pass


class Admission:
    def __init__(self, path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(sqlite3.connect(path, timeout=5)) as conn:
            with conn:
                conn.execute("CREATE TABLE IF NOT EXISTS resource_lease (resource TEXT PRIMARY KEY, token TEXT NOT NULL, state TEXT NOT NULL)")

    def acquire(self, resource):
        return self.acquire_many([resource])

    def release(self, resource, token):
        self.release_many([resource], token)

    def uncertain(self, resource, token):
        self.uncertain_many([resource], token)

    def _validate_many(self, resources):
        if not resources or not isinstance(resources, (list, tuple)):
            raise ValueError("resources must be a nonempty sequence")
        if len(resources) > 16:
            raise ValueError("max 16 resources")
        seen = set()
        for r in resources:
            if not isinstance(r, str) or not r or len(r) > 128:
                raise ValueError(f"invalid resource name: {r!r}")
            if r in seen:
                raise ValueError(f"duplicate resource: {r!r}")
            seen.add(r)
        return sorted(resources)

    def acquire_many(self, resources):
        resources = self._validate_many(resources)
        token = uuid.uuid4().hex
        with contextlib.closing(sqlite3.connect(self._path, timeout=5)) as conn:
            try:
                with conn:
                    for r in resources:
                        conn.execute("INSERT INTO resource_lease (resource, token, state) VALUES (?, ?, 'active')", (r, token))
            except sqlite3.IntegrityError:
                raise AdmissionBusy(resources) from None
        return token

    def release_many(self, resources, token):
        resources = self._validate_many(resources)
        with contextlib.closing(sqlite3.connect(self._path, timeout=5)) as conn:
            with conn:
                for r in resources:
                    cur = conn.execute("DELETE FROM resource_lease WHERE resource = ? AND token = ?", (r, token))
                    if cur.rowcount == 0:
                        raise ValueError(f"no lease for {r!r}")

    def uncertain_many(self, resources, token):
        resources = self._validate_many(resources)
        with contextlib.closing(sqlite3.connect(self._path, timeout=5)) as conn:
            with conn:
                for r in resources:
                    cur = conn.execute("UPDATE resource_lease SET state = 'uncertain' WHERE resource = ? AND token = ?", (r, token))
                    if cur.rowcount == 0:
                        raise ValueError(f"no lease for {r!r}")

    def status(self, resource):
        with contextlib.closing(sqlite3.connect(self._path, timeout=5)) as conn:
            row = conn.execute("SELECT resource, token, state FROM resource_lease WHERE resource = ?", (resource,)).fetchone()
        if row is None:
            return None
        return {"resource": row[0], "token": row[1], "state": row[2]}

    def resources_for(self, token):
        with contextlib.closing(sqlite3.connect(self._path, timeout=5)) as conn:
            return [row[0] for row in conn.execute("SELECT resource FROM resource_lease WHERE token = ? ORDER BY resource", (token,))]

    @contextlib.contextmanager
    def idle_guard(self, resource):
        """Exclude new admission during a bounded local maintenance operation.

        This transaction creates no durable lease. Process death rolls it back.
        Never perform provider or network work while holding this guard.
        """
        self._validate_many([resource])
        with contextlib.closing(sqlite3.connect(self._path, timeout=0.05)) as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError:
                yield False
                return
            try:
                occupied = conn.execute("SELECT 1 FROM resource_lease WHERE resource = ?", (resource,)).fetchone()
                yield occupied is None
            finally:
                conn.rollback()
