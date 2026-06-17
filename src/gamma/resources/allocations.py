from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SIDECAR_ALLOCATION_EVENT = "resource.sidecar_allocation.observed"


@dataclass(frozen=True, slots=True)
class ObservedSidecarAllocation:
    provider: str
    kind: str
    timestamp: str | None
    age_seconds: float | None
    stale: bool
    estimated_vram_mb: int
    observed_vram_mb: int
    allocation_delta_mb: int
    pid: int | None
    process_running: bool
    gpu_allocations: tuple[dict[str, Any], ...]
    gpu_process_match_count: int
    snapshot_sampled_at: str | None
    gpu_status: str | None

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.kind}"

    @property
    def fresh(self) -> bool:
        return not self.stale

    def as_payload(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "provider": self.provider,
            "kind": self.kind,
            "pid": self.pid,
            "process_running": self.process_running,
            "estimated_vram_mb": self.estimated_vram_mb,
            "observed_vram_mb": self.observed_vram_mb,
            "allocation_delta_mb": self.allocation_delta_mb,
            "gpu_allocations": list(self.gpu_allocations),
            "gpu_process_match_count": self.gpu_process_match_count,
            "snapshot_sampled_at": self.snapshot_sampled_at,
            "gpu_status": self.gpu_status,
            "age_seconds": self.age_seconds,
            "stale": self.stale,
            "fresh": self.fresh,
        }


def latest_sidecar_allocations(
    log_path: Path,
    *,
    ttl_seconds: int,
    now: datetime | None = None,
    limit: int | None = None,
) -> tuple[ObservedSidecarAllocation, ...]:
    if not log_path.exists():
        return ()
    current_time = now or datetime.now(timezone.utc)
    entries: list[ObservedSidecarAllocation] = []
    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            payload = _parse_line(line)
            if payload is None:
                continue
            entry = observed_sidecar_allocation_from_payload(
                payload,
                ttl_seconds=ttl_seconds,
                now=current_time,
            )
            if entry is not None:
                entries.append(entry)

    latest_by_key: dict[str, ObservedSidecarAllocation] = {}
    for entry in entries:
        latest_by_key[entry.key] = entry
    values = list(latest_by_key.values())
    if limit is not None:
        values = values[-max(1, limit):]
    return tuple(values)


def recent_sidecar_allocation_entries(
    log_path: Path,
    *,
    ttl_seconds: int,
    now: datetime | None = None,
    limit: int = 12,
) -> tuple[ObservedSidecarAllocation, ...]:
    if not log_path.exists():
        return ()
    current_time = now or datetime.now(timezone.utc)
    max_entries = max(1, limit)
    entries: list[ObservedSidecarAllocation] = []
    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            payload = _parse_line(line)
            if payload is None:
                continue
            entry = observed_sidecar_allocation_from_payload(
                payload,
                ttl_seconds=ttl_seconds,
                now=current_time,
            )
            if entry is not None:
                entries.append(entry)
                if len(entries) > max_entries:
                    entries.pop(0)
    return tuple(entries)


def observed_sidecar_allocation_from_payload(
    payload: dict[str, Any],
    *,
    ttl_seconds: int,
    now: datetime | None = None,
) -> ObservedSidecarAllocation | None:
    if payload.get("event") != SIDECAR_ALLOCATION_EVENT:
        return None
    provider = str(payload.get("provider") or "").strip().lower()
    kind = str(payload.get("kind") or "").strip().lower()
    if not provider or not kind:
        return None

    current_time = now or datetime.now(timezone.utc)
    timestamp = str(payload.get("timestamp") or "").strip() or None
    sampled_at = str(payload.get("snapshot_sampled_at") or "").strip() or None
    observed_at = _parse_datetime(timestamp) or _parse_datetime(sampled_at)
    age_seconds = None
    if observed_at is not None:
        age_seconds = max(0.0, (current_time - observed_at).total_seconds())
    stale = age_seconds is None or age_seconds > max(0, int(ttl_seconds))
    gpu_allocations = payload.get("gpu_allocations")
    allocation_payload = tuple(item for item in gpu_allocations if isinstance(item, dict)) if isinstance(gpu_allocations, list) else ()
    estimated = _as_int(payload.get("estimated_vram_mb"), default=0)
    observed = _as_int(payload.get("observed_vram_mb"), default=0)
    return ObservedSidecarAllocation(
        provider=provider,
        kind=kind,
        timestamp=timestamp,
        age_seconds=round(age_seconds, 1) if age_seconds is not None else None,
        stale=stale,
        estimated_vram_mb=max(0, estimated),
        observed_vram_mb=max(0, observed),
        allocation_delta_mb=observed - estimated,
        pid=_optional_int(payload.get("pid")),
        process_running=bool(payload.get("process_running")),
        gpu_allocations=allocation_payload,
        gpu_process_match_count=max(0, _as_int(payload.get("gpu_process_match_count"), default=len(allocation_payload))),
        snapshot_sampled_at=sampled_at,
        gpu_status=str(payload.get("gpu_status") or "").strip() or None,
    )


def _parse_line(line: str) -> dict[str, Any] | None:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
