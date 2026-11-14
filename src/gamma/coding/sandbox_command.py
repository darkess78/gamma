"""Command line for running a repository's test suite inside Gamma's Linux sandbox."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def build_test_sandbox_command(workspace: Path, *, timeout_seconds: float, pytest_args: tuple[str, ...] = ()) -> list[str]:
    """systemd-run scope + prlimit + bwrap: no network, read-only source, private /tmp and data."""
    if os.name != "posix":
        raise RuntimeError("sandboxed test runs require Gamma's Linux sandbox host")
    tools = {name: shutil.which(name) for name in ("bwrap", "prlimit", "systemd-run")}
    missing = [name for name, path in tools.items() if not path]
    if missing:
        raise RuntimeError("sandbox tools are missing:" + ",".join(missing))
    root = workspace.resolve()
    venv = Path(sys.prefix).resolve()
    if not (venv / "bin" / "python").is_file() or venv == Path(sys.base_prefix).resolve():
        raise RuntimeError("sandboxed test runs require a trusted external virtual environment")
    if root == venv or root in venv.parents or venv in root.parents:
        raise RuntimeError("workspace and trusted virtual environment must be isolated")
    cpu_seconds = max(1, min(int(timeout_seconds), 900))
    command = [
        str(tools["systemd-run"]), "--user", "--scope", "--collect", "--quiet",
        "-p", "MemoryMax=8G", "-p", "TasksMax=64", "-p", "CPUQuota=400%",
        str(tools["prlimit"]), f"--cpu={cpu_seconds}", "--as=8589934592",
        "--nofile=4096", "--fsize=104857600", "--",
        str(tools["bwrap"]), "--die-with-parent", "--new-session", "--unshare-net",
        "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--cap-drop", "ALL",
    ]
    for path in ("/usr", "/bin", "/lib", "/lib64"):
        if Path(path).exists():
            command.extend(("--ro-bind", path, path))
    if Path("/etc/mime.types").is_file():
        command.extend(("--dir", "/etc", "--ro-bind", "/etc/mime.types", "/etc/mime.types"))
    command.extend(("--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp", "--dir", "/tmp/gamma-home",
                    "--dir", "/workspace", "--ro-bind", str(root), "/workspace",
                    "--tmpfs", "/workspace/data"))
    for sub in ("audio", "live_jobs", "memory", "runtime", "runtime/logs"):
        command.extend(("--dir", f"/workspace/data/{sub}"))
    command.extend((
        "--dir", "/opt", "--ro-bind", str(venv), "/opt/gamma-venv", "--chdir", "/workspace",
        "--clearenv",
        "--setenv", "HOME", "/tmp/gamma-home",
        "--setenv", "PATH", "/opt/gamma-venv/bin:/usr/bin:/bin",
        "--setenv", "LANG", "C.UTF-8",
        "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
        "--setenv", "PYTHONPATH", "/workspace/src",
        "--setenv", "SHANA_PROJECT_ROOT", "/workspace",
        "--", "/opt/gamma-venv/bin/python", "-m", "pytest", "-q", "-p", "no:cacheprovider",
    ))
    command.extend(pytest_args)
    return command


def launcher_environment() -> dict[str, str]:
    """Only what systemd-run --user needs to reach the user manager."""
    return {name: os.environ[name] for name in ("DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR")
            if os.environ.get(name)}
