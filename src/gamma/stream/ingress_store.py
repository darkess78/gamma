"""Stdlib SQLite durable ingress receipt store."""
import os
import sqlite3
import time

class IngressFull(Exception):
    pass

class IngressConflict(Exception):
    pass

_STATES = {"accepted", "running", "completed", "failed", "cancelled", "uncertain"}
_TERMINAL = {"completed", "failed", "cancelled"}
_TRANS = {
    "accepted": {"running", "failed", "cancelled", "uncertain"},
    "running": {"completed", "failed", "uncertain"},
}


def _receipt(r):
    return {
        "job_id": r["id"], "state": r["state"], "created_at": r["created"],
        "updated_at": r["updated"], "reason_code": r["reason"], "owner": r["owner"],
    }


class IngressStore:
    def __init__(self, path, capacity=32, history_limit=4096, retention_seconds=86400):
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity <= 0:
            raise ValueError("capacity must be a positive integer")
        if not isinstance(history_limit, int) or isinstance(history_limit, bool) or history_limit <= 0:
            raise ValueError("history_limit must be a positive integer")
        if not isinstance(retention_seconds, (int, float)) or isinstance(retention_seconds, bool):
            raise ValueError("retention_seconds must be positive finite number")
        if retention_seconds <= 0 or retention_seconds != retention_seconds or retention_seconds == float("inf"):
            raise ValueError("retention_seconds must be positive finite")
        self._path = path
        self._cap = capacity
        self._hist = history_limit
        self._ret = retention_seconds
        d = os.path.dirname(os.path.abspath(path))
        os.makedirs(d, exist_ok=True)
        self._init()

    def _c(self):
        c = sqlite3.connect(self._path, timeout=5)
        c.row_factory = sqlite3.Row
        return c

    def _init(self):
        c = self._c()
        try:
            c.execute("CREATE TABLE IF NOT EXISTS receipts("
                      "id TEXT PRIMARY KEY, digest TEXT, owner TEXT,"
                      "state TEXT, created REAL, updated REAL, reason TEXT)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_state ON receipts(state)")
            c.commit()
        finally:
            c.close()

    def claim(self, job_id, digest, owner):
        c = self._c()
        try:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM receipts WHERE id=?", (job_id,)).fetchone()
            if row:
                c.execute("ROLLBACK")
                if row["digest"] == digest:
                    return _receipt(row), False
                raise IngressConflict("event payload conflict")
            now = time.time()
            c.execute("DELETE FROM receipts WHERE state IN('completed','failed','cancelled') AND updated<?",
                      (now - self._ret,))
            active = c.execute("SELECT COUNT(*) n FROM receipts WHERE state IN('accepted','running')").fetchone()["n"]
            if active >= self._cap:
                c.execute("ROLLBACK")
                raise IngressFull()
            total = c.execute("SELECT COUNT(*) n FROM receipts").fetchone()["n"]
            if total >= self._hist:
                c.execute("ROLLBACK")
                raise IngressFull()
            c.execute("INSERT INTO receipts VALUES(?,?,?,?,?,?,?)",
                      (job_id, digest, owner, "accepted", now, now, ""))
            c.execute("COMMIT")
            return {"job_id": job_id, "state": "accepted", "created_at": now,
                    "updated_at": now, "reason_code": "", "owner": owner}, True
        except Exception:
            try:
                c.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            c.close()

    def get(self, job_id):
        c = self._c()
        try:
            row = c.execute("SELECT * FROM receipts WHERE id=?", (job_id,)).fetchone()
            return _receipt(row) if row else None
        finally:
            c.close()

    def transition(self, job_id, owner, state, reason=""):
        if state not in _STATES:
            raise ValueError("invalid state")
        if len(reason) > 64:
            raise ValueError("reason too long")
        c = self._c()
        try:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM receipts WHERE id=?", (job_id,)).fetchone()
            if not row or row["owner"] != owner:
                return False
            cur = row["state"]
            if cur in _TERMINAL or cur == "uncertain":
                return False
            if state not in _TRANS.get(cur, set()):
                return False
            now = time.time()
            c.execute("UPDATE receipts SET state=?, updated=?, reason=? WHERE id=?",
                      (state, now, reason, job_id))
            c.commit()
            return True
        finally:
            c.close()

    def orphan(self, owner):
        c = self._c()
        try:
            now = time.time()
            c.execute("UPDATE receipts SET state='uncertain', updated=?, reason='owner_exited'"
                      " WHERE owner=? AND state IN('accepted','running')", (now, owner))
            c.commit()
        finally:
            c.close()

    def active_owners(self):
        c = self._c()
        try:
            return [r[0] for r in c.execute("SELECT DISTINCT owner FROM receipts WHERE state IN ('accepted','running')")]
        finally:
            c.close()

    def stats(self):
        c = self._c()
        try:
            active = c.execute("SELECT COUNT(*) n FROM receipts WHERE state IN('accepted','running')").fetchone()["n"]
            total = c.execute("SELECT COUNT(*) n FROM receipts").fetchone()["n"]
            m = c.execute("SELECT MIN(created) m FROM receipts WHERE state IN('accepted','running')").fetchone()["m"]
            return {"active_count": active, "total_count": total,
                    "oldest_active_age_seconds": (time.time() - m) if m is not None else 0.0}
        finally:
            c.close()
