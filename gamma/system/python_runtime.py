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
            resolved = expanded.resolve()
        except Exception:
            resolved = expanded
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(resolved)
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
            project_root / ".venv" / "Scripts" / "python.exe",
            project_root / ".venv312" / "bin" / "python",
            project_root / ".venv312" / "Scripts" / "python.exe",
        ]
    )

    if os.name == "nt":
        candidates.append(Path.home() / "AppData" / "Local" / "Programs" / "Python" / "Python312" / "python.exe")
    else:
        for command_name in ("python3", "python"):
            discovered = shutil.which(command_name)
            if discovered:
                candidates.append(Path(discovered))

    return _unique_paths(candidates)


def resolve_python_executable(
    project_root: Path,
    *,
    env_var: str = "SHANA_PYTHON",
    prefer_windowless: bool = False,
) -> str:
    for candidate in python_candidates(project_root, env_var=env_var):
        if not candidate.exists():
            continue
        if prefer_windowless and os.name == "nt":
            pythonw = candidate.parent / "pythonw.exe"
            if pythonw.exists():
                return str(pythonw)
        return str(candidate)
    return sys.executable
