from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Iterable


def _unique_paths(paths: Iterable[Path]) -> list[Path]:
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        expanded = path.expanduser()
        try:
            normalized = expanded.absolute()
        except Exception:
            normalized = expanded
        if normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


def python_candidates(project_root: Path, *, env_var: str = "SHANA_PYTHON") -> list[Path]:
    candidates: list[Path] = []
    env_python = os.getenv(env_var)
    if env_python:
        candidates.append(Path(env_python))
    if sys.executable:
        candidates.append(Path(sys.executable))

    candidates.extend(
        [
            project_root / ".venv" / "bin" / "python",
            project_root / ".venv312" / "bin" / "python",
        ]
    )

    for command_name in ("python3", "python"):
        discovered = shutil.which(command_name)
        if discovered:
            candidates.append(Path(discovered))

    return _unique_paths(candidates)


def resolve_python_executable(
    project_root: Path,
    *,
    env_var: str = "SHANA_PYTHON",
) -> str:
    for candidate in python_candidates(project_root, env_var=env_var):
        if not candidate.exists():
            continue
        return str(candidate)
    return sys.executable
