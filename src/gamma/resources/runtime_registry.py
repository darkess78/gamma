from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..config import load_app_file_config
from .models import RuntimeTarget


@dataclass(frozen=True, slots=True)
class ResourceRoutingPolicy:
    shadow_mode: bool = False
    snapshot_max_age_seconds: int = 5
    minimum_headroom_mb: int = 2048
    default_estimated_vram_mb: int = 0


@dataclass(frozen=True, slots=True)
class ResourceRoutingRegistry:
    policy: ResourceRoutingPolicy
    targets: tuple[RuntimeTarget, ...]
    validation_errors: tuple[str, ...] = ()


def load_resource_routing_registry(config: dict[str, Any] | None = None) -> ResourceRoutingRegistry:
    app_config = config if config is not None else load_app_file_config()
    root = app_config.get("resource_routing", {})
    if not isinstance(root, dict):
        root = {}
    policy = _policy(root.get("policy", {}))
    raw_targets = root.get("targets", [])
    targets, validation_errors = _targets(raw_targets)
    return ResourceRoutingRegistry(policy=policy, targets=targets, validation_errors=validation_errors)


def _policy(value: Any) -> ResourceRoutingPolicy:
    payload = value if isinstance(value, dict) else {}
    return ResourceRoutingPolicy(
        shadow_mode=_as_bool(payload.get("shadow_mode", False)),
        snapshot_max_age_seconds=_as_int(payload.get("snapshot_max_age_seconds", 5), default=5),
        minimum_headroom_mb=_as_int(payload.get("minimum_headroom_mb", 2048), default=2048),
        default_estimated_vram_mb=_as_int(payload.get("default_estimated_vram_mb", 0), default=0),
    )


def _targets(value: Any) -> tuple[tuple[RuntimeTarget, ...], tuple[str, ...]]:
    if not isinstance(value, list):
        if value:
            return (), ("resource_routing.targets must be a list",)
        return (), ()
    targets: list[RuntimeTarget] = []
    errors: list[str] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            errors.append(f"resource_routing.targets[{index}] must be an object")
            continue
        target = _target(item)
        target_errors = _target_validation_errors(target, index=index, seen_ids=seen_ids)
        errors.extend(target_errors)
        if target_errors:
            continue
        seen_ids.add(target.id)
        targets.append(target)
    return tuple(targets), tuple(errors)


def _target(value: dict[str, Any]) -> RuntimeTarget:
    kind = str(value.get("kind", "ollama") or "ollama").strip().lower()
    provider = str(value.get("provider", "") or "").strip().lower() or ("local" if kind == "ollama" else kind)
    return RuntimeTarget(
        id=str(value.get("id", "") or "").strip(),
        kind=kind,
        provider=provider,
        endpoint_ref=str(value.get("endpoint_ref", "") or "").strip(),
        device=str(value.get("device", "") or "").strip().lower(),
        gpu_uuid=str(value.get("gpu_uuid", "") or "").strip(),
        models=_string_tuple(value.get("models", ())),
        modalities=_string_tuple(value.get("modalities", ("text",))) or ("text",),
        enabled=_as_bool(value.get("enabled", True)),
        healthy=_as_bool(value.get("healthy", True)),
        managed=_as_bool(value.get("managed", False)),
        warm_models=_string_tuple(value.get("warm_models", ())),
        reserved_vram_mb=_as_int(value.get("reserved_vram_mb", 0), default=0),
    )


def _target_validation_errors(target: RuntimeTarget, *, index: int, seen_ids: set[str]) -> tuple[str, ...]:
    prefix = f"resource_routing.targets[{index}]"
    errors: list[str] = []
    if not target.id:
        errors.append(f"{prefix}.id is required")
    elif target.id in seen_ids:
        errors.append(f"{prefix}.id duplicates {target.id}")
    if target.device and not _supported_device(target.device):
        errors.append(f"{prefix}.device is unsupported: {target.device}")
    invalid_modalities = [modality for modality in target.modalities if modality not in _SUPPORTED_MODALITIES]
    if invalid_modalities:
        errors.append(f"{prefix}.modalities contains unsupported values: {', '.join(invalid_modalities)}")
    return tuple(errors)


_CUDA_DEVICE_RE = re.compile(r"^cuda(?::[0-9]+)?$")
_SUPPORTED_MODALITIES = {"text", "vision", "audio", "speech"}


def _supported_device(value: str) -> bool:
    return value == "cpu" or bool(_CUDA_DEVICE_RE.match(value))


def _string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = ()
    return tuple(str(item).strip() for item in items if str(item).strip())


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
