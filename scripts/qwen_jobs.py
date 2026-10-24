import re
import threading
import uuid
from collections import OrderedDict

_JOB_RE = re.compile(r'^[0-9a-f]{32}$')


class JobLedger:
    def __init__(self, capacity: int = 256):
        if type(capacity) is not int or capacity <= 0:
            raise ValueError("capacity must be a positive integer")
        self.capacity = capacity
        self.generation = uuid.uuid4().hex
        self._ledger: OrderedDict = OrderedDict()
        self._lock = threading.Lock()

    def _validate(self, job_id: str) -> None:
        if not isinstance(job_id, str) or not _JOB_RE.fullmatch(job_id):
            raise ValueError("invalid job_id")

    def begin(self, job_id: str) -> None:
        self._validate(job_id)
        with self._lock:
            if job_id in self._ledger:
                raise RuntimeError("duplicate job_id")
            if len(self._ledger) >= self.capacity:
                for k in list(self._ledger):
                    if len(self._ledger) < self.capacity:
                        break
                    if self._ledger[k] in ("completed", "failed"):
                        del self._ledger[k]
                if len(self._ledger) >= self.capacity:
                    raise RuntimeError("capacity full")
            self._ledger[job_id] = "active"

    def finish(self, job_id: str, success: bool) -> None:
        self._validate(job_id)
        with self._lock:
            if job_id not in self._ledger:
                raise ValueError("job_id not found")
            if self._ledger[job_id] != "active":
                raise ValueError("job is not active")
            self._ledger[job_id] = "completed" if success else "failed"

    def status(self, job_id: str) -> dict:
        self._validate(job_id)
        with self._lock:
            state = self._ledger.get(job_id, "unknown")
        return {
            "schema_version": 1,
            "generation": self.generation,
            "job_id": job_id,
            "state": state,
        }
