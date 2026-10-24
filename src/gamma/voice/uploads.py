"""Bounded, cancellation-owned voice upload storage."""
import asyncio
import os
import threading


class VoiceUploadTooLarge(ValueError):
    pass


async def save_stream(source, target, *, max_bytes):
    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError("voice upload limit must be a positive integer")
    cancelled, created = threading.Event(), threading.Event()

    def copy():
        try:
            with target.open("xb") as output:
                created.set()
                os.chmod(target, 0o600)
                size = 0
                while not cancelled.is_set():
                    chunk = source.read(min(65536, max_bytes - size + 1))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise VoiceUploadTooLarge("voice upload exceeds limit")
                    output.write(chunk)
            if cancelled.is_set():
                target.unlink(missing_ok=True)
            return target
        except BaseException:
            if created.is_set():
                target.unlink(missing_ok=True)
            raise

    task = asyncio.create_task(asyncio.to_thread(copy))
    interrupted = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            cancelled.set()
            interrupted = True
            if task.cancelled():
                raise
        except BaseException:
            if interrupted:
                raise asyncio.CancelledError() from None
            raise
    if interrupted:
        if created.is_set():
            target.unlink(missing_ok=True)
        raise asyncio.CancelledError()
    return result
