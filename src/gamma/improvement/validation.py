from __future__ import annotations

import ast
import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, Field

from .candidates import CandidateApplicationReceipt
from .experiments import (
    ExperimentManifest,
    _run_git,
    validate_candidate_scope,
    verify_experiment_workspace,
)


_SAFETY_TESTS = (
    "tests/test_safety_filters.py",
    "tests/test_conversation_pipeline.py",
    "tests/test_api_routes.py",
)
_MAXIMUM_OUTPUT_CHARS = 30_000


class SandboxedTestResult(BaseModel):
    profile: Literal["safety_privacy", "full_suite"]
    passed: bool
    return_code: int
    duration_ms: float
    output_sha256: str
    output_tail: str = Field(max_length=_MAXIMUM_OUTPUT_CHARS)
    network_isolated: bool = True
    workspace_read_only: bool = True
    host_home_hidden: bool = True
    resource_limited: bool = True


class CandidateValidationReport(BaseModel):
    manifest_id: str
    baseline_commit: str
    diff_sha256: str
    changed_paths: tuple[str, ...]
    static_checks_passed: bool
    test_results: tuple[SandboxedTestResult, ...]
    passed: bool
    verified_gates: tuple[str, ...]
    remaining_required_gates: tuple[str, ...]
    authority: Literal["validation_evidence_only"] = "validation_evidence_only"


class CandidateValidator:
    """Validate a candidate with static checks and fixed tests in a fail-closed OS sandbox."""

    def __init__(
        self,
        sandbox_runner: Callable[[Path, Literal["safety_privacy", "full_suite"], float], SandboxedTestResult]
        | None = None,
    ) -> None:
        self.sandbox_runner = sandbox_runner or run_sandboxed_test_profile

    def validate(
        self,
        *,
        manifest: ExperimentManifest,
        receipt: CandidateApplicationReceipt,
        workspace: Path,
        required_gates: tuple[str, ...],
        maximum_duration_seconds: float | None = None,
    ) -> CandidateValidationReport:
        if manifest.status != "candidate_ready":
            raise ValueError("candidate validation requires a candidate_ready experiment")
        verify_experiment_workspace(manifest, workspace, require_clean=False)
        root = workspace.resolve()
        changed_paths = tuple(
            line.strip().replace("\\", "/")
            for line in _run_git(root, ["diff", "--name-only", "--"]).splitlines()
            if line.strip()
        )
        if not changed_paths:
            raise ValueError("candidate validation requires a non-empty diff")
        scope = validate_candidate_scope(manifest, list(changed_paths))
        if not scope.passed:
            raise ValueError("candidate validation scope violation:" + ",".join(scope.violations))
        if set(changed_paths) != set(receipt.changed_paths):
            raise ValueError("candidate receipt changed paths do not match workspace")
        if receipt.manifest_id != manifest.id or receipt.baseline_commit != manifest.baseline_commit:
            raise ValueError("candidate receipt manifest binding mismatch")
        receipt_by_path = {item.path: item for item in receipt.files}
        if set(receipt_by_path) != set(changed_paths):
            raise ValueError("candidate receipt file set does not match workspace")
        for relative in changed_paths:
            item = receipt_by_path.get(relative)
            if item is None:
                raise ValueError(f"candidate receipt is missing changed file:{relative}")
            if hashlib.sha256((root / relative).read_bytes()).hexdigest() != item.after_sha256:
                raise ValueError(f"candidate receipt file hash mismatch:{relative}")
        _run_git(root, ["diff", "--check", "--"])
        diff = _run_git(root, ["diff", "--no-ext-diff", "--binary", "--"])
        diff_sha256 = hashlib.sha256(diff.encode("utf-8")).hexdigest()
        if diff_sha256 != receipt.diff_sha256:
            raise ValueError("candidate receipt diff hash mismatch")
        for relative in changed_paths:
            if relative.endswith(".py"):
                ast.parse((root / relative).read_text(encoding="utf-8", errors="strict"), filename=relative)

        duration_limit = min(float(manifest.maximum_wall_clock_minutes * 60), 3600.0)
        if maximum_duration_seconds is not None:
            duration_limit = min(duration_limit, max(0.0, maximum_duration_seconds))
        deadline = time.monotonic() + duration_limit
        results: list[SandboxedTestResult] = []
        for profile in ("safety_privacy", "full_suite"):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            result = self.sandbox_runner(root, profile, remaining)
            results.append(result)
            if not result.passed:
                break
        passed = len(results) == 2 and all(item.passed for item in results)
        verified = ("automated_tests", "safety_privacy") if passed else ()
        remaining_gates = tuple(gate for gate in required_gates if gate not in verified)
        return CandidateValidationReport(
            manifest_id=manifest.id,
            baseline_commit=manifest.baseline_commit,
            diff_sha256=diff_sha256,
            changed_paths=tuple(sorted(changed_paths)),
            static_checks_passed=True,
            test_results=tuple(results),
            passed=passed,
            verified_gates=verified,
            remaining_required_gates=remaining_gates,
        )


