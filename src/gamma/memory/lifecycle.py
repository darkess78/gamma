"""Core-owned recovery scheduling, independent of model or conversation readiness."""
import asyncio
import logging

from ..observability import configure_logging, log_event
from .continuity import ContinuityService
from ..delivery.maintenance import DeliveryMaintenance
from ..brain.image_maintenance import ImageMaintenance
from ..resources.admission import Admission
from ..config import settings


class StateLifecycle:
    def __init__(self, continuity: ContinuityService | None = None):
        self.continuity = continuity
        self._stop = asyncio.Event()
        self._task = None
        self._delivery = None
        self._images = None
        self._logger = configure_logging("state")

    def _reconcile(self):
        if self.continuity is None:
            self.continuity = ContinuityService()
        recovered = self.continuity.reconcile_expired_generations()
        if recovered:
            log_event(self._logger, logging.INFO, "state.recovery.completed",
                      "Expired worker exchanges reconciled.", count=recovered)
        try:
            if self._delivery is None:
                self._delivery = DeliveryMaintenance(database_url=str(self.continuity._engine.url))
            counts = self._delivery.sweep()
            if any(counts[key] for key in ("receipts", "grants", "leases", "artifacts", "errors")):
                log_event(self._logger, logging.INFO, "delivery.maintenance.completed",
                          "Delivery retention batch completed.", **counts)
        except Exception as exc:
            log_event(self._logger, logging.ERROR, "delivery.maintenance.failed",
                      "Delivery retention deferred.", error_class=type(exc).__name__)
        try:
            if self._images is None:
                self._images = ImageMaintenance(settings.data_dir / "runtime/model-images",
                    Admission(settings.data_dir / "runtime/model-admission.sqlite3"))
            counts = self._images.sweep()
            if counts["artifacts"] or counts["errors"]:
                log_event(self._logger, logging.INFO, "images.maintenance.completed",
                          "Inference image retention batch completed.", **counts)
        except Exception as exc:
            log_event(self._logger, logging.ERROR, "images.maintenance.failed",
                      "Inference image retention deferred.", error_class=type(exc).__name__)

    async def start(self):
        await asyncio.to_thread(self._reconcile)
        self._task = asyncio.create_task(self._run())

    async def _run(self):
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=30)
            except TimeoutError:
                try:
                    await asyncio.to_thread(self._reconcile)
                except Exception as exc:
                    log_event(self._logger, logging.ERROR, "state.recovery.failed",
                              "State recovery deferred.", error_class=type(exc).__name__)

    async def stop(self):
        self._stop.set()
        if self._task is not None:
            await self._task
        try:
            if self._delivery is not None:
                await asyncio.to_thread(self._delivery.close)
        finally:
            if self._images is not None:
                await asyncio.to_thread(self._images.close)
