from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .models import PlacementCandidate, PlacementDecision, RuntimeTarget, WorkloadSpec
from .runtime_registry import ResourceRoutingPolicy


def rank_placement_candidates(
    *,
    snapshot: dict[str, Any],
    workload: WorkloadSpec,
    targets: tuple[RuntimeTarget, ...],
    policy: ResourceRoutingPolicy,
) -> PlacementDecision:
    rejected: dict[str, str] = {}
    candidates: list[PlacementCandidate] = []
    snapshot_age = _snapshot_age_seconds(snapshot)

    if snapshot_age is None or snapshot_age > policy.snapshot_max_age_seconds:
        return PlacementDecision(
            workload=workload,
            selected=None,
            rejected={target.id: "snapshot_stale" for target in targets if target.id},
            snapshot_age_seconds=snapshot_age,
            status="snapshot_stale",
        )

    gpus = _gpus_by_identity(snapshot)
    for target in targets:
        reason = _rejection_reason(target, workload)
        if reason:
            rejected[target.id or "<unknown>"] = reason
            continue
        candidate = _candidate_for_target(target=target, workload=workload, gpus=gpus)
        if candidate is None:
            rejected[target.id] = "gpu_not_found"
            continue
        if candidate.projected_headroom_mb is not None and candidate.projected_headroom_mb < workload.minimum_headroom_mb:
            rejected[target.id] = "insufficient_vram_headroom"
            continue
        candidates.append(candidate)

    if not candidates:
        return PlacementDecision(
            workload=workload,
            selected=None,
            rejected=rejected,
            snapshot_age_seconds=snapshot_age,
            status="no_fit" if rejected else "no_target",
        )

    candidates.sort(key=lambda candidate: (-candidate.score, candidate.target.id))
    return PlacementDecision(
        workload=workload,
        selected=candidates[0],
        rejected=rejected,
        snapshot_age_seconds=snapshot_age,
        status="selected",
    )


def _rejection_reason(target: RuntimeTarget, workload: WorkloadSpec) -> str | None:
    if not target.id:
        return "missing_target_id"
    if not target.enabled:
        return "target_disabled"
    if not target.healthy:
        return "target_unhealthy"
    if target.provider != workload.provider:
        return "provider_mismatch"
    if workload.model and not target.models:
        return "models_missing"
    if target.models and workload.model and workload.model not in target.models:
        return "model_unavailable"
    if target.modalities and workload.modality not in target.modalities:
        return "modality_unavailable"
    return None


def _candidate_for_target(
    *,
    target: RuntimeTarget,
    workload: WorkloadSpec,
    gpus: dict[str, dict[str, Any]],
) -> PlacementCandidate | None:
    if target.device == "cpu":
        warm = bool(workload.model and workload.model in target.warm_models)
        return PlacementCandidate(
            target=target,
            score=100.0 + (1000.0 if warm else 0.0),
            reason="cpu-target",
            warm=warm,
        )

    gpu = _target_gpu(target, gpus)
    if gpu is None:
        return None
    free_vram = _int_value(gpu.get("memory_free_mb"))
    utilization = _int_value(gpu.get("utilization_percent"))
    projected_headroom = free_vram - max(0, target.reserved_vram_mb) - max(0, workload.estimated_vram_mb)
    warm = bool(workload.model and workload.model in target.warm_models)
    score = float(projected_headroom) - (utilization * 10.0) + (1000.0 if warm else 0.0)
    return PlacementCandidate(
        target=target,
        score=score,
        reason="gpu-headroom",
        gpu_index=_int_value(gpu.get("index")),
        gpu_uuid=str(gpu.get("uuid") or "") or None,
        free_vram_mb=free_vram,
        projected_headroom_mb=projected_headroom,
        warm=warm,
    )


def _target_gpu(target: RuntimeTarget, gpus: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if target.gpu_uuid:
        return gpus.get(target.gpu_uuid)
    if target.device.startswith("cuda:"):
        return gpus.get(target.device.split(":", 1)[1])
    if target.device == "cuda":
        return gpus.get("0")
    return None


def _gpus_by_identity(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    gpu_payload = snapshot.get("gpu") if isinstance(snapshot, dict) else {}
    if not isinstance(gpu_payload, dict) or not gpu_payload.get("ok"):
        return {}
    gpus: dict[str, dict[str, Any]] = {}
    for item in gpu_payload.get("gpus", []):
        if not isinstance(item, dict):
            continue
        index = str(item.get("index", "")).strip()
        uuid = str(item.get("uuid", "")).strip()
        if index:
            gpus[index] = item
        if uuid:
            gpus[uuid] = item
    return gpus


def _snapshot_age_seconds(snapshot: dict[str, Any]) -> float | None:
    sampled_at = str(snapshot.get("sampled_at") or "").strip()
    if not sampled_at:
        return None
    try:
        parsed = datetime.fromisoformat(sampled_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds())


def _int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
