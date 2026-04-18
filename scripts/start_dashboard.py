from __future__ import annotations

import os
import subprocess
import sys
import webbrowser
from pathlib import Path

from gamma.config import settings


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    kwargs: dict[str, object] = {
        "cwd": repo_root,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([sys.executable, "-m", "gamma.supervisor.cli", "start", "dashboard"], **kwargs)
    webbrowser.open(settings.dashboard_base_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
