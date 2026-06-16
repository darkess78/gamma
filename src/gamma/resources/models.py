from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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
    warm: bool = False

    def as_payload(self) -> dict[str, Any]:
        return {
            "target_id": self.target.id,
            "provider": self.target.provider,
            "kind": self.target.kind,
            "device": self.target.device,
            "gpu_index": self.gpu_index,
            "gpu_uuid": self.gpu_uuid,
            "score": round(self.score, 3),
            "reason": self.reason,
            "free_vram_mb": self.free_vram_mb,
            "projected_headroom_mb": self.projected_headroom_mb,
            "warm": self.warm,
        }


@dataclass(frozen=True, slots=True)
class PlacementDecision:
    workload: WorkloadSpec
    selected: PlacementCandidate | None
    rejected: dict[str, str] = field(default_factory=dict)
    snapshot_age_seconds: float | None = None
    status: str = "no_target"

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
        }
