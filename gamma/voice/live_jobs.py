from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import psutil
from fastapi import UploadFile

from ..config import settings
from ..schemas.voice import LiveVoiceJobResponse


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


class LiveVoiceJobManager:
    history_rotate_bytes = 5 * 1024 * 1024

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, LiveVoiceJob] = {}
        self._runtime_dir = settings.data_dir / "runtime" / "live_jobs"
        self._runtime_dir.mkdir(parents=True, exist_ok=True)
        self._history_path = self._runtime_dir / "history.current.jsonl"

    async def start_job(
        self,
        *,
        audio_file: UploadFile,
        session_id: str | None,
        synthesize_speech: bool,
        response_mode: str = "simple_chunked",
        turn_id: str | None = None,
    ) -> LiveVoiceJobResponse:
        self._prune_finished_jobs()
        actual_turn_id = turn_id or uuid4().hex
        input_path = await self._save_upload(actual_turn_id, audio_file)
        output_path = self._runtime_dir / f"{actual_turn_id}.result.json"
        status_path = self._runtime_dir / f"{actual_turn_id}.status.json"
        stdout_log = self._runtime_dir / f"{actual_turn_id}.stdout.log"
        stderr_log = self._runtime_dir / f"{actual_turn_id}.stderr.log"
        stdout_log.write_text("", encoding="utf-8")
        stderr_log.write_text("", encoding="utf-8")
        self._write_status(
            status_path,
            {
                "turn_id": actual_turn_id,
                "status": "queued",
                "session_id": session_id,
                "synthesize_speech": synthesize_speech,
                "response_mode": response_mode,
                "created_at": _utc_now(),
            },
        )

        process = self._spawn_worker(
            turn_id=actual_turn_id,
            input_path=input_path,
            output_path=output_path,
            status_path=status_path,
            session_id=session_id,
            synthesize_speech=synthesize_speech,
            response_mode=response_mode,
            stdout_log=stdout_log,
            stderr_log=stderr_log,
        )
        created_at = _utc_now()
        job = LiveVoiceJob(
            turn_id=actual_turn_id,
            session_id=session_id,
            synthesize_speech=synthesize_speech,
            response_mode=response_mode,
            input_path=input_path,
            output_path=output_path,
            status_path=status_path,
            stdout_log=stdout_log,
            stderr_log=stderr_log,
            created_at=created_at,
            process=process,
        )
        with self._lock:
            self._jobs[actual_turn_id] = job
        self._append_lifecycle_event(actual_turn_id, "created", {"pid": process.pid, "session_id": session_id})
        return self.get_job(actual_turn_id)

    def get_job(self, turn_id: str) -> LiveVoiceJobResponse:
        job = self._require_job(turn_id)
        self._refresh_job(job)
        status_payload = self._read_status(job.status_path)
        output_payload = self._read_json(job.output_path)
        return LiveVoiceJobResponse(
            turn_id=turn_id,
            status=str(status_payload.get("status", "failed")),
            session_id=job.session_id,
            synthesize_speech=job.synthesize_speech,
            response_mode=job.response_mode,
            worker_pid=job.process.pid if job.process else None,
            transcript=output_payload.get("transcript"),
            reply_text=output_payload.get("reply_text"),
            reply_chunks=output_payload.get("reply_chunks", []),
            audio_content_type=output_payload.get("audio_content_type"),
            audio_base64=output_payload.get("audio_base64"),
            timing_ms=output_payload.get("timing_ms", {}),
            created_at=status_payload.get("created_at") or job.created_at,
            started_at=status_payload.get("started_at"),
            completed_at=status_payload.get("completed_at") or job.completed_at,
            cancel_requested_at=job.cancel_requested_at,
            cancelled_at=status_payload.get("cancelled_at"),
            cancel_latency_ms=self._cancel_latency_ms(job.cancel_requested_at, status_payload.get("cancelled_at")),
            cancel_reason=job.cancel_reason or status_payload.get("cancel_reason"),
            error=status_payload.get("error"),
        )

    def get_recent_history(self, *, limit: int = 20) -> list[dict[str, Any]]:
        if not self._history_path.exists():
            return []
        entries: list[dict[str, Any]] = []
        try:
            with self._history_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(payload, dict):
                        entries.append(payload)
        except Exception:
            return []
        return entries[-max(1, limit):]

    def cancel_job(self, turn_id: str, *, reason: str = "interrupted") -> LiveVoiceJobResponse:
        job = self._require_job(turn_id)
        self._refresh_job(job)
        current = self._read_status(job.status_path)
        if current.get("status") in {"completed", "cancelled", "failed"}:
            return self.get_job(turn_id)

        cancel_requested_at = _utc_now()
        job.cancel_requested_at = cancel_requested_at
        job.cancel_reason = reason
        self._write_status(
            job.status_path,
            {
                **current,
                "status": "cancelled",
                "cancel_requested_at": cancel_requested_at,
                "cancelled_at": _utc_now(),
                "cancel_reason": reason,
                "history_logged": True,
            },
        )
        if job.process is not None and job.process.poll() is None:
            self._kill_process_tree(job.process.pid)
        job.completed_at = _utc_now()
        self._append_lifecycle_event(turn_id, "cancelled", {"reason": reason, "pid": job.process.pid if job.process else None})
        self._append_history_entry(
            {
                "timestamp": _utc_now(),
                "kind": "event",
                "label": "cancelled",
                "detail": "Live turn cancelled.",
                "job": {
                    "turn_id": turn_id,
                    "cancel_reason": reason,
                    "cancel_latency_ms": self._cancel_latency_ms(cancel_requested_at, _utc_now()),
                },
            }
        )
        return self.get_job(turn_id)

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

    async def _save_upload(self, turn_id: str, audio_file: UploadFile) -> Path:
        suffix = Path(audio_file.filename or "").suffix or ".wav"
        target = self._runtime_dir / f"{turn_id}{suffix}"
        content = await audio_file.read()
        target.write_bytes(content)
        return target

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
        candidates = [
            os.getenv("SHANA_PYTHON"),
            sys.executable,
            str(settings.project_root / ".venv" / "bin" / "python"),
            str(settings.project_root / ".venv" / "Scripts" / "python.exe"),
            str(settings.project_root / ".venv312" / "Scripts" / "python.exe"),
            str(Path.home() / "AppData" / "Local" / "Programs" / "Python" / "Python312" / "python.exe"),
        ]
        for raw in candidates:
            if not raw:
                continue
            path = Path(raw).expanduser()
            if path.exists():
                return str(path.resolve())
        return sys.executable

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
