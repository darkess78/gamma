"""Application-owned journal recovery, independent of conversation generation."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from .continuity import ContinuityService


class JournalRecovery:
    """Reconcile bounded journal batches at startup and while Shana is running."""

    def __init__(self, *, factory: Callable[[], ContinuityService] = ContinuityService,
                 interval_seconds: float = 30.0, batch_size: int = 100) -> None:
        if interval_seconds <= 0 or not 1 <= batch_size <= 1000:
            raise ValueError("Invalid journal recovery bounds")
        self._factory = factory
        self._interval = interval_seconds
        self._batch_size = batch_size
        self._continuity: ContinuityService | None = None
        self._stop = asyncio.Event()
        self._started = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._logger = logging.getLogger("gamma.journal_recovery")

    def _reconcile(self) -> None:
        if self._continuity is None:
            self._continuity = self._factory()
        self._continuity.reconcile_interrupted(limit=self._batch_size)

    async def _attempt(self) -> None:
        try:
            await asyncio.to_thread(self._reconcile)
        except Exception as exc:
            # Do not log SQL parameters, journal text, or process metadata.
            self._logger.error("Journal recovery deferred (%s)", type(exc).__name__)

    async def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("Journal recovery already started")
        self._stop.clear()
        self._started.clear()
        self._task = asyncio.create_task(self._run(), name="journal-recovery")
        await self._started.wait()

    async def _run(self) -> None:
        try:
            await self._attempt()
        finally:
            self._started.set()
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except TimeoutError:
                await self._attempt()

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None
