"""Execute fictional coding exercises in a read-only, offline Linux sandbox."""
from __future__ import annotations

import hashlib
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path


def build_practice_command(workspace: Path, *, module: str, kind: str,
                           timeout_seconds: float, venv: Path | None = None) -> list[str]:
    if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", module) or kind not in {"regression", "feature"}:
        raise ValueError("invalid practice check")
    if not math.isfinite(timeout_seconds) or not 1 <= timeout_seconds <= 30:
        raise ValueError("invalid practice check timeout")
    if not sys.platform.startswith("linux"):
        raise RuntimeError("practice execution requires the Gamma Linux sandbox")
    tools = {name: shutil.which(name) for name in ("bwrap", "prlimit", "systemd-run")}
    if not all(tools.values()):
        raise RuntimeError("required practice sandbox tools are unavailable")
    workspace = Path(workspace)
    if workspace.is_symlink() or not workspace.is_dir():
        raise ValueError("invalid practice workspace")
    workspace = workspace.resolve()
    venv = Path(venv or sys.prefix).resolve()
    if not (venv / "bin" / "python").is_file():
        raise RuntimeError("practice requires a trusted external virtual environment")
    if workspace == venv or workspace in venv.parents or venv in workspace.parents:
        raise ValueError("practice workspace must be separate from the trusted environment")
    command = [tools["systemd-run"], "--user", "--scope", "--collect", "--quiet",
               "-p", "MemoryMax=1G", "-p", "TasksMax=32", "-p", "CPUQuota=100%",
               tools["prlimit"], "--as=1073741824", "--cpu=30", "--fsize=1048576", "--",
               tools["bwrap"], "--unshare-all", "--new-session", "--die-with-parent", "--clearenv"]
    for path in (Path("/usr"), Path("/lib"), Path("/lib64")):
        if path.exists():
            command += ["--ro-bind", str(path), str(path)]
    command += ["--ro-bind", str(venv), str(venv), "--ro-bind", str(workspace), "/work",
                "--tmpfs", "/tmp", "--proc", "/proc", "--setenv", "PYTHONPATH", "/work",
                "--setenv", "PYTHONDONTWRITEBYTECODE", "1", "--setenv", "HOME", "/tmp",
                "--chdir", "/work", str(venv / "bin" / "python"), "-B", "-m"]
    return command + (["unittest", module] if kind == "regression" else [module])


def run_practice_check(workspace, spec):
    started = time.monotonic()
    result = {"name": spec.name, "kind": spec.kind, "passed": False,
              "infrastructure_error": False,
              "exit_code": -1, "output_tail": "", "output_sha256": "", "duration_ms": 0}
    try:
        command = build_practice_command(workspace, module=spec.module, kind=spec.kind, timeout_seconds=30)
        receipt = "GAMMA_CHECK_COMPLETED_" + uuid.uuid4().hex
        if spec.kind == "regression":
            bootstrap = (
                "import unittest,sys; "
                f"suite=unittest.defaultTestLoader.loadTestsFromName({spec.module!r}); "
                "result=unittest.TextTestRunner().run(suite); "
                "ok=result.testsRun>0 and result.wasSuccessful(); "
                f"print({receipt!r}) if ok else None; sys.exit(0 if ok else 1)"
            )
        else:
            bootstrap = f"import runpy; runpy.run_module({spec.module!r},run_name='__main__'); print({receipt!r})"
        command = command[:command.index("-B") + 1] + ["-c", bootstrap]
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            info = subprocess.STARTUPINFO()
            info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            info.wShowWindow = subprocess.SW_HIDE
            kwargs["startupinfo"] = info
        with tempfile.TemporaryFile() as log:
            try:
                completed = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=log,
                                           stderr=subprocess.STDOUT, timeout=30, check=False, **kwargs)
                result["exit_code"] = completed.returncode
            except subprocess.TimeoutExpired:
                result["exit_code"] = 124
            log.seek(0)
            output = log.read(1048577)
            if len(output) > 1048576:
                result["exit_code"] = 125
            result["output_tail"] = output[-2000:].decode("utf-8", errors="replace")
            result["output_sha256"] = hashlib.sha256(output).hexdigest()
        result["passed"] = result["exit_code"] == 0 and receipt.encode() in output.splitlines()
        result["infrastructure_error"] = any(line.startswith((b"bwrap:", b"Failed to connect to bus", b"Failed to start transient")) for line in output.splitlines())
        result["output_tail"] = result["output_tail"].replace(receipt, "check_completed")
        if result["exit_code"] == 0 and not result["passed"]:
            result["exit_code"] = 126
            result["output_tail"] += "\nmissing_check_completion_receipt"
    except (OSError, RuntimeError, ValueError) as exc:
        result["infrastructure_error"] = True
        result["output_tail"] = "sandbox_unavailable:" + type(exc).__name__
    result["duration_ms"] = round((time.monotonic() - started) * 1000, 3)
    return result
