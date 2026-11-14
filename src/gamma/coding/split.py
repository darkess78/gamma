"""Splitting a coding goal into small tasks a local model can finish.

Local models that fit Gamma PC do much better on one small, checkable change
than on a whole feature. Shana breaks an owner's goal into tasks that each:

- change at most MAX_FILES files (tests included),
- name the check that proves the task is done (a test file or test id),
- say which earlier tasks they depend on.

The model proposes the split as JSON; code enforces the rules. A task that is
still too big is split again once; a goal that can't be split into valid
tasks is returned with the problems so Shana can ask the owner instead of
guessing.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable

MAX_TASKS = 6
MAX_FILES = 3

SPLIT_GUIDE = f"""You plan coding work for a small local model that can only handle one small change at a time.
Split the goal into at most {MAX_TASKS} tasks, in order. Each task must:
- change at most {MAX_FILES} files, including any test file it adds or edits;
- name exactly one check that proves it is done: a pytest file or test id (add a new test when needed);
- be understandable on its own: say what to change and why, mention function or class names;
- list the ids of earlier tasks it depends on.
Only change modules under src/gamma that aren't protected (coding, safety, persona, tools, api, identity,
supervisor, config) and NEW test files tests/test_*.py; never edit existing tests.
A task is finished only when its check passes, so each task adds or updates its test AND makes the change that passes it; never plan a test-only task. Do not plan refactors the goal didn't ask for.
If the goal is unclear or needs a decision from the owner, return {{"question": "..."}} instead.
Reply with JSON only: {{"tasks": [{{"id": "t1", "title": "...", "detail": "...", "files": ["..."],
"check": "tests/test_x.py::test_y", "depends_on": []}}]}}"""


@dataclass
class Task:
    id: str
    title: str
    detail: str
    files: list[str]
    check: str
    depends_on: list[str] = field(default_factory=list)


@dataclass
class Plan:
    tasks: list[Task]
    problems: list[str]
    question: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.tasks) and not self.problems and not self.question


def _json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    try:
        value = json.loads(text[start:end + 1]) if start >= 0 else {}
    except json.JSONDecodeError:
        value = {}
    return value if isinstance(value, dict) else {}


_SAFE_PATH = re.compile(r"^(?!/)(?!.*\.\.)[\w./-]+$")


def validate(raw: dict, *, known_files: set[str] | None = None,
             editable: Callable[[str], bool] | None = None) -> Plan:
    if isinstance(raw.get("question"), str) and raw["question"].strip():
        return Plan([], [], raw["question"].strip())
    tasks, problems = [], []
    items = raw.get("tasks") if isinstance(raw.get("tasks"), list) else []
    if not items:
        problems.append("no tasks")
    if len(items) > MAX_TASKS:
        problems.append(f"{len(items)} tasks; at most {MAX_TASKS}")
    seen: set[str] = set()
    planned: set[str] = set()  # files earlier tasks will create or change
    for index, item in enumerate(items[:MAX_TASKS]):
        if not isinstance(item, dict):
            problems.append(f"task {index + 1} is not an object"); continue
        tid = str(item.get("id") or f"t{index + 1}")
        files = [f for f in item.get("files") or [] if isinstance(f, str)]
        check = str(item.get("check") or "").strip()
        depends = [d for d in item.get("depends_on") or [] if isinstance(d, str)]
        label = f"task {tid}"
        if not files:
            problems.append(f"{label} names no files")
        if len(files) > MAX_FILES:
            problems.append(f"{label} changes {len(files)} files; at most {MAX_FILES}")
        if any(not _SAFE_PATH.match(f) for f in files):
            problems.append(f"{label} has an unsafe path")
        elif editable is not None:
            blocked = [f for f in files if not editable(f)]
            if blocked:
                problems.append(f"{label} may not change {', '.join(blocked)} (protected, or an existing test)")
        if not check.startswith("tests/"):
            problems.append(f"{label} has no pytest check under tests/")
        if known_files is not None and check.split("::")[0] not in known_files | planned | set(files):
            problems.append(f"{label} check {check.split('::')[0]} neither exists nor is created by this or an earlier task")
        if any(d not in seen for d in depends):
            problems.append(f"{label} depends on a task that doesn't come before it")
        if not str(item.get("detail") or item.get("title") or "").strip():
            problems.append(f"{label} doesn't say what to do")
        seen.add(tid)
        planned.update(files)
        tasks.append(Task(tid, str(item.get("title") or tid)[:120], str(item.get("detail") or "")[:1500],
                          files, check, depends))
    return Plan(tasks, problems)


def split_goal(model, goal: str, *, repo_overview: str, known_files: set[str] | None = None,
               editable: Callable[[str], bool] | None = None) -> Plan:
    """Ask the model for a split; retry once with the problems if it breaks the rules."""
    user = f"Goal from the owner:\n{goal}\n\nRepository overview:\n{repo_overview[:6000]}"
    plan = validate(_json(_ask(model, SPLIT_GUIDE, user)), known_files=known_files, editable=editable)
    if plan.ok or plan.question:
        return plan
    retry = f"{user}\n\nYour previous split broke these rules:\n- " + "\n- ".join(plan.problems)
    return validate(_json(_ask(model, SPLIT_GUIDE, retry)), known_files=known_files, editable=editable)


def _ask(model, system: str, user: str) -> str:
    reply = model.generate_reply(system_prompt=system, user_text=user)
    return getattr(reply, "text", str(reply))
