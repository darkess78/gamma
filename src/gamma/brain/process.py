"""Generation-fenced brain process. Core retains state, tools and delivery."""
from contextlib import contextmanager
from dataclasses import asdict
import os
import queue
import subprocess
import sys
import threading
import time
from uuid import uuid4

from ..config import settings
from ..errors import ConversationError, ConfigurationError, ExternalServiceError, GammaError
from ..schemas.response import AssistantResponse
from .authority import BrainAuthority
from .queue import BrainQueueClosed
from .wire import encode, decode, MAX_FRAME


class BrainUnavailable(BrainQueueClosed, ConversationError):
    pass


class BrainProcess:
    def __init__(self, *, command=None, env=None, timeout=180):
        self.command = command or [sys.executable, "-m", "gamma.brain.worker"]
        self.env, self.timeout = env, timeout
        self._owner = os.getpid()
        self._lock = threading.RLock()
        self._execution = threading.Lock()
        self._process = None
        self._messages = None
        self._generation = None
        self._state = "stopped"
        self._failure = None
        self._active = False

    def _check_owner(self):
        if os.getpid() != self._owner:
            raise BrainUnavailable("brain client belongs to another process")

    def status(self):
        self._check_owner()
        with self._lock:
            if self._process is not None and self._process.poll() is not None and self._state != "stopped":
                self._state, self._failure = "failed", "worker_exited"
            return dict(state=self._state, ready=self._state in {"ready", "running"},
                pid=self._process.pid if self._process else None, generation=self._generation,
                active=self._active, failure=self._failure, protocol_version=1)

    @staticmethod
    def _reader(process, messages):
        try:
            while True:
                raw = process.stdout.readline(MAX_FRAME + 1)
                if not raw:
                    break
                messages.put_nowait(decode(raw))
        except Exception:
            pass
        finally:
            try:
                messages.put_nowait(None)
            except queue.Full:
                pass

    def _retire_locked(self):
        self._generation = None
        process = self._process
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            process.stdin.close()
            process.stdout.close()
        self._process = None

    def control(self, action):
        self._check_owner()
        with self._lock:
            self.status()
            if action == "stop":
                self._state = "stopped"
                self._retire_locked()
            elif action == "pause":
                if self._state not in {"ready", "running", "paused"}:
                    raise BrainUnavailable("brain is not running")
                self._state = "paused"
            elif action == "resume":
                if self._state != "paused":
                    raise BrainUnavailable("brain is not paused")
                self._state = "running" if self._active else "ready"
            elif action == "start":
                if self._state in {"ready", "running", "paused"}:
                    return self.status()
                if self._active:
                    raise BrainUnavailable("previous brain request is draining")
                self._retire_locked()
                generation = uuid4().hex
                self._generation, self._state, self._failure = generation, "starting", None
                env = dict(os.environ if self.env is None else self.env)
                env["GAMMA_BRAIN_GENERATION"] = generation
                try:
                    process = subprocess.Popen(self.command, cwd=settings.project_root, env=env,
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                        bufsize=0, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    self._process = process
                    messages = self._messages = queue.Queue(maxsize=4)
                    threading.Thread(target=self._reader, args=(process, messages), daemon=True).start()
                    ready = messages.get(timeout=min(self.timeout, 15))
                    if (not ready or ready["type"] != "ready" or ready["generation"] != generation
                            or ready.get("pid") != process.pid or ready["request_id"] is not None):
                        raise BrainUnavailable("brain readiness failed")
                    self._state = "ready"
                except Exception as exc:
                    self._retire_locked()
                    self._state, self._failure = "failed", "startup_failed"
                    raise BrainUnavailable("brain startup failed") from exc
            else:
                raise ValueError("unsupported brain action")
            return self.status()

    @contextmanager
    def guard(self, generation):
        # Stop and the short durable commit/authorization section are serialized.
        with self._lock:
            status = self.status()
            if generation != self._generation or status["state"] not in {"ready", "running", "paused"}:
                raise BrainUnavailable("obsolete brain result")
            yield

    def execute(self, operation, fields, authority):
        self._check_owner()
        if not self._execution.acquire(blocking=False):
            raise BrainUnavailable("brain is busy")
        generation = None
        reported_error = False
        try:
            with self._lock:
                if self.status()["state"] != "ready":
                    raise BrainUnavailable("brain is not ready")
                generation = self._generation
                process, messages = self._process, self._messages
                self._active, self._state = True, "running"
            identity = uuid4().hex
            base = dict(schema_version=1, generation=generation, request_id=identity)
            message = dict(base, type="execute", operation=operation, fields=fields)
            try:
                encode(message)
            except (ValueError, TypeError) as exc:
                reported_error = True
                raise ConversationError("Brain request exceeds protocol limits or has invalid fields.") from exc
            self._send(process, message)
            deadline = time.monotonic() + self.timeout
            callbacks = 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BrainUnavailable("brain deadline exceeded")
                reply = messages.get(timeout=remaining)
                with self.guard(generation):
                    if (not reply or reply["generation"] != generation or reply["request_id"] != identity):
                        raise BrainUnavailable("invalid brain response")
                if reply["type"] == "callback":
                    callbacks += 1
                    if callbacks > 64:
                        raise BrainUnavailable("brain callback limit exceeded")
                    # Authority implementations pin speaker/session to this request.
                    # No arbitrary method names, writes, paths or identities cross here.
                    with self.guard(generation):
                        pass
                    value = authority(reply.get("operation"), reply.get("args"))
                    with self.guard(generation):
                        self._send(process, dict(base, type="callback_result", value=value))
                elif reply["type"] == "result":
                    return reply["value"], generation
                elif reply["type"] == "error":
                    code = reply.get("error_code")
                    reported_error = code in {"vision_unsupported", "conversation", "configuration", "provider"}
                    if code == "vision_unsupported":
                        raise ConversationError("Configured LLM does not support image analysis.")
                    if code == "conversation":
                        raise ConversationError("Brain rejected the conversation request.")
                    if code == "configuration":
                        raise ConfigurationError("Brain provider configuration unavailable.")
                    if code == "provider":
                        raise ExternalServiceError("Brain provider request failed.")
                    raise BrainUnavailable("brain computation failed")
                else:
                    raise BrainUnavailable("invalid brain result type")
        except Exception as exc:
            if reported_error and isinstance(exc, GammaError) and not isinstance(exc, BrainUnavailable):
                raise
            if generation is not None:
                with self._lock:
                    if self._generation == generation:
                        self._retire_locked()
                        self._state, self._failure = "failed", type(exc).__name__
            if isinstance(exc, BrainUnavailable):
                raise
            raise BrainUnavailable("brain request failed") from exc
        finally:
            with self._lock:
                self._active = False
                if self._state == "running":
                    self._state = "ready"
            self._execution.release()

    def _send(self, process, message):
        raw = encode(message)
        done = threading.Event()
        errors = []
        def write():
            try:
                remaining = memoryview(raw)
                while remaining:
                    count = process.stdin.write(remaining)
                    if not count:
                        raise BrokenPipeError()
                    remaining = remaining[count:]
                process.stdin.flush()
            except Exception as exc:
                errors.append(exc)
            finally:
                done.set()
        threading.Thread(target=write, daemon=True).start()
        if not done.wait(min(self.timeout, 5)) or errors:
            raise BrainUnavailable("brain transport failed")

    def compute(self, service, operation, fields):
        from .image_payloads import staged_images
        fields = dict(fields)
        fields.pop("self", None)
        fields.pop("get_brain_process", None)
        authority = BrainAuthority(service, fields)
        fields["speaker"] = asdict(fields["speaker"]) if fields.get("speaker") is not None else None
        fields["state_proposals"] = {"memories": [], "core_facts": [], "session_id": authority.session} if authority.proposals is not None else None
        image = fields.pop("image", None)
        analysis = fields.get("vision_analysis")
        if analysis is not None:
            fields["vision_analysis"] = analysis.model_dump(mode="json")
        with staged_images([image.to_llm_input()] if image else []) as images:
            fields["images"] = images
            value, generation = self.execute(operation, fields, authority)
        if operation == "vision":
            from ..schemas.response import VisionAnalysis
            with self.guard(generation):
                return VisionAnalysis.model_validate(value)
        response = AssistantResponse.model_validate(value)
        # Worker cannot grant permission or supply an artifact.
        response.audio_path = response.audio_content_type = None
        response.tts_metadata.pop("delivery_grant", None)
        response.tts_metadata["brain_generation"] = generation
        response.tts_metadata["brain_pid"] = self.status()["pid"]
        return response


_singleton_lock = threading.Lock()
_runtime = None


def get_brain_process():
    global _runtime
    with _singleton_lock:
        if _runtime is None:
            _runtime = BrainProcess()
        return _runtime
