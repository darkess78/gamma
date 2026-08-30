from __future__ import annotations

import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


WorkStatus = Literal[
    "queued",
    "running",
    "paused",
    "review_ready",
    "rejected",
    "exhausted",
    "failed",
    "stopped",
]
DesiredState = Literal["running", "paused", "stopped"]

_WORK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{7,79}$")
_DOMAIN_RE = re.compile(r"^[a-z][a-z0-9_-]{1,79}$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,199}$")
_TERMINAL_STATUSES = {"review_ready", "rejected", "exhausted", "failed", "stopped"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class WorkEvent(BaseModel):
    at: str = Field(default_factory=utc_now)
    stage: str = Field(min_length=2, max_length=80)
    message: str = Field(min_length=2, max_length=500)
    code: str | None = Field(default=None, max_length=120)


class ImprovementWorkRequest(BaseModel):
    """Durable owner authority for one bounded autonomous improvement run."""

    version: Literal[1] = 1
    id: str
    goal: str = Field(min_length=12, max_length=1200)
    selection_mode: Literal["directed", "automatic"] = "directed"
    focus_domains: tuple[str, ...] = ()
    models: tuple[str, ...]
    budget_minutes: int = Field(default=480, ge=15, le=720)
    maximum_cycles: int = Field(default=3, ge=1, le=5)
    maximum_attempts_per_series: int = Field(default=6, ge=1, le=10)
    status: WorkStatus = "queued"
    desired_state: DesiredState = "running"
    stage: str = Field(default="queued", min_length=2, max_length=80)
    cycle_count: int = Field(default=0, ge=0, le=5)
    current_series_id: str | None = Field(default=None, max_length=80)
    result_summary: str | None = Field(default=None, max_length=700)
    reason_codes: tuple[str, ...] = ()
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    started_at: str | None = None
    completed_at: str | None = None
    events: tuple[WorkEvent, ...] = ()
    promotion_authority: Literal[False] = False

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not _WORK_ID_RE.fullmatch(value):
            raise ValueError("invalid improvement work id")
        return value

    @field_validator("goal")
    @classmethod
    def validate_goal(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if any(ord(character) < 32 for character in normalized):
            raise ValueError("goal contains control characters")
        return normalized

    @field_validator("focus_domains")
    @classmethod
    def validate_domains(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(value.strip().lower() for value in values if value.strip()))
        if len(normalized) > 6 or any(not _DOMAIN_RE.fullmatch(value) for value in normalized):
            raise ValueError("invalid focus domain")
        return normalized

    @field_validator("models")
    @classmethod
    def validate_models(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(value.strip() for value in values if value.strip()))
        if not 2 <= len(normalized) <= 3:
            raise ValueError("work requests require two or three unique models")
        if any(not _MODEL_RE.fullmatch(value) for value in normalized):
            raise ValueError("invalid model name")
        return normalized

    @model_validator(mode="after")
    def validate_state(self) -> "ImprovementWorkRequest":
        if self.status in _TERMINAL_STATUSES and not self.completed_at:
            raise ValueError("terminal work request requires completed_at")
        if self.cycle_count > self.maximum_cycles:
            raise ValueError("cycle count exceeds request limit")
        return self

    def public_summary(self) -> dict[str, object]:
        return {
            "id": self.id,
            "goal": self.goal,
            "selection_mode": self.selection_mode,
            "focus_domains": list(self.focus_domains),
            "models": list(self.models),
            "budget_minutes": self.budget_minutes,
            "maximum_cycles": self.maximum_cycles,
            "maximum_attempts_per_series": self.maximum_attempts_per_series,
            "status": self.status,
            "desired_state": self.desired_state,
            "stage": self.stage,
            "cycle_count": self.cycle_count,
            "current_series_id": self.current_series_id,
            "result_summary": self.result_summary,
            "reason_codes": list(self.reason_codes),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "events": [event.model_dump(mode="json") for event in self.events[-30:]],
            "promotion_authority": False,
        }


class ImprovementWorkStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.requests_root = root / "requests"
        self.lock_path = root / "queue.lock"

    def create(
        self,
        *,
        goal: str,
        selection_mode: Literal["directed", "automatic"],
        focus_domains: tuple[str, ...],
        models: tuple[str, ...],
        budget_minutes: int,
        maximum_cycles: int,
        maximum_attempts_per_series: int,
    ) -> ImprovementWorkRequest:
        identifier = f"work-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        request = ImprovementWorkRequest(
            id=identifier,
            goal=goal,
            selection_mode=selection_mode,
            focus_domains=focus_domains,
            models=models,
            budget_minutes=budget_minutes,
            maximum_cycles=maximum_cycles,
            maximum_attempts_per_series=maximum_attempts_per_series,
            events=(WorkEvent(stage="queued", message="Owner-authorized bounded work request queued."),),
        )
        with self._lock():
            self.requests_root.mkdir(parents=True, exist_ok=True)
            self._write(request, exclusive=True)
        return request

    def load(self, request_id: str) -> ImprovementWorkRequest:
        path = self._path(request_id)
        return ImprovementWorkRequest.model_validate_json(path.read_text(encoding="utf-8"))

    def list(self, *, limit: int = 20) -> list[ImprovementWorkRequest]:
        if not self.requests_root.is_dir():
            return []
        requests: list[ImprovementWorkRequest] = []
        for path in self.requests_root.glob("*.json"):
            if path.is_symlink() or path.stat().st_size > 512_000:
                continue
            try:
                requests.append(ImprovementWorkRequest.model_validate_json(path.read_text(encoding="utf-8")))
            except (OSError, UnicodeError, ValueError):
                continue
        requests.sort(key=lambda item: item.created_at, reverse=True)
        return requests[: max(1, min(limit, 100))]

    def next_queued(self) -> ImprovementWorkRequest | None:
        queued = [
            item
            for item in self.list(limit=100)
            if item.status in {"queued", "running"} and item.desired_state == "running"
        ]
        return min(queued, key=lambda item: item.created_at) if queued else None

    def mutate(
        self,
        request_id: str,
        operation: Callable[[ImprovementWorkRequest], ImprovementWorkRequest],
    ) -> ImprovementWorkRequest:
        with self._lock():
            current = self.load(request_id)
            updated = operation(current)
            updated.updated_at = utc_now()
            self._write(updated)
            return updated

    def control(
        self,
        request_id: str,
        action: Literal["pause", "resume", "stop", "reject"],
    ) -> ImprovementWorkRequest:
        def apply(request: ImprovementWorkRequest) -> ImprovementWorkRequest:
            if action == "reject":
                if request.status != "review_ready":
                    raise ValueError("only review-ready work may be rejected")
                request.status = "rejected"
                request.desired_state = "stopped"
                request.stage = "rejected"
                request.result_summary = (
                    "The isolated candidate was rejected during owner review; all evidence was retained."
                )
                request.reason_codes = tuple(
                    dict.fromkeys((*request.reason_codes, "owner_rejected_candidate"))
                )
                request.events = (
                    *request.events,
                    WorkEvent(
                        stage="rejected",
                        code="owner_rejected_candidate",
                        message="Review-ready candidate rejected; evidence retained and nothing promoted.",
                    ),
                )[-50:]
                return request
            if request.status in _TERMINAL_STATUSES:
                raise ValueError("completed work cannot be controlled")
            if action == "pause":
                request.desired_state = "paused"
                if request.status == "queued":
                    request.status = "paused"
                    request.stage = "paused"
                request.events = (*request.events, WorkEvent(stage=request.stage, message="Pause requested."))[-50:]
            elif action == "resume":
                request.desired_state = "running"
                if request.status == "paused":
                    request.status = "queued"
                    request.stage = "queued"
                request.events = (*request.events, WorkEvent(stage=request.stage, message="Resume requested."))[-50:]
            else:
                request.desired_state = "stopped"
                if request.status in {"queued", "paused"}:
                    request.status = "stopped"
                    request.stage = "stopped"
                    request.completed_at = utc_now()
                request.events = (*request.events, WorkEvent(stage=request.stage, message="Stop requested."))[-50:]
            return request

        return self.mutate(request_id, apply)

    def _path(self, request_id: str) -> Path:
        if not _WORK_ID_RE.fullmatch(request_id):
            raise ValueError("invalid improvement work id")
        return self.requests_root / f"{request_id}.json"

    def _write(self, request: ImprovementWorkRequest, *, exclusive: bool = False) -> None:
        path = self._path(request.id)
        payload = json.dumps(request.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
        if exclusive:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", errors="strict") as handle:
                handle.write(payload)
            return
        temporary = path.with_suffix(f".tmp-{os.getpid()}")
        temporary.write_text(payload, encoding="utf-8", errors="strict")
        os.replace(temporary, path)

    @contextmanager
    def _lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + 3.0
        descriptor: int | None = None
        while descriptor is None:
            try:
                descriptor = os.open(self.lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                if self._lock_is_stale():
                    try:
                        self.lock_path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                if time.monotonic() >= deadline:
                    raise TimeoutError("improvement queue is busy")
                time.sleep(0.025)
        try:
            os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
            os.close(descriptor)
            descriptor = None
            yield
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                self.lock_path.unlink()
            except FileNotFoundError:
                pass

    def _lock_is_stale(self) -> bool:
        age = 0.0
        try:
            age = time.time() - self.lock_path.stat().st_mtime
            if age <= 30:
                return False
            raw_pid = self.lock_path.read_text(encoding="ascii").strip()
            pid = int(raw_pid)
            os.kill(pid, 0)
            return False
        except (FileNotFoundError, ProcessLookupError):
            return True
        except (OSError, ValueError):
            return age > 300
