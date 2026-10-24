from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import psutil
from fastapi import UploadFile

from ..performer.bus import PerformerEventBus, get_performer_event_bus
from ..performer.models import PerformerOutputEvent
from ..config import load_desired_tts_selection, settings
from ..schemas.voice import LiveVoiceJobResponse
from ..system.python_runtime import resolve_python_executable
from .uploads import save_stream


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class LiveVoiceJob:
    turn_id: str
    session_id: str | None
    synthesize_speech: bool
    response_mode: str
    input_path: Path
    output_path: Path
    status_path: Path
    stdout_log: Path
    stderr_log: Path
    created_at: str
    process: subprocess.Popen[str] | None = None
    cancel_requested_at: str | None = None
    cancel_reason: str | None = None
    completed_at: str | None = None
    performer_started: bool = False
    performer_subtitle_published: bool = False
    performer_speech_started: bool = False
    performer_completed: bool = False
    performer_published_chunks: set[int] | None = None
    performer_last_status: str | None = None


class LiveVoiceJobManager:
    history_rotate_bytes = 5 * 1024 * 1024

    def __init__(self, performer_bus: PerformerEventBus | None = None) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, LiveVoiceJob] = {}
        self._runtime_dir = settings.data_dir / "runtime" / "live_jobs"
        self._runtime_dir.mkdir(parents=True, exist_ok=True)
        self._history_path = self._runtime_dir / "history.current.jsonl"
        self._performer_bus = performer_bus or get_performer_event_bus()

    def _core_runtime(self):
        # Compatibility callers share the API-owned runtime and its cancellation.
        from ..api.routes import get_live_turn_runtime
        return get_live_turn_runtime()

    async def start_job(
        self,
        *,
        audio_file: UploadFile,
        session_id: str | None,
        synthesize_speech: bool,
        response_mode: str = "simple_chunked",
        turn_id: str | None = None,
    ) -> LiveVoiceJobResponse:
        if turn_id is not None and (not isinstance(turn_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", turn_id)):
            raise ValueError("invalid turn_id")
        return await self._core_runtime().start_turn(audio_file=audio_file, session_id=session_id, synthesize_speech=synthesize_speech, response_mode=response_mode, turn_id=turn_id)

    def get_job(self, turn_id: str) -> LiveVoiceJobResponse:
        return self._core_runtime().get_turn(turn_id)

    def get_recent_history(self, *, limit: int = 20) -> list[dict[str, Any]]:
        return self._core_runtime().get_recent_history(limit=limit)

    def _publish_live_performer_events(
        self,
        job: LiveVoiceJob,
        *,
        status_payload: dict[str, Any],
        output_payload: dict[str, Any],
    ) -> None:
        raise RuntimeError("Legacy live JSON publication is retired; use the core live runtime")

    def _publish_performer_event(self, event_type: str, job: LiveVoiceJob, payload: dict[str, Any]) -> None:
        self._performer_bus.publish(
            PerformerOutputEvent(
                type=event_type,  # type: ignore[arg-type]
                turn_id=job.turn_id,
                input_event_id=job.turn_id,
                source="live_voice",
                target_policy="dashboard_monitor",
                payload=payload,
            )
        )

    @staticmethod
    def _live_response_emotion(output_payload: dict[str, Any]) -> str | None:
        stream = output_payload.get("stream")
        if not isinstance(stream, dict):
            return None
        response = stream.get("assistant_response")
        if not isinstance(response, dict):
            return None
        emotion = str(response.get("emotion") or "").strip()
        return emotion or None

    @staticmethod
    def _live_output_context(job: LiveVoiceJob, output_payload: dict[str, Any]) -> dict[str, Any]:
        stream = output_payload.get("stream")
        input_event = stream.get("input_event") if isinstance(stream, dict) else None
        if not isinstance(input_event, dict):
            return {
                "input": {
                    "kind": "mic_transcript",
                    "event_id": job.turn_id,
                    "session_id": job.session_id,
                },
                "actor": {
                    "source": "dashboard",
                    "platform_id": "live_voice",
                    "display_name": "Live Voice",
                    "roles": ["voice"],
                },
            }
        actor = input_event.get("actor") if isinstance(input_event.get("actor"), dict) else {}
        return {
            "input": {
                "kind": input_event.get("kind") or "mic_transcript",
                "event_id": input_event.get("event_id") or job.turn_id,
                "session_id": input_event.get("session_id") or job.session_id,
            },
            "actor": {
                "source": actor.get("source") or "dashboard",
                "platform_id": actor.get("platform_id") or "live_voice",
                "display_name": actor.get("display_name") or "Live Voice",
                "roles": actor.get("roles") if isinstance(actor.get("roles"), list) else ["voice"],
            },
        }

    def cancel_job(self, turn_id: str, *, reason: str = "interrupted") -> LiveVoiceJobResponse:
        return self._core_runtime().cancel_turn(turn_id, reason=reason)

    def cancel_active_jobs(self, *, reason: str = "interrupted") -> list[LiveVoiceJobResponse]:
        return self._core_runtime().cancel_active_turns(reason=reason)

    def _spawn_worker(
        self,
        *,
        turn_id: str,
        input_path: Path,
        output_path: Path,
        status_path: Path,
        session_id: str | None,
        synthesize_speech: bool,
        response_mode: str,
        stdout_log: Path,
        stderr_log: Path,
    ) -> subprocess.Popen[str]:
        command = [
            self._foreground_python(),
            "-m",
            "gamma.run_live_voice_worker",
            "--turn-id",
            turn_id,
            "--audio-path",
            str(input_path),
            "--output-path",
            str(output_path),
            "--status-path",
            str(status_path),
            "--synthesize-speech",
            "true" if synthesize_speech else "false",
            "--response-mode",
            response_mode,
        ]
        if session_id:
            command.extend(["--session-id", session_id])
        with stdout_log.open("ab") as stdout_handle, stderr_log.open("ab") as stderr_handle:
            kwargs: dict[str, Any] = {
                "cwd": settings.project_root,
                "stdout": stdout_handle,
                "stderr": stderr_handle,
                "env": self._worker_env(),
            }
            if os.name == "nt":
                kwargs["creationflags"] = (
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    | subprocess.DETACHED_PROCESS
                    | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                )
            else:
                kwargs["start_new_session"] = True
            return subprocess.Popen(command, **kwargs)

    def _selected_tts_env(self) -> dict[str, str]:
        selected = load_desired_tts_selection()
        provider = selected.get("tts_provider") or settings.tts_provider
        profile = selected.get("tts_profile") or settings.tts_profile
        env = {"SHANA_TTS_PROVIDER": provider}
        if profile:
            env["SHANA_TTS_PROFILE"] = profile
        else:
            env["SHANA_TTS_PROFILE"] = ""
        return env

    def _selected_stt_env(self) -> dict[str, str]:
        """Read STT config directly from environment (which includes .env).
        
        For CPU STT, omit SHANA_STT_DEVICE_INDEX entirely. For GPU, pass explicit index.
        """
        # Read STT config directly from environment (which includes .env)
        stt_device = os.environ.get("SHANA_STT_DEVICE", settings.stt_device or "cpu")
        env = {
            "SHANA_STT_PROVIDER": os.environ.get("SHANA_STT_PROVIDER", settings.stt_provider or "faster-whisper"),
            "SHANA_STT_MODEL": os.environ.get("SHANA_STT_MODEL", settings.stt_model or "base.en"),
            "SHANA_STT_DEVICE": stt_device,
            "SHANA_STT_COMPUTE_TYPE": os.environ.get("SHANA_STT_COMPUTE_TYPE", settings.stt_compute_type or "int8"),
        }
        # Only set device_index for GPU STT. For CPU, omit it entirely (ctranlate2 uses default 0).
        if not stt_device.lower().startswith("cpu"):
            env["SHANA_STT_DEVICE_INDEX"] = str(settings._as_int(os.environ.get("SHANA_STT_DEVICE_INDEX", settings.stt_device_index or 0), default=0))
        return env

    def _worker_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(self._selected_tts_env())
        env.update(self._selected_stt_env())
        
        # Ensure LD_LIBRARY_PATH includes CUDA runtime libraries ONLY when STT device is cuda
        # When STT device is cpu, no LD_LIBRARY_PATH needed; ctranslate2 uses system CUDA libs
        stt_device = env.get("SHANA_STT_DEVICE", "cpu").lower()
        cuda_lib_paths: list[str] = []
        
        if stt_device.startswith("cuda"):
            cuda_lib_paths.extend(["/usr/local/lib/ollama/cuda_v12/lib",
                                   "/usr/local/lib/ollama/cuda_v13/lib",
                                   "/home/neety/Documents/gamma-main/.venv/lib/python3.14/site-packages/nvidia/cu12/lib",
                                   "/home/neety/Documents/gamma-main/.venv/lib/python3.14/site-packages/nvidia/cu13/lib"])
            existing = os.environ.get("LD_LIBRARY_PATH", "").strip().split(":")
            new_paths = [p for p in cuda_lib_paths if p not in existing]
            if new_paths:
                existing.extend(new_paths)
            if existing:
                env["LD_LIBRARY_PATH"] = ":".join(existing)
        # NOTE: For CPU STT, no LD_LIBRARY_PATH is needed; ctranslate2 uses system CUDA libs
        return env

    async def _save_upload(self, turn_id: str, audio_file: UploadFile) -> Path:
        suffix = Path(audio_file.filename or "").suffix or ".wav"
        target = self._runtime_dir / f"{turn_id}{suffix}"
        return await save_stream(audio_file.file, target, max_bytes=settings.voice_upload_max_bytes)

    def _cleanup_completed_input(self, job: LiveVoiceJob) -> None:
        try:
            resolved_input = job.input_path.resolve()
            resolved_runtime = self._runtime_dir.resolve()
        except Exception:
            return
        if resolved_input.parent != resolved_runtime:
            return
        try:
            resolved_input.unlink(missing_ok=True)
        except Exception:
            pass

    def _require_job(self, turn_id: str) -> LiveVoiceJob:
        with self._lock:
            job = self._jobs.get(turn_id)
        if not job:
            raise KeyError(turn_id)
        return job

    def _refresh_job(self, job: LiveVoiceJob) -> None:
        if job.process is None:
            return
        returncode = job.process.poll()
        if returncode is None:
            return
        status_payload = self._read_status(job.status_path)
        if job.completed_at and status_payload.get("status") in {"completed", "cancelled", "failed"}:
            return
        if status_payload.get("status") not in {"completed", "cancelled", "failed"}:
            status = "failed" if returncode else "completed"
            self._write_status(
                job.status_path,
                {
                    **status_payload,
                    "status": status,
                    "completed_at": _utc_now(),
                    "error": status_payload.get("error") or ("worker exited non-zero" if returncode else None),
                },
            )
        if not job.completed_at:
            job.completed_at = _utc_now()
        self._maybe_log_history(job, status_payload, returncode)
        self._cleanup_completed_input(job)
        self._append_lifecycle_event(job.turn_id, "completed" if returncode == 0 else "failed", {"returncode": returncode})

    def _maybe_log_history(self, job: LiveVoiceJob, status_payload: dict[str, Any], returncode: int) -> None:
        if status_payload.get("history_logged"):
            return
        output_payload = self._read_json(job.output_path)
        status = str(status_payload.get("status") or ("failed" if returncode else "completed"))
        if status == "completed":
            entry = {
                "timestamp": _utc_now(),
                "turn_id": job.turn_id,
                "transcript": output_payload.get("transcript"),
                "reply_text": output_payload.get("reply_text"),
                "reply_chunks": output_payload.get("reply_chunks", []),
                "timing_ms": output_payload.get("timing_ms", {}),
                "job": {
                    "turn_id": job.turn_id,
                    "status": status,
                    "worker_pid": job.process.pid if job.process else None,
                    "completed_at": job.completed_at,
                },
            }
            self._append_history_entry(entry)
        elif status in {"failed", "cancelled"}:
            entry = {
                "timestamp": _utc_now(),
                "kind": "event",
                "label": status,
                "detail": "Live turn failed." if status == "failed" else "Live turn cancelled.",
                "job": {
                    "turn_id": job.turn_id,
                    "status": status,
                    "cancel_reason": status_payload.get("cancel_reason"),
                    "cancel_latency_ms": self._cancel_latency_ms(job.cancel_requested_at, status_payload.get("cancelled_at")),
                    "error": status_payload.get("error"),
                },
            }
            self._append_history_entry(entry)
        self._write_status(job.status_path, {**status_payload, "history_logged": True})

    def _read_status(self, path: Path) -> dict[str, Any]:
        payload = self._read_json(path)
        return payload if isinstance(payload, dict) else {}

    def _read_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_status(self, path: Path, payload: dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _append_lifecycle_event(self, turn_id: str, event: str, payload: dict[str, Any]) -> None:
        log_path = self._runtime_dir / "lifecycle.jsonl"
        line = json.dumps({"timestamp": _utc_now(), "turn_id": turn_id, "event": event, **payload}, ensure_ascii=False)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def _append_history_entry(self, payload: dict[str, Any]) -> None:
        self._rotate_history_if_needed()
        with self._history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _rotate_history_if_needed(self) -> None:
        if not self._history_path.exists():
            return
        try:
            size = self._history_path.stat().st_size
        except OSError:
            return
        if size < self.history_rotate_bytes:
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        rotated_path = self._runtime_dir / f"history.{stamp}.jsonl"
        suffix = 1
        while rotated_path.exists():
            rotated_path = self._runtime_dir / f"history.{stamp}.{suffix}.jsonl"
            suffix += 1
        self._history_path.replace(rotated_path)

    def _cancel_latency_ms(self, requested_at: str | None, cancelled_at: str | None) -> float | None:
        if not requested_at or not cancelled_at:
            return None
        try:
            start = datetime.fromisoformat(requested_at.replace("Z", "+00:00")).timestamp()
            end = datetime.fromisoformat(cancelled_at.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
        return round((end - start) * 1000, 1)

    def _kill_process_tree(self, pid: int) -> None:
        try:
            parent = psutil.Process(pid)
        except psutil.Error:
            return
        children = parent.children(recursive=True)
        for child in children:
            try:
                child.terminate()
            except psutil.Error:
                pass
        try:
            parent.terminate()
        except psutil.Error:
            pass
        gone, alive = psutil.wait_procs([*children, parent], timeout=5)
        for process in alive:
            try:
                process.kill()
            except psutil.Error:
                pass
        psutil.wait_procs(alive, timeout=3)

    def _foreground_python(self) -> str:
        return resolve_python_executable(settings.project_root)

    def _prune_finished_jobs(self, *, max_age_seconds: int = 900) -> None:
        cutoff = time.time() - max_age_seconds
        to_remove: list[str] = []
        with self._lock:
            items = list(self._jobs.items())
        for turn_id, job in items:
            self._refresh_job(job)
            if job.process is not None and job.process.poll() is None:
                continue
            if job.completed_at:
                try:
                    completed_ts = datetime.fromisoformat(job.completed_at.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    completed_ts = time.time()
                if completed_ts < cutoff:
                    to_remove.append(turn_id)
        if not to_remove:
            return
        with self._lock:
            for turn_id in to_remove:
                self._jobs.pop(turn_id, None)
