"""Owned persistent inference subprocess transport."""
import contextlib
import json
import math
import os
import queue
import subprocess
import threading
from uuid import uuid4
from ..system.cuda_env import prepend_cuda_library_path


class PersistentWorkerClient:
    def __init__(self, command, *, cwd, env=None, timeout=120):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("invalid worker timeout")
        self.command, self.cwd, self.env, self.timeout = list(command), cwd, env, timeout
        self._owner = os.getpid()
        self._lock = threading.Lock()
        self._busy = threading.Event()
        self._process = None
        self._messages = None

    def _check_owner(self):
        if os.getpid() != self._owner:
            raise RuntimeError("inference client belongs to another process")

    @contextlib.contextmanager
    def _operation(self):
        self._check_owner()
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("inference worker busy")
        self._busy.set()
        try:
            yield
        finally:
            self._busy.clear()
            self._lock.release()

    def status(self):
        self._check_owner()
        process = self._process
        return {"pid": process.pid if process else None,
                "running": process is not None and process.poll() is None, "busy": self._busy.is_set()}

    @staticmethod
    def _read(process, messages):
        try:
            while True:
                raw = process.stdout.readline(65537)
                if not raw or len(raw) > 65536 or not raw.endswith(b"\n"):
                    raise ValueError("invalid frame")
                messages.put_nowait(json.loads(raw))
        except Exception:
            try:
                messages.put_nowait(None)
            except queue.Full:
                pass

    def _receive(self, expected, identity=None):
        message = self._messages.get(timeout=self.timeout)
        if (not isinstance(message, dict) or type(message.get("schema_version")) is not int
                or message["schema_version"] != 1 or message.get("pid") != self._process.pid
                or message.get("request_id") != identity or message.get("type") not in {expected, "error"}):
            raise RuntimeError("invalid inference response")
        if message["type"] == "error":
            if message.get("reason") == "admission_busy":
                from ..resources.admission import AdmissionBusy
                raise AdmissionBusy("shared provider capacity unavailable")
            raise RuntimeError("inference request failed")
        return message

    def _send(self, kind, **fields):
        identity = uuid4().hex
        data = (json.dumps({"schema_version": 1, "request_id": identity, "type": kind, **fields}) + "\n").encode()
        if len(data) > 65536:
            raise ValueError("inference request exceeds limit")
        process = self._process
        done, errors = threading.Event(), []
        def write():
            try:
                remaining = memoryview(data)
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
        if not done.wait(self.timeout):
            raise TimeoutError("inference send timed out")
        if errors:
            raise RuntimeError("inference send failed") from errors[0]
        return identity

    def _terminate(self):
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        process.stdin.close()
        process.stdout.close()
        self._process = None
        self._messages = None

    def _start(self):
        if self._process is not None:
            if self._process.poll() is None:
                return
            self._terminate()
        runtime_env = prepend_cuda_library_path(dict(os.environ if self.env is None else self.env))
        self._process = subprocess.Popen(self.command, cwd=self.cwd, env=runtime_env, shell=False,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self._messages = queue.Queue(maxsize=2)
        threading.Thread(target=self._read, args=(self._process, self._messages), daemon=True).start()
        ready = self._receive("ready")
        if ready.get("model_loaded") is not False:
            raise RuntimeError("invalid inference readiness")

    def start(self):
        with self._operation():
            try:
                self._start()
            except Exception:
                self._terminate()
                raise
        return self.status()

    def request(self, kind, **fields):
        with self._operation():
            try:
                self._start()
                return self._receive("result", self._send(kind, **fields))
            except Exception:
                self._terminate()
                raise

    def close(self):
        with self._operation():
            try:
                if self._process is not None and self._process.poll() is None:
                    self._receive("stopped", self._send("shutdown"))
                    self._process.wait(timeout=3)
            finally:
                self._terminate()
