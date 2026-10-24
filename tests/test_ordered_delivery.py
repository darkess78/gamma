import threading

import pytest

from gamma.delivery.ordered import OrderedDelivery
from gamma.brain.queue import BrainQueueClosed, BrainQueueFull


def test_fifo_capacity_and_shutdown_cancel_waiting_without_replacing_active():
    queue = OrderedDelivery(capacity=1)
    entered, release = threading.Event(), threading.Event()
    cancelled = []
    try:
        first = queue.submit(lambda: (entered.set(), release.wait(5)), on_cancel=lambda: cancelled.append("active"))
        assert entered.wait(5)
        waiting = queue.submit(lambda: pytest.fail("cancelled delivery ran"), on_cancel=lambda: cancelled.append("waiting"))
        with pytest.raises(BrainQueueFull):
            queue.submit(lambda: None, on_cancel=lambda: cancelled.append("overflow"))
        assert not queue.close(timeout=0)
        assert waiting.cancelled() and not first.cancelled()
        with pytest.raises(BrainQueueClosed):
            queue.start()
        assert cancelled == ["overflow", "waiting"]
        release.set()
        first.result(5)
        assert queue.close(timeout=5)
        queue.start()
        assert queue.submit(lambda: 7, on_cancel=lambda: None).result(5) == 7
    finally:
        release.set()
        queue.close(timeout=5)
