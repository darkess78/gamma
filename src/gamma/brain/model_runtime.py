"""Opt-in direct text inference process with durable capacity ownership."""
import json
import sys
import threading
from uuid import uuid4

from ..config import settings
from ..llm.base import LLMAdapter, LLMReply
from ..resources.admission import Admission
from ..voice.worker_client import PersistentWorkerClient
from .model_protocol import validate_request, validate_reply, validate_temperature
from .provider_routes import configured_route
from ..errors import ContextOverflowError, CapabilityMismatchError


class ModelRuntime(LLMAdapter):
    @property
    def supports_vision(self):
        return settings.llm_provider.strip().lower() in {"local", "ollama"} and settings.local_llm_supports_vision

    def __init__(self, client, admission, events=None):
        self.client, self.admission = client, admission
        self._events = events
        self._enabled = True
        self._telemetry_closed = False
        self._lock = threading.Lock()
        self._closed = threading.Event()

    def _emit(self, name, trace_id=None, **fields):
        if self._events is not None:
            try:
                self._events.emit(name, trace_id=trace_id, **fields)
            except Exception:
                pass

    def control(self, action):
        if action not in {"start", "stop", "restart"}:
            raise ValueError("unsupported model action")
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("model worker busy")
        trace = uuid4().hex
        try:
            if self._closed.is_set():
                raise RuntimeError("model worker closed")
            if action != "stop" and self.admission.status("brain-inference") is not None:
                raise RuntimeError("model capacity remains occupied")
            self._enabled = False
            self._emit(f"lifecycle.{action}", trace, state="starting")
            if action in {"stop", "restart"}:
                self.client.close()
            if action in {"start", "restart"}:
                self.client.start()
                self._enabled = True
            self._emit(f"lifecycle.{action}", trace, outcome="completed")
            return {**self.status(), "trace_id": trace}
        except Exception:
            self._emit(f"lifecycle.{action}", trace, outcome="failed", reason_code="control_failed")
            raise
        finally:
            self._lock.release()

    def generate_reply(self, system_prompt, user_text, image_inputs=None, *,
                       call_context=None, model_override=None, endpoint_override=None):
        return self.generate_provider_reply(settings.llm_provider.strip().lower(), system_prompt, user_text,
            image_inputs, call_context=call_context, model_override=model_override, endpoint_override=endpoint_override)

    def generate_provider_reply(self, provider, system_prompt, user_text, image_inputs=None, *,
                               call_context=None, model_override=None, endpoint_override=None):
        # Direct adapters ignore call_context. Routed calls arrive after the core
        # router has run its prompt builder and selected the concrete provider.
        provider, endpoint_override = configured_route(provider, endpoint_override)
        if image_inputs and (provider != "local" or not settings.local_llm_supports_vision):
            raise ValueError("unsupported model worker images")
        fields = validate_request(dict(system_prompt=system_prompt, user_text=user_text, model_override=model_override))
        from ..resources.shared_execution import priority_for
        fields.update(provider=provider, endpoint_override=endpoint_override, admission_priority=priority_for(call_context))
        if getattr(call_context, "interaction_mode", None) == "silent":
            fields["silent"] = True
        return self._execute("generate", fields, image_inputs=image_inputs)

    def review_text(self, system_prompt, user_text, *, model, temperature):
        provider, endpoint = configured_route("local", settings.local_llm_endpoint)
        fields = validate_request(dict(system_prompt=system_prompt, user_text=user_text, model_override=model))
        if not fields["model_override"]:
            raise ValueError("review model required")
        from ..resources.shared_execution import priority_for
        fields.update(provider=provider, endpoint_override=endpoint, temperature=validate_temperature(temperature), admission_priority=priority_for())
        return self._execute("review", fields, wait=False).text

    def _execute(self, kind, fields, *, wait=True, image_inputs=None):
        # Account for JSON escaping before acquiring capacity or spawning a child.
        frame = json.dumps(dict(schema_version=1, request_id="0" * 32, type=kind, **fields)) + "\n"
        if len(frame.encode()) > 65536:
            raise ValueError("model request frame exceeds limit")
        if not self._lock.acquire(blocking=wait):
            raise RuntimeError("model worker busy")
        try:
            if self._closed.is_set():
                raise RuntimeError("model worker closed")
            if not self._enabled:
                raise RuntimeError("model worker disabled")
            token = self.admission.acquire("brain-inference")
            trace = uuid4().hex
            self._emit("job.started", trace)
            submitted = False
            try:
                from .image_payloads import staged_images
                with staged_images([] if image_inputs is None else image_inputs) as images:
                    if images:
                        fields = {**fields, "images": images}
                    frame = json.dumps(dict(schema_version=1, request_id="0" * 32, type=kind, **fields)) + "\n"
                    if len(frame.encode()) > 65536:
                        raise ValueError("model request frame exceeds limit")
                    submitted = True
                    result = self.client.request(kind, **fields)
                overflow = result.get("error_code") == "context_overflow" and "text" not in result
                mismatch = result.get("error_code") == "capability_mismatch" and "text" not in result
                if result.get("error_code") is not None and not (overflow or mismatch):
                    raise RuntimeError("invalid model worker result")
                reply = None if overflow or mismatch else LLMReply(validate_reply(result.get("text")))
            except BaseException as exc:
                # Killing our client cannot prove remote Ollama stopped computing.
                # Retain capacity until provider completion can be established.
                from ..resources.shared_execution import is_busy
                rejected = is_busy(exc)
                if submitted and not rejected:
                    self.admission.uncertain("brain-inference", token)
                else:
                    self.admission.release("brain-inference", token)
                self._emit("job.failed", trace, outcome="failed", reason_code="inference_failed")
                if submitted and not rejected:
                    # Preserve the transport exception contract while prohibiting
                    # routed fallback after a possibly submitted request.
                    exc.admission_uncertain = True
                raise
            self.admission.release("brain-inference", token)
            if mismatch:
                self._emit("job.rejected", trace, outcome="failed", reason_code="capability_mismatch")
                raise CapabilityMismatchError("Model worker lacks the required capability")
            if overflow:
                self._emit("job.rejected", trace, outcome="failed", reason_code="context_overflow")
                raise ContextOverflowError("Model worker rejected the context budget")
            self._emit("job.completed", trace, outcome="completed")
            return reply
        finally:
            self._lock.release()

    def close(self):
        self._closed.set()
        with self._lock:
            self._enabled = False
            try:
                self.client.close()
            finally:
                if not self._telemetry_closed:
                    self._emit("lifecycle.closed", outcome="completed")
                    self._telemetry_closed = True
                    if self._events is not None:
                        try:
                            self._events.close()
                        except Exception:
                            pass

    def status(self):
        lease = self.admission.status("brain-inference")
        return {**self.client.status(), "closed": self._closed.is_set(),
                "enabled": self._enabled, "admission_state": lease["state"] if lease else None}


_lock = threading.Lock()
_runtime = None


def get_model_worker():
    global _runtime
    if not settings.llm_router_enabled and settings.llm_provider.strip().lower() not in {"mock", "local", "ollama"}:
        raise ValueError("unsupported model worker configuration")
    with _lock:
        if _runtime is None:
            from ..supervisor.worker_events import WorkerEvents
            _runtime = ModelRuntime(PersistentWorkerClient(
                [sys.executable, "-m", "gamma.brain.model_worker"], cwd=settings.project_root),
                Admission(settings.data_dir / "runtime/model-admission.sqlite3"),
                WorkerEvents(settings.data_dir / "runtime", "brain-model"))
        return _runtime


def close_model_worker():
    with _lock:
        value = _runtime
    if value is not None:
        value.close()


class RoutedModelAdapter(LLMAdapter):
    @property
    def supports_vision(self):
        return self.provider == "local" and settings.local_llm_supports_vision

    def __init__(self, provider):
        self.provider, _ = configured_route(provider, None)

    def generate_reply(self, system_prompt, user_text, image_inputs=None, **kwargs):
        return get_model_worker().generate_provider_reply(self.provider, system_prompt, user_text, image_inputs, **kwargs)
