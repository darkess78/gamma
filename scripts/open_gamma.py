from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _spawn(command: list[str], repo_root: Path) -> None:
    kwargs: dict[str, object] = {
        "cwd": repo_root,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    kwargs["start_new_session"] = True
    subprocess.Popen(command, **kwargs)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    _spawn([sys.executable, "-m", "gamma.supervisor.cli", "start", "dashboard", "--open-browser"], repo_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
