"""Await an owned brain-queue future with shielding and cancel semantics."""
import asyncio

from .queue import BrainQueueClosed


async def await_owned_future(future):
    result = asyncio.wrap_future(future)
    try:
        return await asyncio.shield(result)
    except asyncio.CancelledError:
        if future.cancelled():
            raise BrainQueueClosed("queued brain job cancelled during shutdown") from None
        future.cancel()  # Cancels waiting work only; active capacity remains held.
        result.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        raise
