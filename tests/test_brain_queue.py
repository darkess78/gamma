"""Dependency-free queue contracts, also runnable in a coding-worker sandbox."""
import importlib.util
import contextvars
from pathlib import Path
import threading
import unittest

spec = importlib.util.spec_from_file_location("brain_queue", Path(__file__).resolve().parents[1] / "src/gamma/brain/queue.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class BrainQueueTest(unittest.TestCase):
    def test_serial_execution_and_bounded_waiting(self):
        queue = module.BrainQueue(capacity=1)
        entered, release = threading.Event(), threading.Event()
        calls = []
        def first():
            calls.append(threading.get_ident())
            entered.set()
            self.assertTrue(release.wait(3))
            return 1
        try:
            one = queue.submit(first)
            self.assertTrue(entered.wait(3))
            two = queue.submit(lambda: (calls.append(threading.get_ident()), 2)[1])
            with self.assertRaises(module.BrainQueueFull):
                queue.submit(lambda: 3)
            self.assertEqual(queue.status()["pending"], 1)
            self.assertTrue(queue.status()["active"])
            release.set()
            self.assertEqual(one.result(3), 1)
            self.assertEqual(two.result(3), 2)
            self.assertEqual(len(set(calls)), 1)
            self.assertNotEqual(calls[0], threading.get_ident())
        finally:
            release.set()
            self.assertTrue(queue.close(timeout=3))

    def test_stop_cancels_waiting_but_retains_active_capacity(self):
        queue = module.BrainQueue(capacity=2)
        entered, release = threading.Event(), threading.Event()
        called = threading.Event()
        try:
            active = queue.submit(lambda: (entered.set(), release.wait(3), "done")[-1])
            self.assertTrue(entered.wait(3))
            pending = queue.submit(called.set)
            self.assertFalse(active.cancel())
            self.assertFalse(queue.close(timeout=0))
            self.assertTrue(pending.cancelled())
            self.assertFalse(called.is_set())
            self.assertTrue(queue.status()["active"])
            self.assertEqual(queue.status()["state"], "draining")
            with self.assertRaises(module.BrainQueueClosed):
                queue.submit(lambda: None)
            release.set()
            self.assertEqual(active.result(3), "done")
        finally:
            release.set()
            self.assertTrue(queue.close(timeout=3))
        self.assertEqual(queue.status()["state"], "stopped")

    def test_cancelled_pending_job_never_runs_and_failure_does_not_kill_worker(self):
        queue = module.BrainQueue(capacity=2)
        entered, release = threading.Event(), threading.Event()
        called = threading.Event()
        try:
            first = queue.submit(lambda: (entered.set(), release.wait(3)))
            self.assertTrue(entered.wait(3))
            pending = queue.submit(called.set)
            self.assertTrue(pending.cancel())
            release.set()
            first.result(3)
            def fail():
                raise ValueError("controlled")
            failed = queue.submit(fail)
            with self.assertRaisesRegex(ValueError, "controlled"):
                failed.result(3)
            self.assertEqual(queue.submit(lambda: 7).result(3), 7)
            self.assertFalse(called.is_set())
        finally:
            release.set()
            queue.close(timeout=3)

    def test_unused_close_and_invalid_capacity(self):
        for capacity in (0, -1):
            with self.assertRaises(ValueError):
                module.BrainQueue(capacity=capacity)
        queue = module.BrainQueue(capacity=1)
        self.assertTrue(queue.close(timeout=0))
        with self.assertRaises(module.BrainQueueClosed):
            queue.submit(lambda: None)

    def test_request_context_is_copied_without_cross_request_leakage(self):
        context = contextvars.ContextVar("request", default="none")
        queue = module.BrainQueue(capacity=2)
        try:
            token = context.set("first")
            try:
                one = queue.submit(context.get)
            finally:
                context.reset(token)
            self.assertEqual(one.result(3), "first")
            self.assertEqual(queue.submit(context.get).result(3), "none")
            nested = queue.submit(lambda: queue.submit(lambda: None))
            with self.assertRaises(module.BrainQueueClosed):
                nested.result(3)
        finally:
            queue.close(timeout=3)

    def test_cancellation_callback_can_inspect_queue(self):
        queue = module.BrainQueue(capacity=1)
        entered, release, callback = threading.Event(), threading.Event(), threading.Event()
        try:
            queue.submit(lambda: (entered.set(), release.wait(3)))
            self.assertTrue(entered.wait(3))
            pending = queue.submit(lambda: None)
            pending.add_done_callback(lambda _: (queue.status(), callback.set()))
            self.assertFalse(queue.close(timeout=0))
            self.assertTrue(callback.wait(1))
        finally:
            release.set()
            queue.close(timeout=3)


if __name__ == "__main__":
    unittest.main()


def test_cancel_reclaims_both_lanes_while_provider_is_stalled_or_paused():
    for priority in ('foreground', 'background'):
        # One pending slot is reserved for foreground; background needs two total.
        queue = module.BrainQueue(capacity=2 if priority == 'background' else 1)
        entered, release = threading.Event(), threading.Event()
        called = []
        try:
            active = queue.submit(lambda: (entered.set(), release.wait(5)))
            assert entered.wait(2)
            pending = queue.submit(lambda: called.append('cancelled'), priority=priority)
            with unittest.TestCase().assertRaises(module.BrainQueueFull):
                queue.submit(lambda: called.append('overflow'), priority=priority)
            observed = []
            pending.add_done_callback(lambda _: observed.append(queue.status()['pending']))
            queue.pause()
            assert pending.cancel()
            assert observed == [0]
            assert queue.status()['pending'] == 0
            assert queue.status()['active']
            assert not active.cancel()
            queue.resume()
            replacement = queue.submit(lambda: called.append('replacement'), priority=priority)
            assert queue.status()['pending'] == 1
            release.set()
            replacement.result(2)
            assert called == ['replacement']
        finally:
            release.set()
            assert queue.close(timeout=3)


def test_cancel_reclaims_capacity_before_failing_external_callback():
    queue = module.BrainQueue(capacity=1)
    entered, release = threading.Event(), threading.Event()
    try:
        queue.submit(lambda: (entered.set(), release.wait(5)))
        assert entered.wait(2)
        pending = queue.submit(lambda: None)
        def fail(_):
            raise KeyboardInterrupt('fixture')
        pending.add_done_callback(fail)
        try:
            pending.cancel()
        except KeyboardInterrupt:
            pass
        assert queue.status()['pending'] == 0
        next_job = queue.submit(lambda: 'replacement')
        release.set()
        assert next_job.result(2) == 'replacement'
    finally:
        release.set()
        assert queue.close(timeout=3)