def run_sandboxed_test_profile(
    workspace: Path,
    profile: Literal["safety_privacy", "full_suite"],
    timeout_seconds: float,
) -> SandboxedTestResult:
    command = build_candidate_sandbox_command(workspace, profile=profile, timeout_seconds=timeout_seconds)
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=workspace,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1.0, timeout_seconds),
            check=False,
            env=_minimal_launcher_environment(),
        )
        output = completed.stdout or ""
        return_code = completed.returncode
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout or ""
        output = partial if isinstance(partial, str) else partial.decode("utf-8", errors="replace")
        output += "\nvalidation_timeout"
        return_code = 124
    duration_ms = (time.perf_counter() - started) * 1000.0
    return SandboxedTestResult(
        profile=profile,
        passed=return_code == 0,
        return_code=return_code,
        duration_ms=round(duration_ms, 3),
        output_sha256=hashlib.sha256(output.encode("utf-8")).hexdigest(),
        output_tail=output[-_MAXIMUM_OUTPUT_CHARS:],
    )


def build_candidate_sandbox_command(
    workspace: Path,
    *,
    profile: Literal["safety_privacy", "full_suite"],
    timeout_seconds: float,
) -> list[str]:
    if os.name != "posix":
        raise RuntimeError("candidate execution requires Gamma's Linux sandbox host")
    tools = {name: shutil.which(name) for name in ("bwrap", "prlimit", "systemd-run")}
    missing = [name for name, path in tools.items() if not path]
    if missing:
        raise RuntimeError("candidate sandbox tools are missing:" + ",".join(missing))
    root = workspace.resolve()
    venv = Path(sys.prefix).resolve()
    if not (venv / "bin" / "python").is_file() or venv == Path(sys.base_prefix).resolve():
        raise RuntimeError("candidate execution requires a trusted external virtual environment")
    if root == venv or root in venv.parents or venv in root.parents:
        raise RuntimeError("candidate workspace and trusted virtual environment must be isolated")
    cpu_seconds = max(1, min(int(timeout_seconds), 900))
    command = [
        str(tools["systemd-run"]),
        "--user",
        "--scope",
        "--collect",
        "--quiet",
        "-p",
        "MemoryMax=8G",
        "-p",
        "TasksMax=64",
        "-p",
        "CPUQuota=400%",
        str(tools["prlimit"]),
        f"--cpu={cpu_seconds}",
        "--as=8589934592",
        "--nofile=4096",
        "--fsize=104857600",
        "--",
        str(tools["bwrap"]),
        "--die-with-parent",
        "--new-session",
        "--unshare-net",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--cap-drop",
        "ALL",
    ]
    for path in ("/usr", "/bin", "/lib"):
        if Path(path).exists():
            command.extend(("--ro-bind", path, path))
    if Path("/lib64").exists():
        command.extend(("--ro-bind", "/lib64", "/lib64"))
    if Path("/etc/mime.types").is_file():
        command.extend(("--dir", "/etc", "--ro-bind", "/etc/mime.types", "/etc/mime.types"))
    command.extend(
        (
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--tmpfs",
            "/tmp",
            "--dir",
            "/tmp/gamma-home",
            "--dir",
            "/workspace",
            "--ro-bind",
            str(root),
            "/workspace",
            "--tmpfs",
            "/workspace/data",
            "--dir",
            "/workspace/data/audio",
            "--dir",
            "/workspace/data/improvement",
            "--dir",
            "/workspace/data/live_jobs",
            "--dir",
            "/workspace/data/memory",
            "--dir",
            "/workspace/data/runtime",
            "--dir",
            "/workspace/data/runtime/logs",
            "--dir",
            "/opt",
            "--ro-bind",
            str(venv),
            "/opt/gamma-venv",
            "--chdir",
            "/workspace",
            "--clearenv",
            "--setenv",
            "HOME",
            "/tmp/gamma-home",
            "--setenv",
            "PATH",
            "/opt/gamma-venv/bin:/usr/bin:/bin",
            "--setenv",
            "LANG",
            "C.UTF-8",
            "--setenv",
            "PYTHONDONTWRITEBYTECODE",
            "1",
            "--setenv",
            "PYTHONPATH",
            "/workspace/src",
            "--setenv",
            "SHANA_PROJECT_ROOT",
            "/workspace",
            "--",
            "/opt/gamma-venv/bin/python",
            "-m",
            "pytest",
        )
    )
    if profile == "safety_privacy":
        command.extend(_SAFETY_TESTS)
    command.extend(("-q", "-p", "no:cacheprovider"))
    return command


def _minimal_launcher_environment() -> dict[str, str]:
    environment: dict[str, str] = {}
    for name in ("DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment
