import threading

import pytest

from gamma.brain.queue import BrainQueue, BrainQueueFull


def test_waiting_interactive_work_overtakes_background_without_starvation():
    queue = BrainQueue(capacity=16)
    entered, release = threading.Event(), threading.Event()
    order = []
    try:
        active = queue.submit(lambda: (entered.set(), release.wait(5)))
        assert entered.wait(5)
        background = [queue.submit(lambda n=n: order.append(("background", n)), priority="background") for n in range(2)]
        foreground = [queue.submit(lambda n=n: order.append(("foreground", n))) for n in range(10)]
        assert not active.cancel()
        assert order == []
        state = queue.status()
        assert state["pending"] == 12
        assert state["pending_foreground"] == 10 and state["pending_background"] == 2
        release.set()
        for result in [active, *background, *foreground]:
            result.result(5)
        assert order[0] == ("foreground", 0)
        assert order.index(("background", 0)) <= 8
        assert [n for kind, n in order if kind == "foreground"] == list(range(10))
        assert [n for kind, n in order if kind == "background"] == [0, 1]
    finally:
        release.set()
        assert queue.close(timeout=5)


def test_lanes_share_capacity_and_shutdown_cancels_both():
    queue = BrainQueue(capacity=2)
    entered, release = threading.Event(), threading.Event()
    try:
        active = queue.submit(lambda: (entered.set(), release.wait(5)))
        assert entered.wait(5)
        foreground = queue.submit(lambda: pytest.fail("cancelled foreground ran"))
        background = queue.submit(lambda: pytest.fail("cancelled background ran"), priority="background")
        with pytest.raises(BrainQueueFull):
            queue.submit(lambda: None, priority="background")
        callback = threading.Event()
        background.add_done_callback(lambda _: (queue.status(), callback.set()))
        assert not queue.close(timeout=0)
        assert foreground.cancelled() and background.cancelled() and callback.is_set()
        assert queue.status()["pending"] == 0 and queue.status()["active"]
        release.set()
        active.result(5)
    finally:
        release.set()
        queue.close(timeout=5)


def test_invalid_priority_rejected_before_execution():
    queue = BrainQueue()
    try:
        with pytest.raises(ValueError):
            queue.submit(lambda: pytest.fail("invalid priority ran"), priority="urgent")
        assert queue.status()["pending"] == 0
    finally:
        queue.close()
