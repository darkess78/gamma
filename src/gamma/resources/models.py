from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class RuntimeEndpoint:
    id: str
    url: str
    kind: str = ""
    provider: str = ""

    def as_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "kind": self.kind,
            "provider": self.provider,
        }


@dataclass(frozen=True, slots=True)
class RuntimeTarget:
    id: str
    kind: str
    provider: str
    endpoint_ref: str = ""
    device: str = ""
    gpu_uuid: str = ""
    models: tuple[str, ...] = ()
    modalities: tuple[str, ...] = ("text",)
    enabled: bool = True
    healthy: bool = True
    managed: bool = False
    warm_models: tuple[str, ...] = ()
    reserved_vram_mb: int = 0


@dataclass(frozen=True, slots=True)
class WorkloadSpec:
    id: str
    kind: str
    provider: str
    model: str | None = None
    modality: str = "text"
    estimated_vram_mb: int = 0
    minimum_headroom_mb: int = 0


@dataclass(frozen=True, slots=True)
class PlacementCandidate:
    target: RuntimeTarget
    score: float
    reason: str
    gpu_index: int | None = None
    gpu_uuid: str | None = None
    free_vram_mb: int | None = None
    projected_headroom_mb: int | None = None
    advisory_reserved_vram_mb: int = 0
    warm: bool = False

    def as_payload(self) -> dict[str, Any]:
        return {
            "target_id": self.target.id,
            "provider": self.target.provider,
            "kind": self.target.kind,
            "endpoint_ref": self.target.endpoint_ref,
            "device": self.target.device,
            "gpu_index": self.gpu_index,
            "gpu_uuid": self.gpu_uuid,
            "score": round(self.score, 3),
            "reason": self.reason,
            "free_vram_mb": self.free_vram_mb,
            "projected_headroom_mb": self.projected_headroom_mb,
            "advisory_reserved_vram_mb": self.advisory_reserved_vram_mb,
            "warm": self.warm,
        }


@dataclass(frozen=True, slots=True)
class PlacementDecision:
    workload: WorkloadSpec
    selected: PlacementCandidate | None
    rejected: dict[str, str] = field(default_factory=dict)
    snapshot_age_seconds: float | None = None
    status: str = "no_target"
    reservation_id: str | None = None
    reservation_expires_at: str | None = None
    reservation_ttl_seconds: int | None = None

    def as_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "workload_id": self.workload.id,
            "workload_kind": self.workload.kind,
            "provider": self.workload.provider,
            "model": self.workload.model,
            "selected": self.selected.as_payload() if self.selected else None,
            "rejected": self.rejected,
            "snapshot_age_seconds": self.snapshot_age_seconds,
            "reservation_id": self.reservation_id,
            "reservation_expires_at": self.reservation_expires_at,
            "reservation_ttl_seconds": self.reservation_ttl_seconds,
        }
