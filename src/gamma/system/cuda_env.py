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


_PRELOADED = False


def preload_cuda12_libraries() -> int:
    """Load CUDA 12 cuBLAS/cuDNN (pip ``nvidia-cublas-cu12`` / ``nvidia-cudnn-cu12``) into
    this process so faster-whisper's ctranslate2 can find them.

    Unlike editing LD_LIBRARY_PATH, this changes nothing that child processes inherit, so
    the Qwen TTS sidecar keeps its own (CUDA 13) libraries. Returns how many were loaded.
    """
    global _PRELOADED
    if _PRELOADED:
        return 0
    import ctypes
    loaded = 0
    for directory in candidate_cuda_library_paths():
        name = directory.parent.name  # cublas, cudnn, ...
        if name not in {"cublas", "cudnn"}:
            continue
        patterns = ("libcublasLt.so.12", "libcublas.so.12") if name == "cublas" else ("libcudnn*.so.9",)
        for pattern in patterns:
            for lib in sorted(directory.glob(pattern)):
                try:
                    ctypes.CDLL(str(lib), mode=ctypes.RTLD_GLOBAL)
                    loaded += 1
                except OSError:
                    pass
    _PRELOADED = loaded > 0
    return loaded
