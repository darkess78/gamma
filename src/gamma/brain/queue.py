"""Bounded brain execution queue."""
from collections import deque
from concurrent.futures import Future
from contextvars import copy_context
from contextlib import contextmanager
import os
import threading


class BrainQueueClosed(RuntimeError):
    pass


class BrainQueueFull(RuntimeError):
    pass


class BackgroundYield(RuntimeError):
    pass


class BrainQueue:
    def __init__(self, *, capacity=32):
        if type(capacity) is not int or capacity < 1:
            raise ValueError("capacity must be a positive integer")
        self._capacity = capacity
        self._pid = os.getpid()
        self._condition = threading.Condition()
        self._pending = deque()
        self._background = deque()
        self._foreground_burst = 0
        self._thread = None
        self._closed = False
        self._active = False
        self._paused = False
        self._attention_generation = 0
        self._rejected = 0

    def _check_process(self):
        if os.getpid() != self._pid:
            raise BrainQueueClosed("brain queue belongs to another process")

    def _release_cancelled_future(self, future):
        if not future.cancelled():
            return
        with self._condition:
            for lane in (self._pending, self._background):
                for index, item in enumerate(lane):
                    if item[0] is future:
                        del lane[index]
                        return

    def submit(self, function, *, priority="foreground"):
        self._check_process()
        if not callable(function):
            raise TypeError("brain job must be callable")
        if priority not in ("foreground", "background"):
            raise ValueError("unsupported brain priority")
        future = Future()
        context = copy_context()
        if priority == "background":
            from gamma.resources.shared_execution import _priority
            context.run(_priority.set, "background")
        with self._condition:
            if self._closed or threading.current_thread() is self._thread:
                raise BrainQueueClosed("brain queue closed or nested submission")
            if self._paused:
                raise BrainQueueClosed("brain queue paused")
            if len(self._pending) + len(self._background) >= self._capacity:
                self._rejected += 1
                raise BrainQueueFull("brain queue full")
            if priority == "background" and len(self._background) >= max(0, self._capacity - 1):
                self._rejected += 1
                raise BrainQueueFull("brain capacity reserved for foreground")
            if priority == "foreground":
                self._attention_generation += 1
            lane = self._pending if priority == "foreground" else self._background
            lane.append((future, context, function))
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="gamma-brain", daemon=True)
                try:
                    self._thread.start()
                except Exception:
                    self._thread = None
                    lane.pop()
                    raise
            self._condition.notify()
        future.add_done_callback(self._release_cancelled_future)
        return future

    def pause(self):
        self._check_process()
        with self._condition:
            if self._closed:
                raise BrainQueueClosed("brain queue closed")
            self._paused = True
            self._attention_generation += 1

    def attention_generation(self):
        with self._condition:
            return self._attention_generation

    @contextmanager
    def background_guard(self, generation):
        """Linearize a short internal commit against foreground and controls."""
        with self._condition:
            if (generation != self._attention_generation or self._pending
                    or self._paused or self._closed):
                raise BackgroundYield("foreground_or_control")
            yield

    def resume(self):
        self._check_process()
        with self._condition:
            if self._closed:
                raise BrainQueueClosed("brain queue closed")
            self._paused = False
            self._condition.notify_all()

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or (not self._paused and (self._pending or self._background)))
                if self._closed:
                    return
                if self._background and (not self._pending or self._foreground_burst >= 8):
                    future, context, function = self._background.popleft()
                    self._foreground_burst = 0
                else:
                    future, context, function = self._pending.popleft()
                    self._foreground_burst = self._foreground_burst + 1 if self._background else 0
                self._active = True
            result, error = None, None
            running = future.set_running_or_notify_cancel()
            if running:
                try:
                    result = context.run(function)
                except BaseException as exc:
                    error = exc
            with self._condition:
                self._active = False
            if running:
                # Future callbacks run outside the queue lock and cannot kill
                # the execution lane, even if one raises a BaseException.
                try:
                    if error is None:
                        future.set_result(result)
                    else:
                        future.set_exception(error)
                except BaseException:
                    pass

    def status(self):
        self._check_process()
        with self._condition:
            if self._closed:
                alive = self._thread is not None and self._thread.is_alive()
                state = "draining" if alive else "stopped"
            else:
                alive = self._thread is not None and self._thread.is_alive()
                if self._paused:
                    state = "pausing" if self._active else "paused"
                else:
                    state = "running" if alive else "idle"
            return {"state": state, "pending": len(self._pending) + len(self._background), "active": self._active,
                    "pending_foreground": len(self._pending), "pending_background": len(self._background),
                    "capacity": self._capacity, "rejected": self._rejected,
                    "attention_generation": self._attention_generation}

    def close(self, *, timeout=5):
        self._check_process()
        with self._condition:
            self._closed = True
            self._attention_generation += 1
            pending = [*self._pending, *self._background]
            self._pending.clear()
            self._background.clear()
            worker = self._thread
            self._condition.notify_all()
        for future, _, _ in pending:
            try:
                future.cancel()
            except BaseException:
                pass  # One failing callback must not retain other waiting jobs.
        if worker is not None:
            if threading.current_thread() is worker:
                return False
            worker.join(timeout=timeout)
            return not worker.is_alive()
        return True
