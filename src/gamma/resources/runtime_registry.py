from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from ..config import load_app_file_config
from .models import RuntimeEndpoint, RuntimeTarget


@dataclass(frozen=True, slots=True)
class ResourceRoutingPolicy:
    shadow_mode: bool = False
    active_llm_routing: bool = False
    startup_admission: bool = False
    snapshot_max_age_seconds: int = 5
    reservation_ttl_seconds: int = 30
    minimum_headroom_mb: int = 2048
    default_estimated_vram_mb: int = 0


@dataclass(frozen=True, slots=True)
class ResourceRoutingRegistry:
    policy: ResourceRoutingPolicy
    targets: tuple[RuntimeTarget, ...]
    endpoints: tuple[RuntimeEndpoint, ...] = ()
    validation_errors: tuple[str, ...] = ()

    def endpoint_by_id(self) -> dict[str, RuntimeEndpoint]:
        return {endpoint.id: endpoint for endpoint in self.endpoints}

    def endpoint_for_target(self, target: RuntimeTarget) -> RuntimeEndpoint | None:
        if not target.endpoint_ref:
            return None
        return self.endpoint_by_id().get(target.endpoint_ref)


def load_resource_routing_registry(config: dict[str, Any] | None = None) -> ResourceRoutingRegistry:
    app_config = config if config is not None else load_app_file_config()
    root = app_config.get("resource_routing", {})
    if not isinstance(root, dict):
        root = {}
    policy = _policy(root.get("policy", {}))
    endpoints, endpoint_errors = _endpoints(root.get("endpoints", {}))
    endpoint_ids = {endpoint.id for endpoint in endpoints}
    raw_targets = root.get("targets", [])
    targets, target_errors = _targets(raw_targets, endpoint_ids=endpoint_ids)
    return ResourceRoutingRegistry(
        policy=policy,
        targets=targets,
        endpoints=endpoints,
        validation_errors=endpoint_errors + target_errors,
    )


def _policy(value: Any) -> ResourceRoutingPolicy:
    payload = value if isinstance(value, dict) else {}
    return ResourceRoutingPolicy(
        shadow_mode=_as_bool(payload.get("shadow_mode", False)),
        active_llm_routing=_as_bool(payload.get("active_llm_routing", False)),
        startup_admission=_as_bool(payload.get("startup_admission", False)),
        snapshot_max_age_seconds=_as_int(payload.get("snapshot_max_age_seconds", 5), default=5),
        reservation_ttl_seconds=_as_int(payload.get("reservation_ttl_seconds", 30), default=30),
        minimum_headroom_mb=_as_int(payload.get("minimum_headroom_mb", 2048), default=2048),
        default_estimated_vram_mb=_as_int(payload.get("default_estimated_vram_mb", 0), default=0),
    )


def _endpoints(value: Any) -> tuple[tuple[RuntimeEndpoint, ...], tuple[str, ...]]:
    if not isinstance(value, dict):
        if value:
            return (), ("resource_routing.endpoints must be an object",)
        return (), ()
    endpoints: list[RuntimeEndpoint] = []
    errors: list[str] = []
    for endpoint_id, raw in value.items():
        endpoint = _endpoint(str(endpoint_id), raw)
        endpoint_errors = _endpoint_validation_errors(endpoint)
        errors.extend(endpoint_errors)
        if endpoint_errors:
            continue
        endpoints.append(endpoint)
    return tuple(endpoints), tuple(errors)


def _endpoint(endpoint_id: str, value: Any) -> RuntimeEndpoint:
    endpoint_id = endpoint_id.strip()
    if isinstance(value, str):
        return RuntimeEndpoint(id=endpoint_id, url=value.strip())
    payload = value if isinstance(value, dict) else {}
    return RuntimeEndpoint(
        id=endpoint_id,
        url=str(payload.get("url", "") or "").strip(),
        kind=str(payload.get("kind", "") or "").strip().lower(),
        provider=str(payload.get("provider", "") or "").strip().lower(),
    )


def _endpoint_validation_errors(endpoint: RuntimeEndpoint) -> tuple[str, ...]:
    prefix = f"resource_routing.endpoints.{endpoint.id or '<empty>'}"
    errors: list[str] = []
    if not endpoint.id:
        errors.append(f"{prefix}.id is required")
    if not endpoint.url:
        errors.append(f"{prefix}.url is required")
    elif not _supported_url(endpoint.url):
        errors.append(f"{prefix}.url is unsupported: {endpoint.url}")
    return tuple(errors)


def _targets(value: Any, *, endpoint_ids: set[str]) -> tuple[tuple[RuntimeTarget, ...], tuple[str, ...]]:
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
        target_errors = _target_validation_errors(target, index=index, seen_ids=seen_ids, endpoint_ids=endpoint_ids)
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


def _target_validation_errors(target: RuntimeTarget, *, index: int, seen_ids: set[str], endpoint_ids: set[str]) -> tuple[str, ...]:
    prefix = f"resource_routing.targets[{index}]"
    errors: list[str] = []
    if not target.id:
        errors.append(f"{prefix}.id is required")
    elif target.id in seen_ids:
        errors.append(f"{prefix}.id duplicates {target.id}")
    if target.device and not _supported_device(target.device):
        errors.append(f"{prefix}.device is unsupported: {target.device}")
    if target.endpoint_ref and target.endpoint_ref not in endpoint_ids:
        errors.append(f"{prefix}.endpoint_ref is unknown: {target.endpoint_ref}")
    invalid_modalities = [modality for modality in target.modalities if modality not in _SUPPORTED_MODALITIES]
    if invalid_modalities:
        errors.append(f"{prefix}.modalities contains unsupported values: {', '.join(invalid_modalities)}")
    return tuple(errors)


_CUDA_DEVICE_RE = re.compile(r"^cuda(?::[0-9]+)?$")
_SUPPORTED_MODALITIES = {"text", "vision", "audio", "speech"}


def _supported_device(value: str) -> bool:
    return value == "cpu" or bool(_CUDA_DEVICE_RE.match(value))


def _supported_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https", "ws", "wss"} and bool(parsed.netloc)


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
