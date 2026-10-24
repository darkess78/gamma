import asyncio
import io
import importlib.util
from pathlib import Path
import tempfile
import unittest
import threading

spec = importlib.util.spec_from_file_location("voice_uploads", Path(__file__).resolve().parents[1] / "src/gamma/voice/uploads.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class UploadTests(unittest.TestCase):
    def test_cancellation_waits_for_copy_and_removes_partial_file(self):
        async def run(root):
            entered, release = threading.Event(), threading.Event()
            class Slow(io.BytesIO):
                def read(self, size):
                    entered.set()
                    if not release.wait(3):
                        raise TimeoutError("fixture")
                    return super().read(size)
            target = root / "cancelled.wav"
            job = asyncio.create_task(module.save_stream(Slow(b"fixture"), target, max_bytes=100))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                job.cancel()
                await asyncio.sleep(0)
                self.assertFalse(job.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await job
                self.assertFalse(target.exists())
            finally:
                release.set()
        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(run(Path(directory)))

    def test_exact_limit_and_overflow(self):
        async def run(root):
            target = root / "accepted.wav"
            self.assertEqual(await module.save_stream(io.BytesIO(b"1234"), target, max_bytes=4), target)
            self.assertEqual(target.read_bytes(), b"1234")
            rejected = root / "rejected.wav"
            with self.assertRaises(module.VoiceUploadTooLarge):
                await module.save_stream(io.BytesIO(b"12345"), rejected, max_bytes=4)
            self.assertFalse(rejected.exists())
        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(run(Path(directory)))

    def test_never_overwrites_existing_file(self):
        async def run(root):
            target = root / "prior.wav"
            target.write_bytes(b"prior")
            with self.assertRaises(FileExistsError):
                await module.save_stream(io.BytesIO(b"new"), target, max_bytes=4)
            self.assertEqual(target.read_bytes(), b"prior")
        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(run(Path(directory)))


if __name__ == "__main__":
    unittest.main()
