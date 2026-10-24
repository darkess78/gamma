"""Shared TTS child with durable admission across API process generations."""
import sys
import threading
from uuid import uuid4

from ..config import settings
from ..errors import ExternalServiceError
from ..resources.admission import Admission, AdmissionBusy
from .tts_client import TTSWorkerClient
from .tts_protocol import validate_synthesis


class TTSRuntime:
    def __init__(self, client, admission, receipt_root=None, events=None):
        self.client, self.admission = client, admission
        self.receipt_root = receipt_root
        self._events = events
        self._telemetry_closed = False
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._enabled = True
        self._resources = ("tts", "voice-inference")

    def _emit(self, event, **kwargs):
        try:
            if self._events and not self._telemetry_closed:
                self._events.emit(event, **kwargs)
        except Exception:
            pass

    def status(self):
        info = dict(self.client.status())
        info["busy"] = info.get("busy", False) or self._lock.locked()
        info["enabled"] = self._enabled
        info["closed"] = self._closed.is_set()
        adm = self.admission.status("tts")
        info["admission_state"] = adm["state"] if adm else None
        shared = self.admission.status("voice-inference")
        info["shared_admission_state"] = shared["state"] if shared else None
        info["controls"] = [] if self._closed.is_set() else ["start", "stop", "restart", "reconcile"]
        return info

    def control(self, action):
        if action not in ("start", "stop", "restart", "reconcile"):
            raise ValueError(f"unsupported control action: {action!r}")
        trace_id = uuid4().hex
        self._emit(f"lifecycle.{action}", trace_id=trace_id, outcome="started")
        if not self._lock.acquire(blocking=False):
            self._emit(f"lifecycle.{action}", trace_id=trace_id, outcome="failed", reason_code="control_failed")
            raise RuntimeError("TTS runtime is busy")
        try:
            if self._closed.is_set():
                raise RuntimeError("TTS worker is closed")
            if action in ("start", "restart"):
                if self.admission.status("tts") is not None:
                    raise RuntimeError(
                        f"TTS admission lease exists; cannot {action}")
                self._enabled = False
                if action == "restart":
                    self.client.close()
                self.client.start()
                self._enabled = True
            elif action == "stop":
                self._enabled = False
                self.client.close()
            elif action == "reconcile":
                from .tts_reconcile import reconcile
                if self.admission.status("tts") is not None:
                    if self.receipt_root is None or not reconcile(self.admission, self.receipt_root):
                        raise RuntimeError("TTS completion remains unproven")
                    self._enabled = False
            self._emit(f"lifecycle.{action}", trace_id=trace_id, outcome="completed")
        except BaseException:
            self._emit(f"lifecycle.{action}", trace_id=trace_id, outcome="failed", reason_code="control_failed")
            raise
        finally:
            self._lock.release()
        return {**self.status(), "trace_id": trace_id}

    def synthesize(self, text, emotion=None, styles=None):
        validate_synthesis({"text": text, "emotion": emotion, "styles": styles})
        with self._lock:
            if not self._enabled:
                raise ExternalServiceError("TTS runtime is disabled")
            if self._closed.is_set():
                raise ExternalServiceError("TTS worker is closed")
            try:
                token = self.admission.acquire_many(self._resources)
            except AdmissionBusy as exc:
                raise ExternalServiceError(
                    "TTS resource is occupied or awaiting reconciliation") from exc
            trace_id = uuid4().hex
            self._emit("job.started", trace_id=trace_id)
            try:
                result = self.client.synthesize(text=text, emotion=emotion, styles=styles, job_id=token)
            except BaseException as exc:
                from ..resources.shared_execution import is_busy
                self._emit("job.failed", trace_id=trace_id, outcome="failed", reason_code="synthesis_failed")
                if is_busy(exc):
                    self.admission.release_many(self._resources, token)
                else:
                    # Adapter death or timeout cannot prove remote inference ended.
                    self.admission.uncertain_many(self._resources, token)
                raise
            try:
                self.admission.release_many(self._resources, token)
            except Exception:
                self._emit("job.failed", trace_id=trace_id, outcome="failed", reason_code="release_failed")
                raise
            self._emit("job.completed", trace_id=trace_id)
            return result

    def close(self):
        self._closed.set()
        self._enabled = False
        with self._lock:
            try:
                self.client.close()
            finally:
                self._emit("lifecycle.closed", outcome="completed")
                if self._events and not self._telemetry_closed:
                    self._telemetry_closed = True
                    try:
                        self._events.close()
                    except Exception:
                        pass


_lock = threading.Lock()
_runtime = None


def get_tts_worker():
    global _runtime
    with _lock:
        if _runtime is None:
            from ..supervisor.worker_events import WorkerEvents
            _runtime = TTSRuntime(TTSWorkerClient(
                [sys.executable, "-m", "gamma.voice.tts_worker"], cwd=settings.project_root,
                artifact_root=settings.data_dir / "runtime/tts-worker-artifacts"),
                Admission(settings.data_dir / "runtime/model-admission.sqlite3"),
                receipt_root=settings.data_dir / "runtime/tts-job-receipts",
                events=WorkerEvents(settings.data_dir / "runtime", "tts"))
        return _runtime


def close_tts_worker():
    with _lock:
        value = _runtime
    if value is not None:
        value.close()
