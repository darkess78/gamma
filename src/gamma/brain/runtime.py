"""Process-owned ingress queue, retained while uncancellable work drains."""
import threading

from .queue import BrainQueue, BrainQueueClosed
from .pending import await_owned_future

_lock = threading.Lock()
_queue = None
_stream_epoch = 0


def stream_ingress_epoch():
    with _lock:
        return _stream_epoch


def invalidate_stream_ingress():
    global _stream_epoch
    with _lock:
        _stream_epoch += 1


def get_brain_queue():
    global _queue
    with _lock:
        if _queue is None:
            _queue = BrainQueue(capacity=32)
        return _queue


def start_brain_queue():
    global _queue
    with _lock:
        if _queue is not None:
            state = _queue.status()["state"]
            if state == "draining":
                raise BrainQueueClosed("previous brain execution is still draining")
            if state != "stopped":
                return _queue
        _queue = BrainQueue(capacity=32)
        return _queue


async def await_brain_job(function, *, priority="foreground"):
    future = get_brain_queue().submit(function, priority=priority)
    return await await_owned_future(future)
