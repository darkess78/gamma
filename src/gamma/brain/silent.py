"""Finite, explicitly enabled internal work on the existing brain queue."""
import threading
import time
from contextlib import nullcontext

from .queue import BackgroundYield


class SilentCycles:
    def __init__(self, prepare, allowed, *, clock=time.monotonic, queue=None, work=None):
        self._prepare, self._allowed, self._clock = prepare, allowed, clock
        self._lock = threading.RLock()
        self._enabled = self._active = False
        self._epoch = self._remaining = self._completed = 0
        self._cooldown = 60
        self._next_at = 0
        self._reason = "disabled"
        self._queue, self.work = queue, work
        self._paused = False
        self._cycle_seconds = 60
        self._last_result = None

    def status(self):
        with self._lock:
            return dict(enabled=self._enabled, active=self._active, remaining=self._remaining,
                        completed=self._completed, cooldown_seconds=self._cooldown, last_reason=self._reason,
                        paused=self._paused, generation=self._epoch, cycle_seconds=self._cycle_seconds,
                        last_result=self._last_result,
                        observations=self.work.inbox.status() if self.work else None)

    def enable(self, *, budget, cooldown_seconds, cycle_seconds=60):
        if type(budget) is not int or not 1 <= budget <= 8:
            raise ValueError("budget must be an integer from 1 to 8")
        if type(cooldown_seconds) is not int or not 5 <= cooldown_seconds <= 3600:
            raise ValueError("cooldown must be an integer from 5 to 3600")
        if type(cycle_seconds) is not int or not 1 <= cycle_seconds <= 60:
            raise ValueError("cycle_seconds must be an integer from 1 to 60")
        with self._lock:
            if self._active:
                raise RuntimeError("silent cycle is active")
            self._epoch += 1
            self._enabled, self._remaining = True, budget
            self._completed, self._next_at = 0, 0
            self._cooldown, self._reason = cooldown_seconds, "enabled"
            self._paused, self._cycle_seconds = False, cycle_seconds
            self._last_result = None
            if self.work:
                self.work.reset()
            return self.status()

    def disable(self):
        with self._lock:
            self._epoch += 1
            self._enabled = False
            self._reason = "disabled"
            if self.work:
                self.work.inbox.clear()
            return self.status()

    def pause(self):
        with self._lock:
            self._epoch += 1
            self._paused = True
            self._reason = "paused"
            return self.status()

    def resume(self):
        with self._lock:
            self._paused = False
            self._reason = "resumed" if self._enabled else "disabled"
            return self.status()

    def tick(self):
        with self._lock:
            if not self._enabled or self._active or self._paused:
                return self.status()
            if self._clock() < self._next_at:
                self._reason = "cooldown"
                return self.status()
            if not self._allowed():
                self._reason = "policy_suppressed"
                return self.status()
            queue = self._queue() if self._queue else None
            attention = queue.attention_generation() if queue else None
            if queue:
                try:
                    with queue.background_guard(attention):
                        pass
                except BackgroundYield:
                    self._reason = "foreground_or_control"
                    return self.status()
            selected = self.work.select() if self.work else None
            if self.work and selected is None:
                self._reason = "no_work"
                return self.status()
            try:
                with queue.background_guard(attention) if queue else nullcontext():
                    epoch = self._epoch
                    deadline = self._clock() + self._cycle_seconds
                    self._active = True
                    self._remaining -= 1
            except BackgroundYield:
                self._reason = "foreground_or_control"
                return self.status()
        prepared = None
        try:
            prepared = self._prepare(selected) if self.work else self._prepare()
            with self._lock:
                if self._clock() >= deadline:
                    self._reason = "deadline_exceeded"
                elif self._enabled and not self._paused and self._epoch == epoch and self._allowed():
                    with queue.background_guard(attention) if queue else nullcontext():
                        result = prepared()
                    self._completed += 1
                    self._last_result = result if isinstance(result, dict) else None
                    if self.work:
                        self.work.complete(selected)
                    self._reason = "completed"
                else:
                    self._reason = "discarded"
        except BackgroundYield:
            with self._lock:
                self._reason = "foreground_or_control"
        except BaseException:
            with self._lock:
                self._reason = "cycle_failed"
            raise
        finally:
            try:
                if prepared is not None and hasattr(prepared, "discard"):
                    prepared.discard()
            finally:
                with self._lock:
                    self._active = False
                    self._next_at = self._clock() + self._cooldown
                    if self._remaining == 0:
                        self._enabled = False
        return self.status()
