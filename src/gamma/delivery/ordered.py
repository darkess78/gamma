"""Ordered delivery wrapper around BrainQueue."""
import threading

from ..brain.queue import BrainQueue, BrainQueueClosed


class OrderedDelivery:
    def __init__(self, *, capacity=8):
        self._capacity = capacity
        self._lock = threading.Lock()
        self._queue = BrainQueue(capacity=self._capacity)

    def submit(self, function, *, on_cancel):
        with self._lock:
            queue = self._queue
        try:
            future = queue.submit(function, priority="foreground")
        except Exception:
            try:
                on_cancel()
            except BaseException:
                pass
            raise

        def _callback(fut):
            try:
                if fut.cancelled():
                    on_cancel()
            except BaseException:
                pass

        future.add_done_callback(_callback)
        return future

    def status(self):
        with self._lock:
            queue = self._queue
        return queue.status()

    def close(self, *, timeout=5):
        with self._lock:
            queue = self._queue
        return queue.close(timeout=timeout)

    def start(self):
        with self._lock:
            state = self._queue.status()["state"]
            if state == "draining":
                raise BrainQueueClosed("brain queue is draining")
            if state == "stopped":
                self._queue = BrainQueue(capacity=self._capacity)
