from __future__ import annotations

import subprocess
import sys
import webbrowser
from pathlib import Path

from gamma.config import settings


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    python = Path.home() / "AppData" / "Local" / "Programs" / "Python" / "Python312" / "python.exe"
    if not python.exists():
        python = repo_root / ".venv312" / "Scripts" / "python.exe"
    executable = python
    if not executable.exists():
        raise SystemExit(f"Expected Python runtime at {executable}")

    subprocess.Popen(
        [str(executable), "-m", "gamma.supervisor.cli", "start", "dashboard"],
        cwd=repo_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=(
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        ),
    )
    webbrowser.open(settings.dashboard_base_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
