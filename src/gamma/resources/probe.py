from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import psutil


@dataclass(slots=True)
class GpuProcessSnapshot:
    gpu_uuid: str
    pid: int
    used_memory_mb: int

    def as_payload(self) -> dict[str, Any]:
        return {
            "gpu_uuid": self.gpu_uuid,
            "pid": self.pid,
            "used_memory_mb": self.used_memory_mb,
        }


@dataclass(slots=True)
class GpuSnapshot:
    index: int
    uuid: str
    name: str
    memory_total_mb: int
    memory_used_mb: int
    memory_free_mb: int
    utilization_percent: int
    temperature_c: int
    processes: list[GpuProcessSnapshot] = field(default_factory=list)

    def as_payload(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "uuid": self.uuid,
            "label": f"GPU {self.index}",
            "name": self.name,
            "memory_total_mb": self.memory_total_mb,
            "memory_used_mb": self.memory_used_mb,
            "memory_free_mb": self.memory_free_mb,
            "utilization_percent": self.utilization_percent,
            "temperature_c": self.temperature_c,
            "processes": [process.as_payload() for process in self.processes],
        }


@dataclass(slots=True)
class ResourceSnapshot:
    cpu_percent: float
    memory: dict[str, Any]
    disk: dict[str, Any]
    gpu: dict[str, Any]
    sampled_at: str

    def as_dashboard_payload(self, *, gpu_enabled: bool, refresh_interval_seconds: int) -> dict[str, Any]:
        return {
            "cpu_percent": self.cpu_percent,
            "memory": self.memory,
            "disk": self.disk,
            "gpu": self.gpu,
            "sampled_at": self.sampled_at,
            "gpu_enabled": gpu_enabled,
            "refresh_interval_seconds": refresh_interval_seconds,
        }


class MachineResourceMonitor:
    def __init__(
        self,
        *,
        project_root: Path,
        enable_gpu: Callable[[], bool],
        refresh_interval_seconds: Callable[[], int],
    ) -> None:
        self.project_root = project_root
        self._enable_gpu = enable_gpu
        self._refresh_interval_seconds = refresh_interval_seconds
        self._lock = threading.Lock()
        self._cached_snapshot: ResourceSnapshot | None = None

    def dashboard_payload(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        return snapshot.as_dashboard_payload(
            gpu_enabled=self._enable_gpu(),
            refresh_interval_seconds=self._refresh_interval_seconds(),
        )

    def snapshot(self) -> ResourceSnapshot:
        now = time.time()
        with self._lock:
            snapshot = self._cached_snapshot
            if snapshot is not None:
                sampled_at = datetime.fromisoformat(snapshot.sampled_at.replace("Z", "+00:00")).timestamp()
                if now - sampled_at < self._refresh_interval_seconds():
                    return snapshot

        snapshot = collect_resource_snapshot(
            project_root=self.project_root,
            include_gpu=self._enable_gpu(),
        )
        with self._lock:
            self._cached_snapshot = snapshot
        return snapshot


def collect_resource_snapshot(*, project_root: Path, include_gpu: bool = True) -> ResourceSnapshot:
    vm = psutil.virtual_memory()
    disk = shutil.disk_usage(project_root)
    return ResourceSnapshot(
        cpu_percent=psutil.cpu_percent(interval=0.1),
        memory={
            "total_bytes": vm.total,
            "available_bytes": vm.available,
            "used_bytes": vm.used,
            "percent": vm.percent,
        },
        disk={
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes": disk.free,
            "percent": round((disk.used / disk.total) * 100, 2) if disk.total else 0,
        },
        gpu=gpu_status() if include_gpu else {"ok": False, "detail": "disabled"},
        sampled_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )


def gpu_status() -> dict[str, Any]:
    try:
        gpu_completed = _run_nvidia_smi(
            [
                "nvidia-smi",
                "--query-gpu=index,uuid,name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu",
                "--format=csv,noheader,nounits",
            ]
        )
    except FileNotFoundError:
        return {"ok": False, "detail": "nvidia-smi-not-found"}
    except subprocess.CalledProcessError as exc:
        return {"ok": False, "detail": exc.stderr.strip() or exc.stdout.strip() or "nvidia-smi-failed"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "detail": "nvidia-smi-timeout"}

    processes_by_uuid = _gpu_processes_by_uuid()
    gpus: list[dict[str, Any]] = []
    for line_number, line in enumerate(gpu_completed.stdout.splitlines()):
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 8:
            continue
        index = _int_or_default(parts[0], line_number)
        uuid = parts[1]
        snapshot = GpuSnapshot(
            index=index,
            uuid=uuid,
            name=parts[2],
            memory_total_mb=_int_or_default(parts[3]),
            memory_used_mb=_int_or_default(parts[4]),
            memory_free_mb=_int_or_default(parts[5]),
            utilization_percent=_int_or_default(parts[6]),
            temperature_c=_int_or_default(parts[7]),
            processes=processes_by_uuid.get(uuid, []),
        )
        gpus.append(snapshot.as_payload())
    return {"ok": True, "gpus": gpus}


def _gpu_processes_by_uuid() -> dict[str, list[GpuProcessSnapshot]]:
    try:
        completed = _run_nvidia_smi(
            [
                "nvidia-smi",
                "--query-compute-apps=gpu_uuid,pid,used_memory",
                "--format=csv,noheader,nounits",
            ]
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return {}

    processes: dict[str, list[GpuProcessSnapshot]] = {}
    for line in completed.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 3:
            continue
        gpu_uuid = parts[0]
        process = GpuProcessSnapshot(
            gpu_uuid=gpu_uuid,
            pid=_int_or_default(parts[1]),
            used_memory_mb=_int_or_default(parts[2]),
        )
        processes.setdefault(gpu_uuid, []).append(process)
    return processes


def _run_nvidia_smi(command: list[str]) -> subprocess.CompletedProcess[str]:
    run_kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "timeout": 5,
        "check": True,
    }
    if os.name == "nt":
        run_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(command, **run_kwargs)


def _int_or_default(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
