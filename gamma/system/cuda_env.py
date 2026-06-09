from __future__ import annotations

import os
import site
import sys
from pathlib import Path


def candidate_cuda_library_paths() -> list[Path]:
    paths: list[Path] = []
    seen: set[Path] = set()

    site_roots = [Path(path) for path in site.getsitepackages()]
    if sys.prefix:
        site_roots.append(Path(sys.prefix) / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages")

    relative_lib_dirs = (
        "nvidia/cu12/lib",
        "nvidia/cu13/lib",
        "nvidia/cudnn/lib",
        "nvidia/cublas/lib",
        "nvidia/cuda_runtime/lib",
        "nvidia/cuda_nvrtc/lib",
    )
    for root in site_roots:
        for relative in relative_lib_dirs:
            candidate = (root / relative).resolve()
            if candidate.exists() and candidate not in seen:
                paths.append(candidate)
                seen.add(candidate)
    return paths


def prepend_cuda_library_path(env: dict[str, str] | None = None) -> dict[str, str]:
    target = env if env is not None else os.environ
    existing = target.get("LD_LIBRARY_PATH", "")
    entries = [str(path) for path in candidate_cuda_library_paths()]
    if existing:
        entries.extend(part for part in existing.split(":") if part)
    deduped: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if entry and entry not in seen:
            deduped.append(entry)
            seen.add(entry)
    if deduped:
        target["LD_LIBRARY_PATH"] = ":".join(deduped)
    return target
