from __future__ import annotations

import subprocess
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    python = Path.home() / "AppData" / "Local" / "Programs" / "Python" / "Python312" / "python.exe"
    if not python.exists():
        python = repo_root / ".venv312" / "Scripts" / "python.exe"
    if not python.exists():
        raise SystemExit(f"Expected Python runtime at {python}")

    subprocess.run([str(python), "-m", "gamma.supervisor.cli", "stop", "all"], cwd=repo_root, check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
