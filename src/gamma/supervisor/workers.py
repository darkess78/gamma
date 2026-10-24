"""Phase 1 inventory and controls for existing supervisor services."""
from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass
from uuid import uuid4

from .lifecycle_lock import lifecycle_lock
from .manager import ProcessManager
from .worker_events import WorkerEvents, read_worker_logs


@dataclass(frozen=True)
class WorkerManifest:
    worker_id: str
    module: str
    host: str
    controls: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    optional: bool = True
    readiness: str = "unavailable"
    startup_args: tuple[str, ...] = ()


MANIFESTS = (
    WorkerManifest("shana", "gamma.main:app", "Shana API", optional=False),
    WorkerManifest("dashboard", "gamma.dashboard.main:app", "Dashboard", optional=False),
    WorkerManifest("audio-understanding", "gamma.audio_understanding_server:app", "Audio sidecar"),
    WorkerManifest("improvement_worker", "gamma.improvement.worker", "Independent process",
                   ("start", "stop", "restart"), readiness="heartbeat", startup_args=("--idle-timeout-seconds", "300")),
    WorkerManifest("twitch_worker", "gamma.integrations.twitch.worker", "Independent process",
                   ("start", "stop", "restart"), dependencies=("shana",), readiness="heartbeat"),
    WorkerManifest("twitch_eventsub", "gamma.integrations.twitch.eventsub", "Independent process",
                   ("start", "stop", "restart"), dependencies=("shana",), readiness="heartbeat"),
    WorkerManifest("discord_text", "gamma.integrations.discord.worker", "Independent process"),
)


