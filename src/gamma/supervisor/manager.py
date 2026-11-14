from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil

from ..config import settings
from ..observability import configure_logging
from ..system.cuda_env import prepend_cuda_library_path
from ..system.python_runtime import resolve_python_executable
from .sidecars import SidecarsMixin


@dataclass(frozen=True, slots=True)
class ManagedService:
    """Managed service.
    
    Attributes:
        name: Service name.
        module: Service module.
        bind_host: Bind host.
        public_host: Public host.
        port: Port.
    
    Methods:
        url: Get service URL.
    """
    name: str
    module: str
    bind_host: str
    public_host: str
    port: int

    @property
    def url(self) -> str:
        """Get service URL.
        
        Returns:
            str: Service URL.
        """
        return f"http://{self.public_host}:{self.port}"


class ProcessManager(SidecarsMixin):
    """Process manager.
    
    Attributes:
        _runtime_dir: Runtime directory.
        _services: Services dictionary.
    
    Methods:
        __init__: Initialize manager.
        service: Get service.
        start: Start service.
        stop: Stop service.
        restart: Restart service.
        status: Get service status.
        start_module: Start module.
        stop_module: Stop module.
        module_status: Get module status.
        find_module_process: Find module process.
        find_module_processes: Find module processes.
        looks_like_module_process: Check if process is module.
        find_process: Find process.
        find_processes: Find processes.
        looks_like_service: Check if process is service.
        process_payload: Get process payload.
        pid_file: Get PID file.
        clear_pid_file: Clear PID file.
        stdout_log: Get stdout log.
        stderr_log: Get stderr log.
        _resolve_background_python: Resolve Python executable.
        _start_shana_dependencies: Start Shana dependencies.
        _stop_shana_dependencies: Stop Shana dependencies.
    """

    def __init__(self) -> None:
        """Initialize manager.
        
        Sets up services for background process management.
        """
        self._runtime_dir = settings.data_dir / "runtime"
        self._runtime_dir.mkdir(parents=True, exist_ok=True)
        self._logger = configure_logging("supervisor")
        self._services = {
            "shana": ManagedService(
                "shana",
                "gamma.main:app",
                settings.shana_bind_host,
                settings.shana_public_host,
                settings.shana_port,
            ),
            "dashboard": ManagedService(
                "dashboard",
                "gamma.dashboard.main:app",
                settings.dashboard_bind_host,
                settings.dashboard_public_host,
                settings.dashboard_port,
            ),
            "audio-understanding": ManagedService(
                "audio-understanding",
                "gamma.audio_understanding_server:app",
                settings.audio_understanding_bind_host,
                settings.audio_understanding_bind_host,
                settings.audio_understanding_port,
            ),
        }

    def service(self, name: str) -> ManagedService:
        """Get service.
        
        Args:
            name: Service name.
        
        Returns:
            ManagedService: Service instance.
        """
        return self._services[name]

    def start(self, name: str) -> dict[str, Any]:
        """Start service.
        
        Args:
            name: Service name.
        
        Returns:
            dict[str, Any]: Start result.
        """
        service = self.service(name)
        existing = self.find_process(name)
        if existing:
            payload = {"ok": True, "detail": "already-running", "process": self.process_payload(existing), "url": service.url}
            if name == "shana":
                payload["dependencies"] = self._start_shana_dependencies()
            return payload

        dependencies: list[dict[str, Any]] = []
        if name == "shana":
            dependencies = self._start_shana_dependencies()

        python_executable = self._service_python(name)
        stdout_log = self.stdout_log(name)
        stderr_log = self.stderr_log(name)
        self._preserve_service_logs(name)
        command = [
            python_executable,
            "-m",
            "uvicorn",
            service.module,
            "--host",
            service.bind_host,
            "--port",
            str(service.port),
            "--no-access-log",
        ]
        runtime_env = prepend_cuda_library_path(os.environ.copy())
        if name == "audio-understanding":
            runtime_env.update(self._audio_understanding_admission_env())

        with stdout_log.open("ab") as stdout_handle, stderr_log.open("ab") as stderr_handle:
            process = subprocess.Popen(
                command,
                cwd=settings.project_root,
                stdout=stdout_handle,
                stderr=stderr_handle,
                env=runtime_env,
                start_new_session=True,
            )

        self.pid_file(name).write_text(str(process.pid), encoding="utf-8")
        time.sleep(2)
        found = self.find_process(name)
        return {
            "ok": True,
            "detail": "started",
            "url": service.url,
            "process": self.process_payload(found),
            "dependencies": dependencies if name == "shana" else [],
        }

    def stop(self, name: str) -> dict[str, Any]:
        """Stop service.
        
        Args:
            name: Service name.
        
        Returns:
            dict[str, Any]: Stop result.
        """
        processes = self.find_processes(name)
        if not processes:
            self.clear_pid_file(name)
            payload = {"ok": True, "detail": "not-running", "url": self.service(name).url}
            if name == "shana":
                payload["dependencies"] = self._stop_shana_dependencies()
            return payload

        stopped_pids: list[int] = []
        for process in processes:
            try:
                process.terminate()
                process.wait(timeout=10)
            except psutil.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            except psutil.Error:
                continue
            stopped_pids.append(process.pid)

        self.clear_pid_file(name)
        payload = {"ok": True, "detail": "stopped", "url": self.service(name).url, "pids": stopped_pids}
        if name == "shana":
            payload["dependencies"] = self._stop_shana_dependencies()
        return payload

    def restart(self, name: str) -> dict[str, Any]:
        """Restart service.
        
        Args:
            name: Service name.
        
        Returns:
            dict[str, Any]: Restart result.
        """
        stopped = self.stop(name)
        started = self.start(name)
        return {"ok": True, "detail": "restarted", "stop": stopped, "start": started}

    def status(self, name: str) -> dict[str, Any]:
        """Get service status.
        
        Args:
            name: Service name.
        
        Returns:
            dict[str, Any]: Status dict.
        """
        process = self.find_process(name)
        return {
            "service": name,
            "url": self.service(name).url,
            "process": self.process_payload(process),
            "stdout_path": str(self.stdout_log(name)),
            "stderr_path": str(self.stderr_log(name)),
        }

    def start_module(self, name: str, module: str, args: list[str] | None = None) -> dict[str, Any]:
        """Start module.
        
        Args:
            name: Service name.
            module: Python module.
            args: Module args.
        
        Returns:
            dict[str, Any]: Start result.
        """
        existing = self.find_module_process(name, module)
        if existing:
            return {"ok": True, "detail": "already-running", "process": self.process_payload(existing)}

        python_executable = self._resolve_background_python()
        stdout_log = self.stdout_log(name)
        stderr_log = self.stderr_log(name)
        self._preserve_service_logs(name)
        command = [python_executable, "-m", module, *(args or [])]
        runtime_env = prepend_cuda_library_path(os.environ.copy())
        with stdout_log.open("ab") as stdout_handle, stderr_log.open("ab") as stderr_handle:
            process = subprocess.Popen(
                command,
                cwd=settings.project_root,
                stdout=stdout_handle,
                stderr=stderr_handle,
                env=runtime_env,
                start_new_session=True,
            )
        self.pid_file(name).write_text(str(process.pid), encoding="utf-8")
        time.sleep(0.5)
        found = self.find_module_process(name, module)
        return {
            "ok": True,
            "detail": "started",
            "process": self.process_payload(found),
            "stdout_path": str(stdout_log),
            "stderr_path": str(stderr_log),
        }

    def stop_module(self, name: str, module: str) -> dict[str, Any]:
        """Stop module.
        
        Args:
            name: Service name.
            module: Python module.
        
        Returns:
            dict[str, Any]: Stop result.
        """
        processes = self.find_module_processes(name, module)
        if not processes:
            self.clear_pid_file(name)
            return {"ok": True, "detail": "not-running"}
        stopped_pids: list[int] = []
        for process in processes:
            try:
                process.terminate()
                process.wait(timeout=10)
            except psutil.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            except psutil.Error:
                continue
            stopped_pids.append(process.pid)
        self.clear_pid_file(name)
        return {"ok": True, "detail": "stopped", "pids": stopped_pids}

    def module_status(self, name: str, module: str) -> dict[str, Any]:
        """Get module status.
        
        Args:
            name: Service name.
            module: Python module.
        
        Returns:
            dict[str, Any]: Status dict.
        """
        process = self.find_module_process(name, module)
        return {
            "service": name,
            "module": module,
            "process": self.process_payload(process),
            "stdout_path": str(self.stdout_log(name)),
            "stderr_path": str(self.stderr_log(name)),
        }

    def find_module_process(self, name: str, module: str) -> psutil.Process | None:
        """Find module process.
        
        Args:
            name: Service name.
            module: Python module.
        
        Returns:
            psutil.Process | None: Process or None.
        """
        processes = self.find_module_processes(name, module)
        return processes[0] if processes else None

    def find_module_processes(self, name: str, module: str) -> list[psutil.Process]:
        """Find module processes.
        
        Args:
            name: Service name.
            module: Python module.
        
        Returns:
            list[psutil.Process]: Process list.
        """
        matches: dict[int, psutil.Process] = {}
        pid = self.read_pid_file(name)
        if pid is not None:
            try:
                process = psutil.Process(pid)
                if self.looks_like_module_process(process, module):
                    matches[process.pid] = process
            except psutil.Error:
                self.clear_pid_file(name)

        target = f"-m {module}".lower()
        for process in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                cmdline = " ".join(process.cmdline()).lower()
                if target in cmdline:
                    self.pid_file(name).write_text(str(process.pid), encoding="utf-8")
                    matches[process.pid] = process
            except psutil.Error:
                continue
        return sorted(matches.values(), key=lambda process: process.pid)

    def looks_like_module_process(self, process: psutil.Process, module: str) -> bool:
        """Check if process is module.
        
        Args:
            process: Process.
            module: Python module.
        
        Returns:
            bool: Whether process matches.
        """
        cmdline = " ".join(process.cmdline()).lower()
        return f"-m {module}".lower() in cmdline

    def find_process(self, name: str) -> psutil.Process | None:
        """Find process.
        
        Args:
            name: Service name.
        
        Returns:
            psutil.Process | None: Process or None.
        """
        processes = self.find_processes(name)
        return processes[0] if processes else None

    def find_processes(self, name: str) -> list[psutil.Process]:
        """Find processes.
        
        Args:
            name: Service name.
        
        Returns:
            list[psutil.Process]: Process list.
        """
        matches: dict[int, psutil.Process] = {}
        pid = self.read_pid_file(name)
        if pid is not None:
            try:
                process = psutil.Process(pid)
                if self.looks_like_service(process, name):
                    matches[process.pid] = process
            except psutil.Error:
                self.clear_pid_file(name)

        target = self.service(name).module.lower()
        for process in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                cmdline = " ".join(process.cmdline()).lower()
                if "uvicorn" in cmdline and target in cmdline:
                    self.pid_file(name).write_text(str(process.pid), encoding="utf-8")
                    matches[process.pid] = process
            except psutil.Error:
                continue

        service = self.service(name)
        for conn in psutil.net_connections(kind="inet"):
            if conn.status != psutil.CONN_LISTEN or not conn.laddr:
                continue
            if conn.laddr.port != service.port or conn.pid is None:
                continue
            try:
                process = psutil.Process(conn.pid)
            except psutil.Error:
                continue
            if self.looks_like_service(process, name):
                matches[process.pid] = process

        return sorted(matches.values(), key=lambda process: process.pid)

    def looks_like_service(self, process: psutil.Process, name: str) -> bool:
        """Check if process is service.
        
        Args:
            process: Process.
            name: Service name.
        
        Returns:
            bool: Whether process matches.
        """
        cmdline = " ".join(process.cmdline()).lower()
        return "uvicorn" in cmdline and self.service(name).module.lower() in cmdline

    def process_payload(self, process: psutil.Process | None) -> dict[str, Any]:
        if not process:
            return {"running": False}
        try:
            with process.oneshot():
                status = process.status()
                if status == psutil.STATUS_ZOMBIE:
                    return {"running": False, "pid": process.pid, "status": status}
                memory = process.memory_info()
                return {
                    "running": True,
                    "pid": process.pid,
                    "name": process.name(),
                    "status": status,
                    "create_time": process.create_time(),
                    "cpu_percent": process.cpu_percent(interval=0.1),
                    "rss_bytes": memory.rss,
                    "vms_bytes": memory.vms,
                    "cmdline": process.cmdline(),
                }
        except psutil.Error:
            return {"running": False, "pid": process.pid}

    def pid_file(self, name: str) -> Path:
        return self._runtime_dir / f"{name}.pid"

    def stdout_log(self, name: str) -> Path:
        return self._runtime_dir / f"{name}.stdout.log"

    def stderr_log(self, name: str) -> Path:
        return self._runtime_dir / f"{name}.stderr.log"

    def _preserve_service_logs(self, name: str, *, keep: int = 5) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        for path in (self.stdout_log(name), self.stderr_log(name)):
            if path.exists() and path.stat().st_size:
                archived = path.with_name(f"{path.name}.{stamp}")
                path.replace(archived)
            path.touch(exist_ok=True)
            archives = sorted(
                path.parent.glob(f"{path.name}.*"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
            for expired in archives[max(1, keep):]:
                expired.unlink(missing_ok=True)

    def read_pid_file(self, name: str) -> int | None:
        path = self.pid_file(name)
        if not path.exists():
            return None
        try:
            return int(path.read_text(encoding="utf-8").strip())
        except ValueError:
            self.clear_pid_file(name)
            return None

    def clear_pid_file(self, name: str) -> None:
        path = self.pid_file(name)
        if path.exists():
            path.unlink()

    def resolve_foreground_python(self) -> str:
        return resolve_python_executable(settings.project_root)

    def _resolve_background_python(self) -> str:
        return self.resolve_foreground_python()

    def _service_python(self, name: str) -> str:
        if name != "audio-understanding":
            return self._resolve_background_python()
        configured = str(settings.audio_understanding_python or "").strip()
        candidates = [
            Path(configured).expanduser() if configured else None,
            settings.project_root / ".venv-audio" / "bin" / "python",
        ]
        for candidate in candidates:
            if candidate and candidate.exists():
                return str(candidate)
        return self._resolve_background_python()

