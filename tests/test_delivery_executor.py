import asyncio
import importlib.util
from pathlib import Path
import threading
import unittest

spec = importlib.util.spec_from_file_location("delivery_executor", Path(__file__).resolve().parents[1] / "src/gamma/delivery/executor.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ExecutorTest(unittest.TestCase):
    def test_sync_work_shares_async_capacity_and_releases_after_failure(self):
        executor = module.DeliveryExecutor(capacity=1)
        cancelled = []
        async def attempt():
            with self.assertRaises(module.DeliveryQueueFull):
                await executor.run(lambda: self.fail("overflow ran"), on_cancel=lambda: cancelled.append(True))
        def fail():
            asyncio.run(attempt())
            raise ValueError("fixture")
        with self.assertRaises(ValueError):
            executor.run_sync(fail, on_cancel=lambda: None)
        self.assertEqual(cancelled, [True])
        self.assertEqual(executor.run_sync(lambda: 7, on_cancel=lambda: None), 7)

    def test_cancelled_waiting_thread_never_calls_provider(self):
        from concurrent.futures import ThreadPoolExecutor
        async def exercise():
            loop = asyncio.get_running_loop()
            loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
            entered, release = threading.Event(), threading.Event()
            held = asyncio.create_task(asyncio.to_thread(lambda: (entered.set(), release.wait(3))))
            while not entered.is_set(): await asyncio.sleep(0.001)
            calls = []
            executor = module.DeliveryExecutor(capacity=1)
            job = asyncio.create_task(executor.run(lambda: calls.append(True), on_cancel=lambda: None))
            try:
                await asyncio.sleep(0)
                job.cancel()
                with self.assertRaises(asyncio.CancelledError): await job
                release.set()
                await held
                await asyncio.to_thread(lambda: None)
                self.assertEqual(calls, [])
                self.assertEqual(await executor.run(lambda: 9, on_cancel=lambda: None), 9)
            finally: release.set()
        asyncio.run(exercise())

    def test_cancellation_retains_slot_until_provider_exit(self):
        async def exercise():
            executor = module.DeliveryExecutor(capacity=1)
            entered, release = threading.Event(), threading.Event()
            cancelled = []
            def provider():
                entered.set()
                if not release.wait(3): raise TimeoutError("fixture")
            task = asyncio.create_task(executor.run(provider, on_cancel=lambda: cancelled.append(True)))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                task.cancel()
                with self.assertRaises(asyncio.CancelledError): await task
                self.assertEqual(cancelled, [True])
                rejected = []
                with self.assertRaises(module.DeliveryQueueFull):
                    await executor.run(lambda: self.fail("overflow executed"), on_cancel=lambda: rejected.append(True))
                self.assertEqual(rejected, [True])
                release.set()
                deadline = asyncio.get_running_loop().time() + 3
                while True:
                    try:
                        self.assertEqual(await executor.run(lambda: 7, on_cancel=lambda: None), 7)
                        break
                    except module.DeliveryQueueFull:
                        self.assertLess(asyncio.get_running_loop().time(), deadline)
                        await asyncio.sleep(0.01)
            finally:
                release.set()
        asyncio.run(exercise())


if __name__ == "__main__": unittest.main()
