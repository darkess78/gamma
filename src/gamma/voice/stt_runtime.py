"""Runtime wrapper around STTWorkerClient with safe shutdown and operator control."""
import threading
import uuid

from .stt_client import STTWorkerClient
from ..resources.admission import AdmissionBusy
from ..brain.queue import BrainQueueFull


class STTRuntime:
    """Thin serialisation, shutdown guard, and operator control over STTWorkerClient."""

    def __init__(self, client: STTWorkerClient, events=None, admission=None):
        self._client = client
        self._events = events
        self._admission = admission
        self._resources = ("stt", "voice-inference")
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._enabled = True

    def _emit(self, name, trace_id, **kw):
        if self._events is None:
            return
        try:
            self._events.emit(name, trace_id=trace_id, **kw)
        except Exception:
            pass

    def control(self, action):
        if self._closed.is_set():
            raise RuntimeError("STT runtime is closed")
        if action not in ("start", "stop", "restart"):
            raise ValueError("unsupported action")
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("STT worker busy")
        tid = uuid.uuid4().hex
        try:
            if self._closed.is_set():
                raise RuntimeError("STT runtime is closed")
            if action in {"start", "restart"} and self._admission is not None and self._admission.status("stt") is not None:
                raise RuntimeError("STT admission remains occupied")
            self._enabled = False
            self._emit(f"lifecycle.{action}", tid, state="starting")
            if action == "start":
                self._client.start()
                self._enabled = True
            elif action == "stop":
                self._enabled = False
                self._client.close()
            else:
                self._enabled = False
                self._client.close()
                self._client.start()
                self._enabled = True
            self._emit(f"lifecycle.{action}", tid, outcome="completed")
            return {**self.status(), "trace_id": tid}
        except Exception:
            self._emit(f"lifecycle.{action}", tid, outcome="failed", reason_code="operation_failed")
            raise
        finally:
            self._lock.release()

    def transcribe_audio(self, path):
        with self._lock:
            if self._closed.is_set():
                raise RuntimeError("STT runtime is closed")
            if not self._enabled:
                raise RuntimeError("STT runtime is disabled")
            token = None
            if self._admission is not None:
                try:
                    token = self._admission.acquire_many(self._resources)
                except AdmissionBusy as exc:
                    raise BrainQueueFull("shared voice inference is occupied") from exc
            tid = uuid.uuid4().hex
            self._emit("job.started", tid)
            try:
                result = self._client.transcribe_audio(path)
            except Exception as exc:
                if token:
                    if self._client.status().get("running") is False:
                        # This runtime accepts local-only recognizers. Reaped inference has exited.
                        self._admission.release_many(self._resources, token)
                    else:
                        self._admission.uncertain_many(self._resources, token)
                if isinstance(exc, AdmissionBusy):
                    self._emit("job.failed", tid, outcome="failed", reason_code="admission_busy")
                    raise BrainQueueFull("shared voice inference is occupied") from exc
                self._emit("job.failed", tid, outcome="failed", reason_code="operation_failed")
                raise
            if token:
                self._admission.release_many(self._resources, token)
            self._emit("job.completed", tid, outcome="completed")
            return result

    def close(self):
        self._closed.set()
        self._enabled = False
        try:
            with self._lock:
                self._client.close()
        finally:
            if self._events is not None:
                try:
                    self._events.close()
                except Exception:
                    pass

    def status(self):
        info = dict(self._client.status())
        info["inference_runtime"] = info.pop("runtime", None)
        info["closed"] = self._closed.is_set()
        info["enabled"] = self._enabled
        if self._admission is not None:
            shared = self._admission.status("voice-inference")
            info["shared_admission_state"] = shared["state"] if shared else None
        return info
