from __future__ import annotations

import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse
import urllib.request

import psutil

from ..config import settings
from ..observability import configure_logging, log_event
from ..resources import ResourcePlacementCoordinator, WorkloadSpec
from ..resources.allocations import latest_sidecar_allocations
from ..resources.probe import collect_resource_snapshot
from ..resources.runtime_registry import load_resource_routing_registry
from ..system.cuda_env import prepend_cuda_library_path
from ..system.python_runtime import resolve_python_executable
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


@dataclass(frozen=True, slots=True)
class SidecarVramEstimate:
    vram_mb: int
    source: str
    observed_age_seconds: float | None = None
    ttl_seconds: int | None = None


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
            settings.project_root / ".venv-audio" / "Scripts" / "python.exe",
        ]
        for candidate in candidates:
            if candidate and candidate.exists():
                return str(candidate)
        return self._resolve_background_python()

    def _start_shana_dependencies(self) -> list[dict[str, Any]]:
        return [
            self._llm_dependency_status("start"),
            self._stt_dependency_status("start"),
            self._audio_understanding_dependency_action("start"),
            self._tts_dependency_action("start"),
        ]

    def _stop_shana_dependencies(self) -> list[dict[str, Any]]:
        return [
            self._llm_dependency_status("stop"),
            self._stt_dependency_status("stop"),
            self._audio_understanding_dependency_action("stop"),
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
            completed = self._run_sidecar_command(script, timeout=45, env_overlay=self._qwen_tts_admission_env())
            if action == "start":
                self._log_sidecar_allocation(
                    provider="qwen-tts",
                    kind="qwen-tts",
                    process=self._find_local_url_process(endpoint),
                    estimated_vram_mb=self._sidecar_estimated_vram_mb("qwen-tts"),
                )
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

    def _audio_understanding_dependency_action(self, action: str) -> dict[str, Any]:
        endpoint = settings.audio_understanding_endpoint
        if not endpoint:
            return {
                "name": "audio-understanding",
                "status": "in-process",
                "detail": "No sidecar endpoint configured; Gamma uses local prosody and configured in-process adapters.",
            }
        if not self._is_local_url(endpoint):
            return {
                "name": "audio-understanding",
                "status": "external",
                "detail": f"Audio-understanding endpoint is remote: {endpoint}",
            }
        try:
            result = self.start("audio-understanding") if action == "start" else self.stop("audio-understanding")
            if action == "start" and result.get("ok"):
                self._wait_sidecar_health(endpoint, timeout_seconds=30)
                self._log_sidecar_allocation(
                    provider="audio-understanding",
                    kind="audio-understanding",
                    process=self.find_process("audio-understanding"),
                    estimated_vram_mb=self._sidecar_estimated_vram_mb("audio-understanding"),
                )
            return {
                "name": "audio-understanding",
                "status": "ok" if result.get("ok") else "error",
                "detail": str(result.get("detail") or action),
                "endpoint": endpoint,
            }
        except Exception as exc:
            return {"name": "audio-understanding", "status": "error", "detail": str(exc), "endpoint": endpoint}

    def _tts_script(self, action: str, *, provider: str) -> list[str]:
        scripts_dir = settings.project_root / "scripts"
        return [
            self.resolve_foreground_python(),
            str(scripts_dir / f"{'start' if action == 'start' else 'stop'}_qwen_tts_server.py"),
        ]

    def _run_sidecar_command(
        self,
        command: list[str],
        *,
        timeout: int,
        env_overlay: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        kwargs: dict[str, Any] = {
            "cwd": settings.project_root,
            "capture_output": True,
            "text": True,
            "timeout": timeout,
            "check": True,
        }
        if env_overlay:
            env = os.environ.copy()
            env.update(env_overlay)
            kwargs["env"] = env
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0  # SW_HIDE
            kwargs["startupinfo"] = si
        return subprocess.run(command, **kwargs)

    def _qwen_tts_admission_env(self) -> dict[str, str]:
        requested = os.environ.get("QWEN_TTS_DEVICE") or settings.qwen_tts_device or "auto"
        if not self._is_auto_device(requested):
            self._log_startup_admission_bypassed(
                provider="qwen-tts",
                kind="qwen-tts",
                modality="speech",
                model="qwen-tts",
                requested_device=requested,
            )
            return {}
        estimate = self._sidecar_vram_estimate("qwen-tts")
        device = self._admitted_sidecar_device(
            provider="qwen-tts",
            kind="qwen-tts",
            modality="speech",
            model="qwen-tts",
            estimated_vram_mb=estimate.vram_mb,
            estimate_source=estimate.source,
            estimate_observed_age_seconds=estimate.observed_age_seconds,
            estimate_ttl_seconds=estimate.ttl_seconds,
        )
        return {"QWEN_TTS_DEVICE": device} if device else {}

    def _audio_understanding_admission_env(self) -> dict[str, str]:
        if not any(
            self._is_auto_device(value)
            for value in (settings.audio_analysis_device, settings.speaker_emotion_device, settings.audio_event_device)
        ):
            self._log_startup_admission_bypassed(
                provider="audio-understanding",
                kind="audio-understanding",
                modality="audio",
                model=None,
                requested_device="explicit",
            )
            return {}
        estimate = self._sidecar_vram_estimate("audio-understanding")
        device = self._admitted_sidecar_device(
            provider="audio-understanding",
            kind="audio-understanding",
            modality="audio",
            model=None,
            estimated_vram_mb=estimate.vram_mb,
            estimate_source=estimate.source,
            estimate_observed_age_seconds=estimate.observed_age_seconds,
            estimate_ttl_seconds=estimate.ttl_seconds,
        )
        if not device:
            return {}
        env: dict[str, str] = {}
        if self._is_auto_device(settings.audio_analysis_device):
            env["SHANA_AUDIO_ANALYSIS_DEVICE"] = device
        if self._is_auto_device(settings.speaker_emotion_device):
            env["SHANA_SPEAKER_EMOTION_DEVICE"] = device
        if self._is_auto_device(settings.audio_event_device):
            env["SHANA_AUDIO_EVENT_DEVICE"] = device
        return env

    def _admitted_sidecar_device(
        self,
        *,
        provider: str,
        kind: str,
        modality: str,
        model: str | None,
        estimated_vram_mb: int = 0,
        estimate_source: str = "configured_fallback",
        estimate_observed_age_seconds: float | None = None,
        estimate_ttl_seconds: int | None = None,
    ) -> str | None:
        registry = load_resource_routing_registry()
        if not registry.policy.startup_admission:
            log_event(
                self._logger,
                logging.INFO,
                "resource.startup_admission.skipped",
                "Startup admission skipped because policy is disabled.",
                provider=provider,
                kind=kind,
                modality=modality,
                model=model,
            )
            return None
        if registry.validation_errors:
            log_event(
                self._logger,
                logging.WARNING,
                "resource.startup_admission.skipped",
                "Startup admission skipped because resource registry validation failed.",
                provider=provider,
                kind=kind,
                modality=modality,
                model=model,
                validation_errors=list(registry.validation_errors),
            )
            return None
        decision = ResourcePlacementCoordinator(registry=registry).rank(
            WorkloadSpec(
                id=f"{kind}:startup",
                kind=kind,
                provider=provider,
                model=model,
                modality=modality,
                estimated_vram_mb=estimated_vram_mb,
            )
        )
        selected = decision.selected.as_payload() if decision.selected else None
        event_name = "resource.startup_admission.selected" if decision.selected else "resource.startup_admission.rejected"
        level = logging.INFO if decision.selected else logging.WARNING
        log_event(
            self._logger,
            level,
            event_name,
            "Startup admission placement decision completed.",
            provider=provider,
            kind=kind,
            modality=modality,
            model=model,
            workload_id=decision.workload.id,
            estimated_vram_mb=decision.workload.estimated_vram_mb,
            estimate_source=estimate_source,
            estimate_observed_age_seconds=estimate_observed_age_seconds,
            estimate_ttl_seconds=estimate_ttl_seconds,
            minimum_headroom_mb=decision.workload.minimum_headroom_mb,
            status=decision.status,
            selected=selected,
            rejected=decision.rejected,
            snapshot_age_seconds=decision.snapshot_age_seconds,
        )
        if decision.selected is None:
            return None
        return decision.selected.target.device or None

    def _log_startup_admission_bypassed(
        self,
        *,
        provider: str,
        kind: str,
        modality: str,
        model: str | None,
        requested_device: str,
    ) -> None:
        log_event(
            self._logger,
            logging.INFO,
            "resource.startup_admission.bypassed",
            "Startup admission bypassed for explicit sidecar device.",
            provider=provider,
            kind=kind,
            modality=modality,
            model=model,
            requested_device=requested_device,
        )

    def _sidecar_estimated_vram_mb(self, kind: str) -> int:
        return self._sidecar_vram_estimate(kind).vram_mb

    def _sidecar_vram_estimate(self, kind: str) -> SidecarVramEstimate:
        normalized = kind.strip().lower()
        registry = load_resource_routing_registry()
        if normalized == "qwen-tts":
            configured = max(0, int(getattr(registry.policy, "qwen_tts_estimated_vram_mb", 0)))
            provider = "qwen-tts"
        elif normalized == "audio-understanding":
            configured = max(0, int(getattr(registry.policy, "audio_understanding_estimated_vram_mb", 0)))
            provider = "audio-understanding"
        else:
            return SidecarVramEstimate(vram_mb=0, source="unsupported_sidecar")
        log_path = settings.data_dir / "runtime" / "logs" / "supervisor.jsonl"
        for allocation in latest_sidecar_allocations(
            log_path,
            ttl_seconds=registry.policy.sidecar_allocation_ttl_seconds,
        ):
            if allocation.provider == provider and allocation.kind == normalized and allocation.fresh and allocation.observed_vram_mb > 0:
                return SidecarVramEstimate(
                    vram_mb=allocation.observed_vram_mb,
                    source="observed_fresh",
                    observed_age_seconds=allocation.age_seconds,
                    ttl_seconds=registry.policy.sidecar_allocation_ttl_seconds,
                )
        return SidecarVramEstimate(
            vram_mb=configured,
            source="configured_fallback",
            ttl_seconds=registry.policy.sidecar_allocation_ttl_seconds,
        )

    def _log_sidecar_allocation(
        self,
        *,
        provider: str,
        kind: str,
        process: psutil.Process | None,
        estimated_vram_mb: int,
    ) -> dict[str, Any]:
        payload = self._sidecar_allocation_payload(
            provider=provider,
            kind=kind,
            process=process,
            estimated_vram_mb=estimated_vram_mb,
        )
        log_event(
            self._logger,
            logging.INFO,
            "resource.sidecar_allocation.observed",
            "Observed sidecar GPU allocation after startup.",
            **payload,
        )
        return payload

    def _sidecar_allocation_payload(
        self,
        *,
        provider: str,
        kind: str,
        process: psutil.Process | None,
        estimated_vram_mb: int,
    ) -> dict[str, Any]:
        pid = process.pid if process else None
        gpu_allocations: list[dict[str, Any]] = []
        snapshot = collect_resource_snapshot(
            project_root=settings.project_root,
            include_gpu=bool(settings.dashboard_enable_gpu),
        )
        gpu_payload = snapshot.gpu if isinstance(snapshot.gpu, dict) else {}
        if pid is not None and gpu_payload.get("ok"):
            for gpu in gpu_payload.get("gpus", []):
                if not isinstance(gpu, dict):
                    continue
                for gpu_process in gpu.get("processes", []):
                    if not isinstance(gpu_process, dict):
                        continue
                    if int(gpu_process.get("pid") or -1) != pid:
                        continue
                    gpu_allocations.append(
                        {
                            "gpu_index": gpu.get("index"),
                            "gpu_uuid": gpu.get("uuid") or gpu_process.get("gpu_uuid"),
                            "used_memory_mb": int(gpu_process.get("used_memory_mb") or 0),
                        }
                    )
        observed_vram_mb = sum(max(0, int(item.get("used_memory_mb") or 0)) for item in gpu_allocations)
        process_payload = self.process_payload(process)
        return {
            "provider": provider,
            "kind": kind,
            "pid": pid,
            "process_running": bool(process_payload.get("running")),
            "estimated_vram_mb": max(0, int(estimated_vram_mb)),
            "observed_vram_mb": observed_vram_mb,
            "allocation_delta_mb": observed_vram_mb - max(0, int(estimated_vram_mb)),
            "gpu_allocations": gpu_allocations,
            "gpu_process_match_count": len(gpu_allocations),
            "snapshot_sampled_at": snapshot.sampled_at,
            "gpu_status": gpu_payload.get("detail") if not gpu_payload.get("ok") else "ok",
        }

    def _find_local_url_process(self, value: str | None) -> psutil.Process | None:
        if not value:
            return None
        parsed = urlparse(value)
        port = parsed.port
        if port is None:
            if parsed.scheme == "http":
                port = 80
            elif parsed.scheme == "https":
                port = 443
        if port is None:
            return None
        for conn in psutil.net_connections(kind="inet"):
            if conn.status != psutil.CONN_LISTEN or not conn.laddr or conn.pid is None:
                continue
            if conn.laddr.port != port:
                continue
            try:
                return psutil.Process(conn.pid)
            except psutil.Error:
                continue
        return None

    def _wait_sidecar_health(self, endpoint: str, *, timeout_seconds: float) -> bool:
        health_url = self._health_url(endpoint)
        if not health_url:
            return False
        deadline = time.time() + max(0.0, timeout_seconds)
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(health_url, timeout=1) as response:
                    if 200 <= response.status < 300:
                        return True
            except Exception:
                time.sleep(0.5)
        return False

    @staticmethod
    def _health_url(endpoint: str) -> str | None:
        parsed = urlparse(endpoint)
        if not parsed.scheme or not parsed.netloc:
            return None
        return urlunparse((parsed.scheme, parsed.netloc, "/health", "", "", ""))

    @staticmethod
    def _is_auto_device(value: str | None) -> bool:
        return str(value or "").strip().lower() == "auto"

    def _is_local_url(self, value: str | None) -> bool:
        if not value:
            return False
        parsed = urlparse(value)
        return parsed.hostname in {"127.0.0.1", "localhost"}
