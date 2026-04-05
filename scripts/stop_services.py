from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, "-m", "gamma.supervisor.cli", "stop", "all"], cwd=repo_root, check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
