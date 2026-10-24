"""Bounded practice with immutable checks and cautious capability assessment."""
from __future__ import annotations

import ast
import hashlib
import json
import math
import re
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,79}", value):
        raise ValueError("invalid practice identifier")


def _path(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:[A-Za-z_]\w*/)*[A-Za-z_]\w*\.py", value):
        raise ValueError("invalid practice source path")


def _digest(files):
    return hashlib.sha256(json.dumps(dict(files), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True)
class PracticeCheck:
    name: str
    kind: str
    module: str

    def __post_init__(self):
        _identifier(self.name)
        if self.kind not in {"regression", "feature"} or not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", self.module):
            raise ValueError("invalid practice check")


@dataclass(frozen=True)
class PracticeTask:
    id: str
    skill: str
    instructions: str
    files: dict[str, str]
    editable: tuple[str, ...]
    checks: tuple[PracticeCheck, ...]
    depends_on: tuple[str, ...] = ()

    def __post_init__(self):
        _identifier(self.id)
        _identifier(self.skill)
        if not isinstance(self.instructions, str) or not 1 <= len(self.instructions) <= 3000:
            raise ValueError("invalid practice instructions")
        if not 1 <= len(self.files) <= 20 or not self.editable or len(set(self.editable)) != len(self.editable):
            raise ValueError("invalid practice source scope")
        for path, source in self.files.items():
            _path(path)
            if not isinstance(source, str):
                raise ValueError("practice source must be text")
        if sum(len(source.encode()) for source in self.files.values()) > 65536:
            raise ValueError("practice source exceeds size limit")
        if not set(self.editable) <= self.files.keys():
            raise ValueError("editable source missing")
        if not 2 <= len(self.checks) <= 10 or {c.kind for c in self.checks} != {"regression", "feature"}:
            raise ValueError("regression and feature checks are mandatory")
        if len({c.name for c in self.checks}) != len(self.checks):
            raise ValueError("duplicate practice check")
        for spec in self.checks:
            module_path = spec.module.replace(".", "/") + ".py"
            if module_path not in self.files or module_path in self.editable:
                raise ValueError("check modules must exist and remain protected")
        for dep in self.depends_on:
            _identifier(dep)
        object.__setattr__(self, "files", MappingProxyType(dict(self.files)))
        object.__setattr__(self, "editable", tuple(self.editable))
        object.__setattr__(self, "checks", tuple(self.checks))
        object.__setattr__(self, "depends_on", tuple(self.depends_on))


def _ordered(tasks):
    if not 1 <= len(tasks) <= 20 or len({task.id for task in tasks}) != len(tasks):
        raise ValueError("invalid practice task list")
    lookup = {task.id: task for task in tasks}
    visited, active, ordered = set(), set(), []
    def visit(task):
        if task.id in active:
            raise ValueError("cyclic practice dependencies")
        if task.id in visited:
            return
        active.add(task.id)
        for dep in task.depends_on:
            if dep not in lookup:
                raise ValueError("unknown practice dependency")
            visit(lookup[dep])
        active.remove(task.id)
        visited.add(task.id)
        ordered.append(task)
    for task in tasks:
        visit(task)
    return ordered


def _checks(task, files, check):
    results = []
    with tempfile.TemporaryDirectory(prefix="gamma-practice-") as directory:
        workspace = Path(directory)
        for relative, source in files.items():
            target = workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source, encoding="utf-8")
        for spec in task.checks:
            try:
                raw = check(workspace, spec)
                valid = isinstance(raw, dict) and raw.get("name") == spec.name and raw.get("kind") == spec.kind
                passed = valid and raw.get("passed") is True and type(raw.get("exit_code")) is int and raw["exit_code"] == 0
                results.append({"name": spec.name, "kind": spec.kind, "passed": bool(passed),
                                "exit_code": raw.get("exit_code", -1) if valid else -1,
                                "infrastructure_error": raw.get("infrastructure_error") is True if valid else True,
                                "output_tail": str(raw.get("output_tail", ""))[-2000:] if valid else "invalid_check_result"})
            except Exception as exc:
                results.append({"name": spec.name, "kind": spec.kind, "passed": False,
                                "infrastructure_error": True,
                                "exit_code": -1, "output_tail": "check_error:" + type(exc).__name__})
        if any(p.is_symlink() for p in workspace.rglob("*")):
            raise ValueError("practice check modified the workspace")
        actual = {str(p.relative_to(workspace)).replace("\\", "/"): p.read_text(encoding="utf-8")
                  for p in workspace.rglob("*.py")}
        if actual != dict(files):
            raise ValueError("practice check modified the workspace")
    return results


