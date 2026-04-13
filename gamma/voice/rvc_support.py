from __future__ import annotations

import re
from pathlib import Path

from ..config import settings
from ..errors import ConfigurationError


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _existing_path_candidates(base: Path, raw_value: str) -> list[Path]:
    value = Path(raw_value).expanduser()
    if value.is_absolute():
        return [value]
    return [
        (base / value),
        (settings.project_root / value),
    ]


def discover_rvc_project_root(explicit: str | None = None) -> Path | None:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())

    project_root = settings.project_root.resolve()
    parents = [project_root, *project_root.parents[:3]]
    for base in parents:
        candidates.extend(
            [
                base / "RVC" / "Retrieval-based-Voice-Conversion-WebUI-main",
                base / "Retrieval-based-Voice-Conversion-WebUI-main",
                base / "data" / "RVC" / "Retrieval-based-Voice-Conversion-WebUI-main",
            ]
        )

    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        if resolved in seen:
            continue
        seen.add(resolved)
        if (resolved / "tools" / "infer_cli.py").exists() and (resolved / "assets" / "weights").exists():
            return resolved
    return None


def discover_rvc_python(explicit: str | None, rvc_root: Path | None) -> Path | None:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    if rvc_root is not None:
        candidates.extend(
            [
                rvc_root / ".venv" / "Scripts" / "python.exe",
                rvc_root / ".venv" / "bin" / "python",
                rvc_root.parent / ".venv" / "Scripts" / "python.exe",
                rvc_root.parent / ".venv" / "bin" / "python",
                rvc_root / "runtime" / "python.exe",
            ]
        )

    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.exists():
            return resolved
    return None


def resolve_rvc_project_root(explicit: str | None = None) -> Path:
    path = discover_rvc_project_root(explicit)
    if path is None:
        raise ConfigurationError(
            "Could not find the RVC project root. Set SHANA_RVC_PROJECT_ROOT or place the repo in an expected location."
        )
    return path


def resolve_rvc_python(explicit: str | None, rvc_root: Path) -> Path:
    path = discover_rvc_python(explicit, rvc_root)
    if path is None:
        raise ConfigurationError(
            "Could not find the RVC Python interpreter. Set SHANA_RVC_PYTHON or create a .venv near the RVC project."
        )
    return path


def resolve_rvc_model_path(rvc_root: Path, model_name: str | None) -> Path:
    if not model_name:
        raise ConfigurationError("SHANA_RVC_MODEL_NAME is required when SHANA_RVC_ENABLED=true.")
    model = Path(model_name).expanduser()
    if model.is_absolute():
        if model.exists():
            return model.resolve()
    else:
        candidates = [
            *_existing_path_candidates(rvc_root, model_name),
            (rvc_root / "assets" / "weights" / model_name),
        ]
        for candidate in candidates:
            candidate = candidate.resolve()
            if candidate.exists():
                return candidate
    raise ConfigurationError(f"RVC model does not exist: {model_name}")


def resolve_rvc_index_path(rvc_root: Path, explicit: str | None, model_name: str | None) -> Path:
    if explicit:
        for candidate in _existing_path_candidates(rvc_root, explicit):
            candidate = candidate.resolve()
            if candidate.exists():
                return candidate
        raise ConfigurationError(f"SHANA_RVC_INDEX_PATH does not exist: {explicit}")

    if not model_name:
        raise ConfigurationError("SHANA_RVC_MODEL_NAME is required to auto-discover the RVC index path.")

    model_stem = Path(model_name).stem
    target = _normalize_name(model_stem)
    search_roots = [
        rvc_root / "assets" / "indices",
        rvc_root / "logs",
    ]
    matches: list[Path] = []
    for root in search_roots:
        if not root.exists():
            continue
        for path in root.rglob("*.index"):
            name = _normalize_name(path.stem)
            if target and target in name:
                matches.append(path.resolve())
    if matches:
        matches.sort(key=lambda path: (len(path.name), str(path)))
        return matches[0]
    raise ConfigurationError(
        f"Could not auto-discover an RVC index for model {model_name}. Set SHANA_RVC_INDEX_PATH explicitly."
    )
