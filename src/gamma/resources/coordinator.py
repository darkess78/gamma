from __future__ import annotations

from typing import Any

from ..config import settings
from .models import PlacementDecision, WorkloadSpec
from .policy import rank_placement_candidates
from .probe import MachineResourceMonitor
from .runtime_registry import ResourceRoutingRegistry, load_resource_routing_registry


class ResourcePlacementCoordinator:
    def __init__(
        self,
        *,
        registry: ResourceRoutingRegistry | None = None,
        monitor: MachineResourceMonitor | None = None,
    ) -> None:
        self._registry = registry
        self._monitor = monitor or MachineResourceMonitor(
            project_root=settings.project_root,
            enable_gpu=lambda: settings.dashboard_enable_gpu,
            refresh_interval_seconds=lambda: settings.dashboard_metrics_interval_seconds,
        )

    def shadow_enabled(self) -> bool:
        registry = self._registry or load_resource_routing_registry()
        return registry.policy.shadow_mode and bool(registry.targets)

    def rank(self, workload: WorkloadSpec) -> PlacementDecision:
        registry = self._registry or load_resource_routing_registry()
        snapshot = self._monitor.dashboard_payload()
        workload = WorkloadSpec(
            id=workload.id,
            kind=workload.kind,
            provider=workload.provider,
            model=workload.model,
            modality=workload.modality,
            estimated_vram_mb=workload.estimated_vram_mb or registry.policy.default_estimated_vram_mb,
            minimum_headroom_mb=workload.minimum_headroom_mb or registry.policy.minimum_headroom_mb,
        )
        return rank_placement_candidates(
            snapshot=snapshot,
            workload=workload,
            targets=registry.targets,
            policy=registry.policy,
        )


def llm_shadow_placement_payload(
    *,
    provider: str,
    model: str | None,
    route_family: str,
    has_images: bool,
    coordinator: ResourcePlacementCoordinator | None = None,
) -> dict[str, Any] | None:
    placement = coordinator or ResourcePlacementCoordinator()
    if not placement.shadow_enabled():
        return None
    workload = WorkloadSpec(
        id=f"llm:{route_family}",
        kind="llm",
        provider=provider,
        model=model,
        modality="vision" if has_images else "text",
    )
    return placement.rank(workload).as_payload()
