import importlib.util
from pathlib import Path
import threading
import unittest

spec = importlib.util.spec_from_file_location("brain_queue", Path(__file__).resolve().parents[1] / "src/gamma/brain/queue.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class BrainPauseTest(unittest.TestCase):
    def test_pause_retains_active_and_pending_then_resumes_same_thread(self):
        queue = module.BrainQueue()
        entered, release, ran = threading.Event(), threading.Event(), threading.Event()
        try:
            active = queue.submit(lambda: (entered.set(), release.wait(3), threading.get_ident())[-1])
            self.assertTrue(entered.wait(3))
            pending = queue.submit(lambda: (ran.set(), threading.get_ident())[-1])
            queue.pause()
            self.assertEqual(queue.status()["state"], "pausing")
            self.assertTrue(queue.status()["active"])
            with self.assertRaises(module.BrainQueueClosed):
                queue.submit(lambda: None)
            release.set()
            worker = active.result(3)
            self.assertEqual(queue.status()["state"], "paused")
            self.assertFalse(ran.wait(0.05))
            self.assertEqual(queue.status()["pending"], 1)
            queue.resume()
            self.assertEqual(pending.result(3), worker)
        finally:
            release.set()
            queue.close(timeout=3)

    def test_close_paused_cancels_pending_and_resume_cannot_reopen(self):
        queue = module.BrainQueue()
        queue.pause()
        self.assertEqual(queue.status()["state"], "paused")
        queue.pause()
        self.assertTrue(queue.close(timeout=0))
        with self.assertRaises(module.BrainQueueClosed):
            queue.resume()
        with self.assertRaises(module.BrainQueueClosed):
            queue.pause()

    def test_shutdown_paused_queue_cancels_waiting_without_releasing_active(self):
        queue = module.BrainQueue()
        entered, release = threading.Event(), threading.Event()
        try:
            active = queue.submit(lambda: (entered.set(), release.wait(3)))
            self.assertTrue(entered.wait(3))
            pending = queue.submit(lambda: self.fail("paused job executed"))
            queue.pause()
            self.assertFalse(queue.close(timeout=0))
            self.assertTrue(pending.cancelled())
            self.assertFalse(active.done())
            self.assertEqual(queue.status()["state"], "draining")
            release.set()
            active.result(3)
        finally:
            release.set()
            self.assertTrue(queue.close(timeout=3))


if __name__ == "__main__":
    unittest.main()
