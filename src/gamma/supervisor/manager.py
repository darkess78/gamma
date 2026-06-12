from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import psutil

from ..config import settings
from ..system.cuda_env import prepend_cuda_library_path
from ..system.python_runtime import python_candidates, resolve_python_executable
from ..voice.voice_profiles import resolve_tts_config


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


class ProcessManager:
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

        python_executable = self._resolve_background_python()
        stdout_log = self.stdout_log(name)
        stderr_log = self.stderr_log(name)
        stdout_log.write_text("", encoding="utf-8")
        stderr_log.write_text("", encoding="utf-8")
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

        with stdout_log.open("ab") as stdout_handle, stderr_log.open("ab") as stderr_handle:
            if os.name == "nt":
                creationflags = (
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    | subprocess.DETACHED_PROCESS
                    | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                )
                process = subprocess.Popen(
                    command,
                    cwd=settings.project_root,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    env=runtime_env,
                    creationflags=creationflags,
                )
            else:
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
        stdout_log.write_text("", encoding="utf-8")
        stderr_log.write_text("", encoding="utf-8")
        command = [python_executable, "-m", module, *(args or [])]
        runtime_env = prepend_cuda_library_path(os.environ.copy())
        with stdout_log.open("ab") as stdout_handle, stderr_log.open("ab") as stderr_handle:
            process = subprocess.Popen(
                command,
                cwd=settings.project_root,
                stdout=stdout_handle,
                stderr=stderr_handle,
                env=runtime_env,
                start_new_session=os.name != "nt",
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    | subprocess.DETACHED_PROCESS
                    | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    if os.name == "nt"
                    else 0
                ),
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
        with process.oneshot():
            memory = process.memory_info()
            return {
                "running": True,
                "pid": process.pid,
                "name": process.name(),
                "status": process.status(),
                "create_time": process.create_time(),
                "cpu_percent": process.cpu_percent(interval=0.1),
                "rss_bytes": memory.rss,
                "vms_bytes": memory.vms,
                "cmdline": process.cmdline(),
            }

    def pid_file(self, name: str) -> Path:
        return self._runtime_dir / f"{name}.pid"

    def stdout_log(self, name: str) -> Path:
        return self._runtime_dir / f"{name}.stdout.log"

    def stderr_log(self, name: str) -> Path:
        return self._runtime_dir / f"{name}.stderr.log"

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

    def _python_candidates(self) -> list[Path]:
        return python_candidates(settings.project_root)

    def _start_shana_dependencies(self) -> list[dict[str, Any]]:
        return [
            self._llm_dependency_status("start"),
            self._stt_dependency_status("start"),
            self._tts_dependency_action("start"),
        ]

    def _stop_shana_dependencies(self) -> list[dict[str, Any]]:
        return [
            self._llm_dependency_status("stop"),
            self._stt_dependency_status("stop"),
            self._tts_dependency_action("stop"),
        ]

    def _llm_dependency_status(self, action: str) -> dict[str, Any]:
        provider = settings.llm_provider.strip().lower()
        endpoint = settings.local_llm_endpoint
        if provider not in {"local", "ollama"}:
            return {"name": "llm", "status": "skipped", "detail": f"{provider} provider does not need local service management."}
        if self._is_local_url(endpoint):
            return {
                "name": "llm",
                "status": "external",
                "detail": f"LLM provider is {provider}. {action.title()} it separately if you want Ollama managed too.",
                "endpoint": endpoint,
            }
        return {"name": "llm", "status": "skipped", "detail": f"LLM endpoint is remote: {endpoint}"}

    def _stt_dependency_status(self, action: str) -> dict[str, Any]:
        provider = settings.stt_provider.strip().lower()
        if provider in {"local", "faster-whisper", "faster_whisper", "whisper"}:
            return {
                "name": "stt",
                "status": "in-process",
                "detail": f"STT provider {provider} is loaded by Shana and does not run as a separate service.",
            }
        return {"name": "stt", "status": "skipped", "detail": f"STT provider {provider} does not have managed sidecar control."}

    def _tts_dependency_action(self, action: str) -> dict[str, Any]:
        tts_cfg = resolve_tts_config()
        provider = tts_cfg.provider.strip().lower()
        if provider not in {"qwen-tts", "qwen_tts", "qwen", "qwentts"}:
            return {"name": "tts", "status": "skipped", "detail": f"TTS provider {provider} does not need local sidecar management."}
        endpoint = tts_cfg.qwen_tts_endpoint
        label = "Qwen3-TTS"
        if not endpoint:
            return {"name": "tts", "status": "skipped", "detail": f"No {label} endpoint configured."}
        if not self._is_local_url(endpoint):
            return {
                "name": "tts",
                "status": "skipped",
                "detail": f"{label} endpoint is remote and will not be managed here: {endpoint}",
            }
        script = self._tts_script(action, provider=provider)
        try:
            completed = self._run_sidecar_command(script, timeout=45)
            return {
                "name": "tts",
                "status": "ok",
                "detail": completed.stdout.strip() or f"TTS {action} completed.",
                "stderr": completed.stderr.strip(),
            }
        except subprocess.CalledProcessError as exc:
            return {
                "name": "tts",
                "status": "error",
                "detail": (exc.stderr or exc.stdout or f"TTS {action} failed").strip(),
            }
        except Exception as exc:
            return {"name": "tts", "status": "error", "detail": str(exc)}

    def _tts_script(self, action: str, *, provider: str) -> list[str]:
        scripts_dir = settings.project_root / "scripts"
        return [
            self.resolve_foreground_python(),
            str(scripts_dir / f"{'start' if action == 'start' else 'stop'}_qwen_tts_server.py"),
        ]

    def _run_sidecar_command(self, command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
        kwargs: dict[str, Any] = {
            "cwd": settings.project_root,
            "capture_output": True,
            "text": True,
            "timeout": timeout,
            "check": True,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0  # SW_HIDE
            kwargs["startupinfo"] = si
        return subprocess.run(command, **kwargs)

    def _is_local_url(self, value: str | None) -> bool:
        if not value:
            return False
        parsed = urlparse(value)
        return parsed.hostname in {"127.0.0.1", "localhost"}
