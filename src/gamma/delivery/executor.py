"""Bounded async delivery execution that retains capacity until provider exit."""
import asyncio
import os
import threading


class DeliveryQueueFull(RuntimeError):
    pass


class DeliveryExecutor:
    def __init__(self, capacity: int = 8):
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity <= 0:
            raise TypeError("capacity must be a positive int")
        self._sem = threading.BoundedSemaphore(capacity)
        self._pid = os.getpid()

    async def run(self, function, *, on_cancel):
        if os.getpid() != self._pid:
            raise RuntimeError("DeliveryExecutor bound to a different pid")
        if not self._sem.acquire(blocking=False):
            on_cancel()
            raise DeliveryQueueFull("delivery queue full")
        cancel = threading.Event()

        def _exec():
            try:
                if cancel.is_set():
                    raise asyncio.CancelledError()
                return function()
            finally:
                self._sem.release()

        task = asyncio.ensure_future(asyncio.to_thread(_exec))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            cancel.set()
            on_cancel()
            task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
            raise

    def run_sync(self, function, *, on_cancel):
        if os.getpid() != self._pid:
            raise RuntimeError("DeliveryExecutor bound to a different pid")
        if not self._sem.acquire(blocking=False):
            on_cancel()
            raise DeliveryQueueFull("delivery queue full")
        try:
            return function()
        finally:
            self._sem.release()
