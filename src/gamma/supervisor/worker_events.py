"""Bounded, content-free worker telemetry over the existing JSON log format."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from uuid import uuid4
import psutil

from ..observability import JsonLogFormatter, bind_context, log_event, reset_context
from .lifecycle_lock import lifecycle_lock

FIELDS = frozenset({"timestamp", "level", "service", "event", "request_id", "trace_id",
                    "turn_id", "job_id", "worker_id", "instance_id", "event_id",
                    "event_type", "severity", "schema_version", "duration_ms", "outcome",
                    "reason_code", "dropped_events", "state", "pid"})


class LossCountingHandler(RotatingFileHandler):
    dropped_events = 0

    def handleError(self, record):
        # Do not print unsanitized records/exceptions to stderr on an I/O failure.
        self.dropped_events += 1


class UnavailableHandler(logging.Handler):
    dropped_events = 0

    def emit(self, record):
        self.dropped_events += 1


class WorkerEvents:
    def __init__(self, root: Path, worker: str, instance: str | None = None):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", worker):
            raise ValueError("invalid worker identity")
        self.worker = worker
        self.instance = instance if instance and re.fullmatch(r"[a-f0-9]{32}", instance) else uuid4().hex
        directory = root / "worker-logs" / worker
        # Every producer rotates only its own file.
        self.path = directory / f"{os.getpid()}-{self.instance}.jsonl"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            self.handler = LossCountingHandler(self.path, maxBytes=65536, backupCount=2, encoding="utf-8")
        except OSError:
            self.handler = UnavailableHandler()
        self.handler.setFormatter(JsonLogFormatter(worker))
        self.logger = logging.Logger(f"worker.{worker}.{self.instance}", logging.INFO)
        self.logger.addHandler(self.handler)
        self.root = root
        try:
            self._prune()
        except (OSError, TimeoutError):
            # Telemetry/retention failures cannot halt independent work.
            self.handler.dropped_events += 1

    def _prune(self):
        # Keep eight terminated/closed producer generations and all live producers.
        # Active workers never share or lose a rotating file.
        with lifecycle_lock(self.root / "worker-log-retention.lock", timeout=1):
            inactive = []
            for path in self.path.parent.glob("*.jsonl"):
                if path == self.path:
                    continue
                try:
                    pid = int(path.name.split("-", 1)[0])
                    if path.with_suffix(".closed").exists() or not psutil.pid_exists(pid):
                        inactive.append(path)
                except (ValueError, OSError):
                    continue
            inactive.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            for path in inactive[8:]:
                for item in (path, Path(str(path) + ".1"), Path(str(path) + ".2"), path.with_suffix(".closed")):
                    item.unlink(missing_ok=True)

    def emit(self, event: str, *, trace_id: str | None = None, **fields):
        trace_id = re.sub(r"[^A-Za-z0-9._:-]", "_", str(trace_id))[:128] if trace_id else None
        token = bind_context(trace_id=trace_id) if trace_id else None
        try:
            allowed = {key: value for key, value in fields.items()
                       if key in {"job_id", "outcome", "reason_code", "state"} and isinstance(value, str)
                       and re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value)}
            if isinstance(fields.get("duration_ms"), (float, int)):
                allowed["duration_ms"] = fields["duration_ms"]
            level = logging.ERROR if fields.get("outcome") == "failed" else logging.INFO
            log_event(self.logger, level, event, "Worker event.",
                      schema_version=1, worker_id=self.worker, instance_id=self.instance,
                      event_id=uuid4().hex, event_type=event.split(".")[0], severity=logging.getLevelName(level).lower(),
                      pid=os.getpid(), dropped_events=self.handler.dropped_events, **allowed)
        finally:
            if token is not None:
                reset_context(token)

    def close(self):
        self.handler.close()
        try:
            if self.path.exists():
                self.path.with_suffix(".closed").touch()
        except OSError:
            self.handler.dropped_events += 1


def read_worker_logs(root: Path, workers: tuple[str, ...], *, event_type: str = "",
                     severity: str = "", trace_id: str = "", before: str = "",
                     since: str = "", limit: int = 100) -> dict:
    """Newest first, bounded tail window. Cursor is a timestamp and event ID, never a path."""
    limit = max(1, min(limit, 200))
    budget = 512 * 1024
    rows, seen = [], set()
    skipped = 0
    truncated = False
    bytes_read = 0
    files_read = 0
    losses = {}
    for worker in workers:
        directory = root / "worker-logs" / worker
        # Registered directories only. Limit directory enumeration as well as reads.
        paths = []
        if directory.exists():
            with os.scandir(directory) as entries:
                for index, entry in enumerate(entries):
                    if index >= 256:
                        truncated = True
                        break
                    try:
                        if entry.is_file(follow_symlinks=False) and ".jsonl" in entry.name:
                            paths.append((entry.stat().st_mtime, Path(entry.path)))
                    except OSError:
                        skipped += 1
        paths.sort(key=lambda item: item[0], reverse=True)
        truncated |= len(paths) > 24
        for _, path in paths[:24]:
            if budget <= 0:
                truncated = True
                break
            try:
                with path.open("rb") as stream:
                    size = stream.seek(0, 2)
                    count = min(size, 32768, budget)
                    stream.seek(size - count)
                    data = stream.read(count)
                files_read += 1
                budget -= len(data)
                bytes_read += len(data)
                truncated |= size > count
                if size > count:
                    data = data.partition(b"\n")[2]
                lines = data.split(b"\n")[:-1]  # A partial trailing record is not an event.
                for line in lines:
                    try:
                        item = json.loads(line)
                        if not isinstance(item, dict) or item.get("worker_id") != worker:
                            skipped += 1
                            continue
                        identity = str(item["instance_id"]), str(item["event_id"])
                        if identity in seen:
                            continue
                        seen.add(identity)
                        losses[identity[0]] = max(losses.get(identity[0], 0), int(item.get("dropped_events", 0)))
                        cursor = str(item["timestamp"]) + "|" + identity[1]
                        if before and cursor >= before or since and str(item["timestamp"]) < since:
                            continue
                        if event_type and item.get("event_type") != event_type:
                            continue
                        if severity and item.get("severity") != severity:
                            continue
                        if trace_id and item.get("trace_id") != trace_id:
                            continue
                        # Never expose legacy messages, stderr, prompts or provider exception text.
                        rows.append({k: v for k, v in item.items() if k in FIELDS})
                    except (ValueError, KeyError, TypeError):
                        skipped += 1
            except OSError:
                skipped += 1
    rows.sort(key=lambda r: (str(r["timestamp"]), str(r["event_id"])), reverse=True)
    page = rows[:limit]
    return {"events": page, "next_cursor": (str(page[-1]["timestamp"]) + "|" + str(page[-1]["event_id"]))
            if len(rows) > limit else None, "window_truncated": truncated,
            "bytes_read": bytes_read, "files_read": files_read, "skipped_records": skipped,
            "reported_dropped_events": sum(losses.values()), "loss_scope": "retained window"}


class WorkerHeartbeat:
    """The worker reports readiness only after initialization, independently of work."""
    def __init__(self, root: Path, worker: str, *, initial_state="ready"):
        if initial_state not in {"ready", "starting"}:
            raise ValueError("invalid initial worker state")
        self.root = root
        self.worker = worker
        self.events = WorkerEvents(root, worker, os.environ.get("GAMMA_WORKER_INSTANCE"))
        self.trace_id = os.environ.get("GAMMA_WORKER_TRACE") or uuid4().hex
        self.state = initial_state
        self.job_id = None
        self.done = threading.Event()
        self.path = root / f"{worker}.heartbeat.json"
        self.thread = threading.Thread(target=self._loop, name="worker-heartbeat", daemon=True)

    def __enter__(self):
        self.events.emit(f"lifecycle.{self.state}", trace_id=self.trace_id, state=self.state)
        self._try_write()
        self.thread.start()
        return self

    def _write(self):
        payload = {"instance_id": self.events.instance, "pid": os.getpid(),
                   "timestamp": time.time(), "state": self.state, "job_id": self.job_id,
                   "dropped_events": self.events.handler.dropped_events}
        temporary = self.path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        temporary.replace(self.path)

    def _loop(self):
        while not self.done.wait(1):
            self._try_write()

    def _try_write(self):
        try:
            self._write()
        except OSError:
            self.events.handler.dropped_events += 1

    def __exit__(self, *exc):
        self.done.set()
        self.thread.join(timeout=2)
        self.state = "failed" if exc[0] or self.state == "failed" else "stopped"
        self._try_write()
        self.events.emit("lifecycle.exited", trace_id=self.trace_id, state=self.state)
        self.events.close()
