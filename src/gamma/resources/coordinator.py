from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..config import settings
from .models import PlacementDecision, WorkloadSpec
from .policy import rank_placement_candidates
from .probe import MachineResourceMonitor
from .runtime_registry import ResourceRoutingRegistry, load_resource_routing_registry


@dataclass(slots=True)
class _AdvisoryReservation:
    id: str
    target_id: str
    vram_mb: int
    expires_at_monotonic: float
    expires_at_iso: str


class ResourcePlacementCoordinator:
    _reservation_lock = threading.Lock()
    _advisory_reservations: dict[str, _AdvisoryReservation] = {}

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
        return self._rank(workload, reserve=False)

    def rank_and_reserve(self, workload: WorkloadSpec) -> PlacementDecision:
        return self._rank(workload, reserve=True)

    @classmethod
    def release_advisory_reservation(cls, reservation_id: str | None) -> None:
        if not reservation_id:
            return
        with cls._reservation_lock:
            cls._advisory_reservations.pop(reservation_id, None)

    @classmethod
    def clear_advisory_reservations(cls) -> None:
        with cls._reservation_lock:
            cls._advisory_reservations.clear()

    def _rank(self, workload: WorkloadSpec, *, reserve: bool) -> PlacementDecision:
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
        active_reservations = self._active_reservations_by_target()
        decision = rank_placement_candidates(
            snapshot=snapshot,
            workload=workload,
            targets=registry.targets,
            policy=registry.policy,
            advisory_reservations_mb=active_reservations if registry.policy.shadow_mode else {},
        )
        if reserve and registry.policy.shadow_mode and decision.selected is not None:
            return self._with_advisory_reservation(decision, ttl_seconds=registry.policy.reservation_ttl_seconds)
        return decision

    @classmethod
    def _active_reservations_by_target(cls) -> dict[str, int]:
        now = time.monotonic()
        totals: dict[str, int] = {}
        with cls._reservation_lock:
            expired = [reservation_id for reservation_id, item in cls._advisory_reservations.items() if item.expires_at_monotonic <= now]
            for reservation_id in expired:
                cls._advisory_reservations.pop(reservation_id, None)
            for item in cls._advisory_reservations.values():
                totals[item.target_id] = totals.get(item.target_id, 0) + max(0, item.vram_mb)
        return totals

    @classmethod
    def _with_advisory_reservation(cls, decision: PlacementDecision, *, ttl_seconds: int) -> PlacementDecision:
        selected = decision.selected
        if selected is None or ttl_seconds <= 0:
            return decision
        reservation_id = uuid.uuid4().hex
        now_monotonic = time.monotonic()
        expires_at_iso = datetime.fromtimestamp(time.time() + ttl_seconds, timezone.utc).isoformat().replace("+00:00", "Z")
        item = _AdvisoryReservation(
            id=reservation_id,
            target_id=selected.target.id,
            vram_mb=max(0, decision.workload.estimated_vram_mb),
            expires_at_monotonic=now_monotonic + ttl_seconds,
            expires_at_iso=expires_at_iso,
        )
        with cls._reservation_lock:
            cls._advisory_reservations[reservation_id] = item
        return PlacementDecision(
            workload=decision.workload,
            selected=decision.selected,
            rejected=decision.rejected,
            snapshot_age_seconds=decision.snapshot_age_seconds,
            status=decision.status,
            reservation_id=reservation_id,
            reservation_expires_at=expires_at_iso,
            reservation_ttl_seconds=ttl_seconds,
        )


def llm_shadow_placement_payload(
    *,
    provider: str,
    model: str | None,
    route_family: str,
    has_images: bool,
    coordinator: ResourcePlacementCoordinator | None = None,
    reserve: bool = True,
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
    decision = placement.rank_and_reserve(workload) if reserve else placement.rank(workload)
    return decision.as_payload()


def release_advisory_reservation(reservation_id: str | None) -> None:
    ResourcePlacementCoordinator.release_advisory_reservation(reservation_id)
