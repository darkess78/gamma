"""Per-turn cancellation serialized with the complete authorized commit."""
from contextlib import contextmanager
import threading


class TurnCancelled(RuntimeError):
    """Cancellation won before the guarded state or execution boundary."""


class CancellationBoundary:
    def __init__(self):
        self._lock = threading.RLock()
        self._cancelled = False

    def is_set(self):
        with self._lock:
            return self._cancelled

    def set(self):
        with self._lock:
            self._cancelled = True

    @contextmanager
    def guard(self):
        # Hold through commit, not merely an earlier check before persistence.
        with self._lock:
            if self._cancelled:
                raise TurnCancelled("turn_cancelled_before_commit")
            yield