class WorkerRuntime:
    def __init__(self, manager: ProcessManager | None = None):
        self.manager = manager or ProcessManager()
        self.root = self.manager._runtime_dir

    def manifest(self, worker: str) -> WorkerManifest:
        for manifest in MANIFESTS:
            if manifest.worker_id == worker:
                return manifest
        raise ValueError("unknown worker")

    def _control_path(self, worker):
        return self.root / f"{worker}.control.json"

    @staticmethod
    def _read(path):
        try:
            if path.stat().st_size > 8192:
                return {}
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def status(self, worker: str) -> dict:
        manifest = self.manifest(worker)
        if worker in self.manager._services:
            process = self.manager.find_process(worker)
        else:
            process = self.manager.find_module_process(worker, manifest.module)
        payload = self.manager.process_payload(process)
        control = self._read(self._control_path(worker))
        if not isinstance(control.get("restart_count", 0), int):
            control["restart_count"] = 0
        heartbeat = self._read(self.root / f"{worker}.heartbeat.json")
        stamp = heartbeat.get("timestamp")
        if not isinstance(stamp, (int, float)) or not math.isfinite(stamp) or stamp > time.time() + 1:
            heartbeat = {}
        running = bool(payload.get("running"))
        age = None
        state = "stopped"
        instance = None
        if running:
            instance = f"{payload['pid']}:{payload.get('create_time')}"
            state = "degraded"  # PID liveness alone cannot establish readiness.
            if manifest.readiness == "heartbeat" and isinstance(control.get("updated_at"), (int, float)):
                if 0 <= time.time() - control["updated_at"] < 5:
                    state = "starting"
            if (manifest.readiness == "heartbeat" and heartbeat.get("pid") == payload["pid"]
                    and heartbeat.get("timestamp", 0) >= payload.get("create_time", 0)):
                age = max(0, time.time() - heartbeat.get("timestamp", 0))
                instance = heartbeat.get("instance_id")
                if age < 5:
                    state = heartbeat.get("state") if heartbeat.get("state") in {"starting", "ready", "busy", "failed"} else "degraded"
        elif control.get("desired_state") == "running":
            state = "stopped" if (heartbeat.get("state") == "stopped" and
                                  heartbeat.get("instance_id") == control.get("instance_id")) else "failed"
        return {**asdict(manifest), "desired_state": control.get("desired_state", "unmanaged"),
                "observed_state": state, "health": "ok" if state in {"ready", "busy"} else "unavailable",
                "pid": payload.get("pid"), "instance_id": instance, "heartbeat_age_seconds": age,
                "current_job": heartbeat.get("job_id") if age is not None and age < 5 else None,
                "queue_depth": None, "resource_allocation": {"rss_bytes": payload.get("rss_bytes")},
                "restart_count": control.get("restart_count", 0),
                "last_failure": control.get("last_failure") or ("process_exited" if state == "failed" else None),
                "dropped_events": heartbeat.get("dropped_events") if age is not None else None,
                "logging_status": ("degraded" if heartbeat.get("dropped_events", 0) else "ok") if age is not None else "unavailable",
                "unsupported_controls": [x for x in ("pause", "resume", "autostart", "drain", "cancel")
                                         if x not in manifest.controls],
                "restart_consequence": "Restarts this process only" if manifest.optional else "Use existing application controls",
                "automatic_restart": False}

    def inventory(self):
        return {"schema_version": 1, "workers": [self.status(m.worker_id) for m in MANIFESTS]}

    def control(self, worker: str, action: str) -> dict:
        manifest = self.manifest(worker)
        if action not in manifest.controls:
            raise ValueError("unsupported worker control")
        if worker == "twitch_worker" and action in {"start", "restart"}:
            from ..integrations.twitch.worker import TwitchWorkerConfig
            from ..errors import ConfigurationError
            try:
                TwitchWorkerConfig.from_settings()
            except ConfigurationError:
                return {"ok": False, "detail": "Twitch IRC configuration required", "auth_required": True}
        if worker == "twitch_eventsub" and action in {"start", "restart"}:
            from ..integrations.twitch.eventsub import TwitchEventSubConfig
            from ..errors import ConfigurationError
            try:
                TwitchEventSubConfig.from_settings()
            except ConfigurationError:
                return {"ok": False, "detail": "Twitch EventSub configuration required", "auth_required": True}
        trace = uuid4().hex
        with lifecycle_lock(self.root / "lifecycle.lock"):
            if action in {"stop", "restart"} and self.status(worker)["observed_state"] == "busy":
                return {"ok": False, "trace_id": trace, "detail": "Use Improvement work controls before stopping a busy worker.",
                        "worker": self.status(worker)}
            old = self._read(self._control_path(worker))
            events = WorkerEvents(self.root, worker)
            started = time.perf_counter()
            try:
                events.emit("lifecycle.requested", trace_id=trace, reason_code=action)
                if action == "stop":
                    result = self.manager.stop_module(worker, manifest.module)
                else:
                    stopped = None
                    if action == "restart":
                        stopped = self.manager.stop_module(worker, manifest.module)
                        if not stopped.get("ok"):
                            return {"ok": False, "trace_id": trace, "result": stopped}
                    try:
                        result = self.manager.start_module(worker, manifest.module,
                                                           list(manifest.startup_args), trace_id=trace)
                    except OSError:
                        result = {"ok": False, "detail": "launch_failed"}
                    if action == "restart":
                        result = {**result, "detail": "restarted", "stop": stopped, "start": dict(result)}
                state = {"desired_state": "stopped" if action == "stop" else "running",
                         "updated_at": time.time(),
                         "instance_id": result.get("instance_id") or old.get("instance_id"),
                         "restart_count": (old.get("restart_count", 0) if isinstance(old.get("restart_count", 0), int) else 0) + int(action == "restart"),
                         "last_failure": None if result.get("ok") else "lifecycle_failed"}
                temporary = self._control_path(worker).with_suffix(".tmp")
                temporary.write_text(json.dumps(state), encoding="utf-8")
                temporary.replace(self._control_path(worker))
                events.emit("lifecycle.completed", trace_id=trace,
                            outcome="ok" if result.get("ok") else "failed", reason_code=action,
                            duration_ms=round((time.perf_counter() - started) * 1000, 3))
                return {**result, "ok": bool(result.get("ok")), "trace_id": trace, "worker": self.status(worker)}
            finally:
                events.close()

    def logs(self, workers: str = "", **filters):
        selected = tuple(dict.fromkeys(workers.split(","))) if workers else tuple(m.worker_id for m in MANIFESTS)
        for worker in selected:
            self.manifest(worker)
        return read_worker_logs(self.root, selected, **filters)
