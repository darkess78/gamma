from __future__ import annotations

from typing import Any


def best_available_cuda_index(torch_module: Any) -> int | None:
    try:
        if not torch_module.cuda.is_available():
            return None
        device_count = int(torch_module.cuda.device_count())
    except Exception:
        return None
    if device_count <= 0:
        return None

    best_index = 0
    best_free_bytes = -1
    for index in range(device_count):
        try:
            free_bytes, _total_bytes = torch_module.cuda.mem_get_info(index)
        except Exception:
            return 0
        if int(free_bytes) > best_free_bytes:
            best_free_bytes = int(free_bytes)
            best_index = index
    return best_index


def resolve_torch_device(
    requested_device: str | None,
    *,
    preferred_index: int | None = None,
    torch_module: Any,
) -> tuple[str, str | None]:
    normalized = str(requested_device or "auto").strip().lower()
    if not normalized or normalized == "auto":
        best_index = best_available_cuda_index(torch_module)
        if best_index is None:
            return "cpu", None
        return f"cuda:{best_index}", None

    if normalized == "cpu":
        return "cpu", None

    if not normalized.startswith("cuda"):
        return normalized, None

    best_index = best_available_cuda_index(torch_module)
    if best_index is None:
        return "cpu", f"Requested device '{normalized}' is unavailable; falling back to CPU."

    suffix = normalized.partition(":")[2].strip()
    requested_index_value: int | None = None
    if suffix:
        try:
            requested_index_value = int(suffix)
        except ValueError:
            return f"cuda:{best_index}", f"Invalid CUDA device '{normalized}'; using cuda:{best_index} instead."
    elif preferred_index is not None:
        requested_index_value = preferred_index

    try:
        device_count = int(torch_module.cuda.device_count())
    except Exception:
        device_count = best_index + 1

    if requested_index_value is None:
        return f"cuda:{best_index}", None
    if 0 <= requested_index_value < max(device_count, 1):
        return f"cuda:{requested_index_value}", None
    return f"cuda:{best_index}", (
        f"Requested CUDA index {requested_index_value} is unavailable; using cuda:{best_index} instead."
    )
