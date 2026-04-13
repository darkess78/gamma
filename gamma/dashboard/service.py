from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
import wave
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import psutil

from ..config import settings
from ..memory.service import MemoryService
from ..schemas.response import AssistantResponse, VisionAnalysis
from ..supervisor.manager import ProcessManager
from ..system.status import SystemStatusService


class DashboardService:
    def __init__(self) -> None:
        self._memory = MemoryService()
        self._process_manager = ProcessManager()
        self._system_status = SystemStatusService()
        self._metrics_lock = threading.Lock()
        self._cached_machine_status: dict[str, Any] = {}
        self._cached_machine_status_at: str | None = None
        self._latest_provider_action: dict[str, Any] = {"status": "idle", "detail": "No provider action has been run yet."}
        self._refresh_machine_status()

    def build_status(self) -> dict[str, Any]:
        local_status = self._system_status.build_status()
        selected_tts_provider = self.selected_tts_provider()
        local_status["providers"]["tts"]["selected_provider"] = selected_tts_provider
        local_status["providers"]["tts"]["restart_required"] = selected_tts_provider != settings.tts_provider
        local_status["providers"]["tts"]["available_providers"] = ["piper", "local", "openai", "stub"]
        shana_process = self._process_manager.find_process("shana")
        system_status = self._probe_json(settings.shana_base_url + "/v1/system/status")
        api_health = {
            "ok": system_status.get("ok", False),
            "detail": "ok" if system_status.get("ok", False) else system_status.get("detail", "unreachable"),
        }
        return {
            "dashboard": {
                "name": f"{settings.app_name} dashboard",
                "url": settings.dashboard_base_url,
            },
            "app": local_status["app"],
            "providers": local_status["providers"],
            "recent_artifacts": local_status["recent_artifacts"],
            "shana": {
                "url": settings.shana_base_url,
                "process": self._process_manager.process_payload(shana_process),
                "api_health": api_health,
                "system_status": system_status,
                "logs": {
                    "stdout_path": str(self._process_manager.stdout_log("shana")),
                    "stderr_path": str(self._process_manager.stderr_log("shana")),
                    "stdout_tail": self._tail(self._process_manager.stdout_log("shana")),
                    "stderr_tail": self._tail(self._process_manager.stderr_log("shana")),
                },
            },
            "machine": self._machine_status(),
            "memory_db": {
                "stats": self._memory.stats(),
                "known_people": self._memory.get_known_people(),
            },
            "provider_actions": self._latest_provider_action,
            "timings": self._recent_timings(),
        }

    def start_shana(self) -> dict[str, Any]:
        return self._process_manager.start("shana")

    def stop_shana(self) -> dict[str, Any]:
        return self._process_manager.stop("shana")

    def restart_shana(self) -> dict[str, Any]:
        return self._process_manager.restart("shana")

    def stop_dashboard(self) -> dict[str, Any]:
        self._schedule_stop("dashboard")
        return {"ok": True, "detail": "dashboard-stop-scheduled", "url": settings.dashboard_base_url}

    def stop_all(self) -> dict[str, Any]:
        shana_result = self._process_manager.stop("shana")
        self._schedule_stop("dashboard")
        return {
            "ok": True,
            "detail": "all-stop-scheduled",
            "shana": shana_result,
            "dashboard_url": settings.dashboard_base_url,
        }

    def start_tts(self) -> dict[str, Any]:
        return self._run_provider_action(
            "tts_start",
            self._tts_script_command("start"),
            success_detail="GPT-SoVITS start requested.",
        )

    def stop_tts(self) -> dict[str, Any]:
        return self._run_provider_action(
            "tts_stop",
            self._tts_script_command("stop"),
            success_detail="GPT-SoVITS stop requested.",
        )

    def selected_tts_provider(self) -> str:
        app_toml = settings.project_root / "config" / "app.toml"
        if app_toml.exists():
            match = re.search(r'(?m)^\s*tts_provider\s*=\s*"([^"]+)"\s*$', app_toml.read_text(encoding="utf-8"))
            if match:
                return match.group(1).strip()
        return settings.tts_provider

    def set_tts_provider(self, provider: str) -> dict[str, Any]:
        normalized = provider.strip().lower()
        allowed = {"piper", "local", "openai", "stub"}
        if normalized not in allowed:
            raise ValueError(f"unsupported tts provider: {provider}")
        app_toml = settings.project_root / "config" / "app.toml"
        existing = app_toml.read_text(encoding="utf-8") if app_toml.exists() else ""
        if re.search(r'(?m)^\s*tts_provider\s*=\s*"([^"]+)"\s*$', existing):
            updated = re.sub(
                r'(?m)^\s*tts_provider\s*=\s*"([^"]+)"\s*$',
                f'tts_provider = "{normalized}"',
                existing,
                count=1,
            )
        else:
            updated = existing.rstrip()
            if updated:
                updated += "\n"
            updated += f'tts_provider = "{normalized}"\n'
        app_toml.write_text(updated, encoding="utf-8")
        self._latest_provider_action = {
            "action": "tts_provider_select",
            "status": "ok",
            "detail": f"TTS provider set to {normalized}. Restart Shana to use it for conversation responses.",
            "ran_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "provider": normalized,
        }
        return {
            "ok": True,
            "provider": normalized,
            "detail": "TTS provider saved. Restart Shana to use it for normal conversations.",
        }

    def test_stt(self) -> dict[str, Any]:
        sample = self._sample_audio_path()
        return self._run_provider_action(
            "stt_test",
            self._python_module_command("gamma.run_stt_test", str(sample)),
            success_detail="STT smoke test completed.",
        )

    def test_tts(self) -> dict[str, Any]:
        selected_provider = self.selected_tts_provider()
        return self._run_provider_action(
            "tts_test",
            self._python_module_command("gamma.run_tts_test", "Dashboard TTS smoke test."),
            env_overrides={"SHANA_TTS_PROVIDER": selected_provider},
            success_detail="TTS smoke test completed.",
        )

    def test_llm(self) -> dict[str, Any]:
        return self._run_provider_action(
            "llm_test",
            self._python_module_command("gamma.run_llm_test", "Dashboard LLM smoke test."),
            success_detail="LLM smoke test completed.",
        )

    def test_voice_roundtrip(self) -> dict[str, Any]:
        sample = self._sample_audio_path()
        return self._run_provider_action(
            "voice_roundtrip_test",
            self._python_module_command("gamma.run_voice_roundtrip", str(sample)),
            timeout=180,
            success_detail="Full voice loop completed.",
        )

    def append_client_log(self, payload: dict[str, Any]) -> None:
        runtime_dir = settings.data_dir / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        log_path = runtime_dir / "dashboard.client.log"
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        line = json.dumps({"timestamp": timestamp, **payload}, ensure_ascii=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def run_remote_live_voice_turn(
        self,
        *,
        pcm_bytes: bytes,
        session_id: str | None,
        synthesize_speech: bool,
    ) -> dict[str, Any]:
        return self.start_remote_live_job(
            pcm_bytes=pcm_bytes,
            session_id=session_id,
            synthesize_speech=synthesize_speech,
            turn_id=None,
        )

    def start_remote_live_job(
        self,
        *,
        pcm_bytes: bytes,
        session_id: str | None,
        synthesize_speech: bool,
        turn_id: str | None,
    ) -> dict[str, Any]:
        return self._post_live_audio(
            path="/v1/voice/live/start",
            pcm_bytes=pcm_bytes,
            session_id=session_id,
            synthesize_speech=synthesize_speech,
            turn_id=turn_id,
        )

    def transcribe_remote_live_audio(self, *, pcm_bytes: bytes) -> dict[str, Any]:
        return self._post_live_audio(
            path="/v1/voice/transcribe",
            pcm_bytes=pcm_bytes,
            session_id=None,
            synthesize_speech=None,
            turn_id=None,
        )

    def get_remote_live_job(self, turn_id: str) -> dict[str, Any]:
        return self._probe_json(settings.shana_base_url + f"/v1/voice/live/{turn_id}", raw_payload=True)

    def cancel_remote_live_job(self, turn_id: str, *, reason: str = "interrupted") -> dict[str, Any]:
        boundary = f"gamma-cancel-{uuid.uuid4().hex}"
        body = self._build_cancel_body(boundary=boundary, reason=reason)
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            **self._api_headers(),
        }
        request = urllib.request.Request(
            settings.shana_base_url + f"/v1/voice/live/{turn_id}/cancel",
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"cancel live turn failed: http-{exc.code} {detail}") from exc
        except Exception as exc:
            raise RuntimeError(f"cancel live turn failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("cancel live turn returned a non-object payload")
        return payload

    def _post_live_audio(
        self,
        *,
        path: str,
        pcm_bytes: bytes,
        session_id: str | None,
        synthesize_speech: bool | None,
        turn_id: str | None,
    ) -> dict[str, Any]:
        wav_bytes = self._pcm_to_wav_bytes(pcm_bytes)
        boundary = f"gamma-live-{uuid.uuid4().hex}"
        body = self._build_multipart_body(
            boundary=boundary,
            audio_bytes=wav_bytes,
            session_id=session_id,
            synthesize_speech=synthesize_speech,
            turn_id=turn_id,
        )
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            **self._api_headers(),
        }
        request = urllib.request.Request(
            settings.shana_base_url + path,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"remote voice roundtrip failed: http-{exc.code} {detail}") from exc
        except Exception as exc:
            raise RuntimeError(f"remote voice roundtrip failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("remote voice roundtrip returned a non-object payload")
        return payload

    def analyze_remote_image(
        self,
        *,
        image_bytes: bytes,
        filename: str,
        content_type: str,
        user_text: str,
        vision_mode: str | None,
    ) -> VisionAnalysis:
        payload = self._post_remote_image(
            path="/v1/vision/analyze",
            image_bytes=image_bytes,
            filename=filename,
            content_type=content_type,
            user_text=user_text,
            vision_mode=vision_mode,
            session_id=None,
            synthesize_speech=None,
        )
        return VisionAnalysis.model_validate(payload)

    def respond_remote_image(
        self,
        *,
        image_bytes: bytes,
        filename: str,
        content_type: str,
        user_text: str,
        vision_mode: str | None,
        session_id: str | None,
        synthesize_speech: bool,
    ) -> AssistantResponse:
        payload = self._post_remote_image(
            path="/v1/conversation/respond-with-image",
            image_bytes=image_bytes,
            filename=filename,
            content_type=content_type,
            user_text=user_text,
            vision_mode=vision_mode,
            session_id=session_id,
            synthesize_speech=synthesize_speech,
        )
        return AssistantResponse.model_validate(payload)

    def _schedule_stop(self, service_name: str, delay_seconds: float = 0.35) -> None:
        timer = threading.Timer(delay_seconds, lambda: self._process_manager.stop(service_name))
        timer.daemon = True
        timer.start()

    def _pcm_to_wav_bytes(self, pcm_bytes: bytes) -> bytes:
        buffer = BytesIO()
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(pcm_bytes)
        return buffer.getvalue()

    def _build_multipart_body(
        self,
        *,
        boundary: str,
        audio_bytes: bytes,
        session_id: str | None,
        synthesize_speech: bool | None,
        turn_id: str | None,
    ) -> bytes:
        parts: list[bytes] = []

        def add_field(name: str, value: str) -> None:
            parts.append(
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                    f"{value}\r\n"
                ).encode("utf-8")
            )

        if session_id:
            add_field("session_id", session_id)
        if turn_id:
            add_field("turn_id", turn_id)
        if synthesize_speech is not None:
            add_field("synthesize_speech", "true" if synthesize_speech else "false")
        parts.append(
            (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="audio_file"; filename="live-browser.wav"\r\n'
                "Content-Type: audio/wav\r\n\r\n"
            ).encode("utf-8")
            + audio_bytes
            + b"\r\n"
        )
        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        return b"".join(parts)

    def _build_cancel_body(self, *, boundary: str, reason: str) -> bytes:
        return (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="reason"\r\n\r\n'
            f"{reason}\r\n"
            f"--{boundary}--\r\n"
        ).encode("utf-8")

    def _post_remote_image(
        self,
        *,
        path: str,
        image_bytes: bytes,
        filename: str,
        content_type: str,
        user_text: str,
        vision_mode: str | None,
        session_id: str | None,
        synthesize_speech: bool | None,
    ) -> dict[str, Any]:
        boundary = f"gamma-image-{uuid.uuid4().hex}"
        body = self._build_image_multipart_body(
            boundary=boundary,
            image_bytes=image_bytes,
            filename=filename,
            content_type=content_type,
            user_text=user_text,
            vision_mode=vision_mode,
            session_id=session_id,
            synthesize_speech=synthesize_speech,
        )
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            **self._api_headers(),
        }
        request = urllib.request.Request(
            settings.shana_base_url + path,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"remote image request failed: http-{exc.code} {detail}") from exc
        except Exception as exc:
            raise RuntimeError(f"remote image request failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("remote image request returned a non-object payload")
        return payload

    def _build_image_multipart_body(
        self,
        *,
        boundary: str,
        image_bytes: bytes,
        filename: str,
        content_type: str,
        user_text: str,
        vision_mode: str | None,
        session_id: str | None,
        synthesize_speech: bool | None,
    ) -> bytes:
        parts: list[bytes] = []

        def add_field(name: str, value: str) -> None:
            parts.append(
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                    f"{value}\r\n"
                ).encode("utf-8")
            )

        add_field("user_text", user_text)
        if vision_mode:
            add_field("vision_mode", vision_mode)
        if session_id:
            add_field("session_id", session_id)
        if synthesize_speech is not None:
            add_field("synthesize_speech", "true" if synthesize_speech else "false")
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="image_file"; filename="{filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("utf-8")
            + image_bytes
            + b"\r\n"
        )
        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        return b"".join(parts)

    def _run_provider_action(
        self,
        action_name: str,
        command: list[str],
        *,
        timeout: int = 60,
        env_overrides: dict[str, str] | None = None,
        success_detail: str,
    ) -> dict[str, Any]:
        payload = {
            "action": action_name,
            "command": command,
            "ran_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        started_at = time.perf_counter()
        try:
            completed = self._run_command(command, timeout=timeout, env_overrides=env_overrides)
            payload.update(
                {
                    "status": "ok",
                    "detail": success_detail,
                    "returncode": completed.returncode,
                    "stdout": completed.stdout.strip(),
                    "stderr": completed.stderr.strip(),
                }
            )
        except subprocess.CalledProcessError as exc:
            payload.update(
                {
                    "status": "error",
                    "detail": f"{action_name} failed",
                    "returncode": exc.returncode,
                    "stdout": (exc.stdout or "").strip(),
                    "stderr": (exc.stderr or "").strip(),
                }
            )
        except subprocess.TimeoutExpired as exc:
            payload.update(
                {
                    "status": "error",
                    "detail": f"{action_name} timed out",
                    "stdout": (exc.stdout or "").strip() if isinstance(exc.stdout, str) else "",
                    "stderr": (exc.stderr or "").strip() if isinstance(exc.stderr, str) else "",
                }
            )
        except Exception as exc:
            payload.update(
                {
                    "status": "error",
                    "detail": str(exc),
                    "stdout": "",
                    "stderr": "",
                }
            )
        self._latest_provider_action = payload
        payload["duration_ms"] = round((time.perf_counter() - started_at) * 1000, 1)
        return payload

    def _run_command(
        self,
        command: list[str],
        *,
        timeout: int,
        env_overrides: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        run_kwargs: dict[str, Any] = {
            "cwd": settings.project_root,
            "capture_output": True,
            "text": True,
            "timeout": timeout,
            "check": True,
        }
        if env_overrides:
            env = os.environ.copy()
            env.update(env_overrides)
            run_kwargs["env"] = env
        if os.name == "nt":
            run_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.run(command, **run_kwargs)

    def _sample_audio_path(self) -> Path:
        sample = settings.project_root / "test_audio" / "jfk.flac"
        if not sample.exists():
            raise FileNotFoundError(f"sample audio not found: {sample}")
        return sample

    def _python_module_command(self, module: str, *args: str) -> list[str]:
        return [self._process_manager.resolve_foreground_python(), "-m", module, *args]

    def _tts_script_command(self, action: str) -> list[str]:
        scripts_dir = settings.project_root / "scripts"
        if os.name == "nt":
            script = scripts_dir / f"{'start' if action == 'start' else 'stop'}_gpt_sovits_windows.ps1"
            return [self._process_manager.resolve_foreground_python(), str(script.with_suffix(".py"))]
        script = scripts_dir / f"{'start' if action == 'start' else 'stop'}_gpt_sovits_linux.sh"
        return ["bash", str(script)]

    def _probe_json(self, url: str, *, raw_payload: bool = False) -> dict[str, Any]:
        try:
            request = urllib.request.Request(url, headers=self._api_headers())
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if raw_payload:
                return payload
            return {"ok": True, "payload": payload}
        except urllib.error.HTTPError as exc:
            return {"ok": False, "detail": f"http-{exc.code}"}
        except Exception as exc:
            return {"ok": False, "detail": str(exc)}

    def _machine_status(self) -> dict[str, Any]:
        now = time.time()
        needs_refresh = False
        with self._metrics_lock:
            if not self._cached_machine_status_at:
                needs_refresh = True
            elif not self._cached_machine_status:
                needs_refresh = True
            else:
                last = datetime.fromisoformat(self._cached_machine_status_at.replace("Z", "+00:00")).timestamp()
                needs_refresh = now - last >= settings.dashboard_metrics_interval_seconds

        if needs_refresh:
            self._refresh_machine_status()

        with self._metrics_lock:
            return {
                **self._cached_machine_status,
                "sampled_at": self._cached_machine_status_at,
                "gpu_enabled": settings.dashboard_enable_gpu,
                "refresh_interval_seconds": settings.dashboard_metrics_interval_seconds,
            }

    def _refresh_machine_status(self) -> None:
        vm = psutil.virtual_memory()
        disk = shutil.disk_usage(settings.project_root)
        payload = {
            "cpu_percent": psutil.cpu_percent(interval=0.1),
            "memory": {
                "total_bytes": vm.total,
                "available_bytes": vm.available,
                "used_bytes": vm.used,
                "percent": vm.percent,
            },
            "disk": {
                "total_bytes": disk.total,
                "used_bytes": disk.used,
                "free_bytes": disk.free,
                "percent": round((disk.used / disk.total) * 100, 2) if disk.total else 0,
            },
            "gpu": self._gpu_status() if settings.dashboard_enable_gpu else {"ok": False, "detail": "disabled"},
        }
        with self._metrics_lock:
            self._cached_machine_status = payload
            self._cached_machine_status_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def _gpu_status(self) -> dict[str, Any]:
        try:
            run_kwargs: dict[str, Any] = {
                "capture_output": True,
                "text": True,
                "timeout": 5,
                "check": True,
            }
            if os.name == "nt":
                run_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            completed = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                **run_kwargs,
            )
        except FileNotFoundError:
            return {"ok": False, "detail": "nvidia-smi-not-found"}
        except subprocess.CalledProcessError as exc:
            return {"ok": False, "detail": exc.stderr.strip() or exc.stdout.strip() or "nvidia-smi-failed"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "detail": "nvidia-smi-timeout"}

        gpus: list[dict[str, Any]] = []
        for line in completed.stdout.splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) != 5:
                continue
            gpus.append(
                {
                    "name": parts[0],
                    "memory_total_mb": int(parts[1]),
                    "memory_used_mb": int(parts[2]),
                    "utilization_percent": int(parts[3]),
                    "temperature_c": int(parts[4]),
                }
            )
        return {"ok": True, "gpus": gpus}

    def _tail(self, path: Path, *, limit: int = 60) -> str:
        if not path.exists():
            return ""
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-limit:])

    def _recent_timings(self, limit: int = 12) -> dict[str, Any]:
        log_path = settings.data_dir / "runtime" / "conversation.timings.jsonl"
        if not log_path.exists():
            return {"entries": [], "summary": {"count": 0}}
        entries: list[dict[str, Any]] = []
        for line in reversed(log_path.read_text(encoding="utf-8", errors="replace").splitlines()):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entries.append(json.loads(stripped))
            except json.JSONDecodeError:
                continue
            if len(entries) >= limit:
                break
        entries.reverse()
        totals = [entry.get("timing_ms", {}).get("total_ms") for entry in entries if isinstance(entry.get("timing_ms", {}).get("total_ms"), (int, float))]
        summary = {
            "count": len(entries),
            "avg_total_ms": round(sum(totals) / len(totals), 1) if totals else None,
            "max_total_ms": round(max(totals), 1) if totals else None,
            "min_total_ms": round(min(totals), 1) if totals else None,
        }
        return {"entries": entries, "summary": summary}

    def _api_headers(self) -> dict[str, str]:
        if settings.api_auth_enabled and settings.api_bearer_token:
            return {"Authorization": f"Bearer {settings.api_bearer_token}"}
        return {}
