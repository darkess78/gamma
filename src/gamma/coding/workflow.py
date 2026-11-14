"""Shana's coding workflow for local models: split, work each task, check, hand to the owner.

1. A throwaway git worktree of the repository is created.
2. ``split.split_goal`` breaks the goal into small tasks (or returns a question).
   Tasks may only touch files ``project_policy.editable`` allows.
3. Each task runs in ``task_loop.TaskLoop``; its check runs in the bubblewrap
   sandbox. Later tasks see earlier tasks' summaries. A task that gives up
   stops the workflow and its reason is reported.
4. Changed source is checked against ``project_policy.unsafe_source``, then
   the full test suite runs on the result.
5. The diff (including new tests) and a report are written for the owner.
   Nothing is applied to running Shana (D5).

Owner goals normally arrive through the dashboard queue and run in
``coding.autonomous``, which passes ``checkpoint`` (pause, stop, time budget)
and ``on_event`` (progress for the Coding page). From a shell:

    python -m gamma.coding.workflow --goal "..." [--model qwen3.8:27b] [--budget 1800]
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from . import project_policy
from .split import Plan, split_goal
from .task_loop import TaskLoop, TaskResult

CHECK_TIMEOUT = 300
SUITE_TIMEOUT = 900


@dataclass
class WorkflowReport:
    goal: str
    status: str  # awaiting_owner_review | needs_owner | blocked | failed
    message: str
    plan: list[dict] = field(default_factory=list)
    tasks: list[dict] = field(default_factory=list)
    suite: dict = field(default_factory=dict)
    diff_path: str = ""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def sandboxed_pytest(workspace: Path, args: tuple[str, ...], timeout: int) -> dict:
    from .sandbox_command import build_test_sandbox_command, launcher_environment
    command = build_test_sandbox_command(workspace, timeout_seconds=timeout, pytest_args=args)
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout + 30, env=launcher_environment())
    except subprocess.TimeoutExpired:
        return {"passed": False, "output": "timed out"}
    output = (done.stdout + done.stderr)[-6000:]
    return {"passed": done.returncode == 0, "output": output}


def overview(repo: Path, limit: int = 300) -> str:
    files = [f for f in _git(repo, "ls-files", "src", "tests").splitlines() if f.endswith(".py")]
    return "\n".join(files[:limit])


def _quiet(*_args, **_kwargs) -> None:
    return None


def run(goal: str, repo: Path, model, out_dir: Path, *,
        run_tests: Callable[[Path, tuple[str, ...], int], dict] = sandboxed_pytest,
        checkpoint: Callable[[], None] = _quiet, on_event: Callable[..., None] = _quiet) -> WorkflowReport:
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="shana-task-")) / "repo"
    branch = f"shana/{uuid.uuid4().hex[:8]}"
    _git(repo, "worktree", "add", "-q", "-b", branch, str(work), "HEAD")
    try:
        files = set(_git(work, "ls-files").splitlines())
        on_event("splitting", "Splitting the goal into small tasks")
        plan: Plan = split_goal(model, goal, repo_overview=overview(work), known_files=files,
                                editable=lambda path: project_policy.editable(path, files))
        report = WorkflowReport(goal, "blocked", "", plan=[asdict(t) for t in plan.tasks])
        if plan.question:
            report.status, report.message = "needs_owner", plan.question
            return _finish(report, out_dir)
        if not plan.ok:
            report.message = "Couldn't split the goal into small tasks: " + "; ".join(plan.problems)
            return _finish(report, out_dir)
        loop = TaskLoop(model, work, lambda check: run_tests(work, (check,), CHECK_TIMEOUT), checkpoint=checkpoint)
        notes = ""
        for task in plan.tasks:
            on_event("working", f"Task {task.id}: {task.title}", task=task.id)
            result: TaskResult = loop.run(task, notes=notes)
            report.tasks.append({"id": result.task_id, "status": result.status, "summary": result.summary,
                                 "steps": result.steps, "changed": sorted(result.changed)})
            if result.status != "done":
                report.message = f"Task {task.id} {result.status.replace('_', ' ')}: {result.summary}"
                break
            notes += f"Already done ({task.id}): {result.summary}\n"
        else:
            problem = _unsafe_change(work, files)
            if problem:
                report.status, report.message = "blocked", f"The change can't be proposed: {problem}"
                return _finish(report, out_dir)
            checkpoint()
            on_event("suite", "Running the full test suite")
            report.suite = run_tests(work, (), SUITE_TIMEOUT)
            if report.suite.get("passed"):
                report.status, report.message = "awaiting_owner_review", "All tasks done and the full suite passes."
            else:
                report.status, report.message = "failed", "Tasks done but the full suite fails."
        _git(work, "add", "-A")
        diff = _git(work, "diff", "--cached", "HEAD")
        diff_path = out_dir / "proposal.diff"
        diff_path.write_text(diff, encoding="utf-8")
        report.diff_path = str(diff_path)
        return _finish(report, out_dir)
    finally:
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(work)], capture_output=True)
        subprocess.run(["git", "-C", str(repo), "branch", "-D", branch], capture_output=True)
        shutil.rmtree(work.parent, ignore_errors=True)


def _unsafe_change(work: Path, before: set[str]) -> str | None:
    changed = [line[3:] for line in _git(work, "status", "--porcelain", "--untracked-files=all").splitlines()]
    for path in changed:
        if not project_policy.editable(path, before):
            return f"{path} is outside what self-coding may change"
        target = work / path
        problem = project_policy.unsafe_source(path, target.read_text(encoding="utf-8")) if target.is_file() else None
        if problem:
            return problem
    return None


def _finish(report: WorkflowReport, out_dir: Path) -> WorkflowReport:
    (out_dir / "report.json").write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Queue a coding goal for Shana's background worker.")
    parser.add_argument("--goal", required=True)
    parser.add_argument("--model", default="qwen3.8:27b")
    parser.add_argument("--budget", type=int, default=1800, help="seconds")
    args = parser.parse_args()
    from .operator import enqueue_goal
    result = enqueue_goal(args.goal, args.model, args.budget)
    print(json.dumps({"job": result["job"]["id"], "status": result["job"]["status"],
                      "worker": result["process"].get("ok")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