def run_practice(tasks, *, propose, check, maximum_attempts=5, budget_seconds=300,
                 evaluate=None, collect=None):
    started = time.monotonic()
    if type(maximum_attempts) is not int or not 1 <= maximum_attempts <= 10:
        raise ValueError("invalid practice attempt limit")
    if not math.isfinite(budget_seconds) or not 1 <= budget_seconds <= 1800:
        raise ValueError("invalid practice time budget")
    ordered = _ordered(tasks)
    deadline = time.monotonic() + budget_seconds
    outcomes = {}
    for task in ordered:
        item = {"id": task.id, "skill": task.skill, "status": "needs_checker", "reason": "attempt_limit",
                "attempt_count": 0, "checks": [], "source_sha256": "", "attempts": []}
        outcomes[task.id] = item
        if any(outcomes[dep]["status"] != "verified_practice" for dep in task.depends_on):
            item.update(status="blocked", reason="dependency_unverified")
            continue
        files = dict(task.files)
        feedback = []
        if time.monotonic() >= deadline:
            item.update(status="blocked", reason="budget_exhausted")
            continue
        checked = _checks(task, files, check)
        feedback.append({"stage": "baseline", "checks": checked})
        item["checks"] = checked
        item["source_sha256"] = _digest(files)
        if any(c.get("infrastructure_error") for c in checked):
            item.update(status="blocked", reason="verification_unavailable")
            continue
        if all(c["passed"] for c in checked):
            item.update(status="verified_practice", reason="baseline_verified")
        for attempt in range(1, maximum_attempts + 1) if item["status"] != "verified_practice" else ():
            if time.monotonic() >= deadline:
                item["reason"] = "budget_exhausted"
                break
            item["attempt_count"] = attempt
            reason = "check_failed"
            try:
                context = [{"latest_executed_checks": item["checks"]}, *feedback[-1:]]
                edits = propose(task, {p: files[p] for p in task.editable}, context)
                if not isinstance(edits, dict) or not edits or not set(edits) <= set(task.editable):
                    reason = "proposal_scope_violation"
                    raise ValueError(reason)
                if any(not isinstance(source, str) for source in edits.values()):
                    reason = "proposal_invalid_source"
                    raise ValueError(reason)
                candidate = {**files, **edits}
                if candidate == files or sum(len(s.encode()) for s in candidate.values()) > 65536:
                    reason = "proposal_unchanged_or_oversized"
                    raise ValueError(reason)
                for path, source in edits.items():
                    ast.parse(source, filename=path)
                if time.monotonic() >= deadline:
                    reason = "budget_exhausted"
                    raise ValueError(reason)
                files = candidate
                checked = _checks(task, files, check)
                item["checks"] = checked
                item["source_sha256"] = _digest(files)
                if any(c.get("infrastructure_error") for c in checked):
                    item.update(status="blocked", reason="verification_unavailable")
                elif all(c["passed"] for c in checked):
                    item.update(status="verified_practice", reason="checks_passed")
                evidence = {"attempt": attempt, "reason": item["reason"] if item["status"] == "verified_practice" else reason,
                            "checks": checked, "source_sha256": item["source_sha256"]}
            except Exception as exc:
                from .learner import PracticeInferenceUnavailable
                if isinstance(exc, PracticeInferenceUnavailable):
                    item.update(status="blocked", reason="inference_unavailable_no_replay")
                    reason = item["reason"]
                if reason == "check_failed":
                    reason = "proposal_error:" + type(exc).__name__
                evidence = {"attempt": attempt, "reason": reason, "checks": []}
            item["attempts"].append(evidence)
            feedback.append(evidence)
            if item["status"] in {"verified_practice", "blocked"}:
                break
            item["reason"] = reason
        if item["status"] == "verified_practice" and evaluate is not None:
            if time.monotonic() >= deadline:
                item.update(status="blocked", reason="evaluation_budget_exhausted")
            else:
                try:
                    evaluation = evaluate(task, dict(files))
                    if not isinstance(evaluation, dict) or evaluation.get("source_sha256") != item["source_sha256"]:
                        raise ValueError("evaluation source mismatch")
                    # Only bounded aggregate evidence is retained; withheld cases never
                    # become learner feedback or an automatic repair prompt.
                    passed = evaluation.get("passed") is True and evaluation.get("status") == "passed"
                    item["evaluation"] = {"passed": passed, "status": evaluation.get("status"),
                                          "source_sha256": evaluation["source_sha256"], "checker_version": 1}
                    if not passed:
                        item.update(status="blocked" if evaluation.get("status") == "blocked" else "needs_checker",
                                    reason="independent_evaluation_unavailable" if evaluation.get("status") == "blocked" else "independent_evaluation_failed")
                except Exception:
                    item.update(status="blocked", reason="independent_evaluation_unavailable")
        if item["status"] == "verified_practice" and collect is not None:
            collect(task, dict(files), dict(item))
    return {"version": 1, "id": "practice-" + uuid.uuid4().hex,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "passed": all(t["status"] == "verified_practice" for t in outcomes.values()),
            "production_verified": False, "duration_ms": round((time.monotonic() - started) * 1000, 3),
            "tasks": list(outcomes.values())}


def record_run(root, result):
    root = Path(root)
    _identifier(result["id"])
    raw = json.dumps(result, ensure_ascii=False, sort_keys=True)
    if len(raw.encode()) > 256000 or result.get("production_verified") is not False:
        raise ValueError("invalid practice record")
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError("invalid practice storage")
    directory = root / result["id"]
    directory.mkdir(parents=True, exist_ok=False)
    temporary = directory / "result.tmp"
    temporary.write_text(raw, encoding="utf-8")
    target = directory / "result.json"
    temporary.replace(target)
    return target


def capability_summary(root):
    root = Path(root)
    records = []
    if root.is_dir() and not any(p.is_symlink() for p in (root, *root.parents)):
        # Scan a bounded number even when many records exist.
        entries = []
        for entry in root.iterdir():
            if len(entries) >= 200:
                break
            if not entry.is_symlink() and entry.is_dir():
                entries.append(entry)
        for entry in sorted(entries, key=lambda p: p.stat().st_mtime, reverse=True)[:50]:
            path = entry / "result.json"
            try:
                if path.is_symlink() or path.stat().st_size > 256000:
                    continue
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("version") != 1 or data.get("production_verified") is not False:
                    continue
                _identifier(data["id"])
                tasks = []
                if not isinstance(data["tasks"], list) or not 1 <= len(data["tasks"]) <= 20:
                    continue
                for task in data["tasks"]:
                    _identifier(task["id"])
                    _identifier(task["skill"])
                    status = task["status"]
                    if status not in {"verified_practice", "needs_checker", "blocked"}:
                        raise ValueError("invalid practice status")
                    # Persisted claims must still contain both successful check kinds.
                    if status == "verified_practice" and not (
                        {c["kind"] for c in task["checks"]} == {"regression", "feature"}
                        and all(c["passed"] is True and type(c["exit_code"]) is int and c["exit_code"] == 0 for c in task["checks"])
                        and re.fullmatch(r"[a-f0-9]{64}", task["source_sha256"])):
                        raise ValueError("missing practice verification")
                    tasks.append({"id": task["id"], "skill": task["skill"], "status": status,
                                  "independent_evaluation_passed": status == "verified_practice" and task.get("evaluation", {}).get("passed") is True
                                      and task.get("evaluation", {}).get("status") == "passed"
                                      and task.get("evaluation", {}).get("source_sha256") == task["source_sha256"],
                                  "repair_count": min(10, sum(a.get("reason") != "checks_passed" for a in task.get("attempts", [])[:10])),
                                  "attempt_count": min(10, max(0, int(task["attempt_count"])))})
                records.append({"id": data["id"], "generated_at": str(data["generated_at"])[:80],
                                "model": str(data.get("model", "unspecified"))[:200], "tasks": tasks,
                                "passed": all(t["status"] == "verified_practice" for t in tasks)})
            except (OSError, UnicodeError, ValueError, KeyError, TypeError, AttributeError):
                continue
    skills = {}
    for record in records:
        for task in record["tasks"]:
            skill = skills.setdefault(task["skill"], {"verified": 0, "failed": 0, "blocked": 0, "repairs": 0, "independently_evaluated": 0, "state": "practice_only"})
            key = {"verified_practice": "verified", "needs_checker": "failed", "blocked": "blocked"}[task["status"]]
            skill[key] += 1
            skill["repairs"] += task["repair_count"]
            skill["independently_evaluated"] += int(task["independent_evaluation_passed"])
    return {"run_count": len(records), "production_verified": False, "skills": skills,
            "recent_runs": records[:10], "suggested_practice": sorted(k for k,v in skills.items() if v["failed"] or v["repairs"]),
            "scope": "fictional_offline_exercises", "model_training_enabled": False}
