"""Working one small task step by step, like a person at a terminal.

The model picks one tool per step and sees its result before the next:

  read     {"path", "start"?, "end"?}      show part of a file (with line numbers)
  search   {"pattern", "path"?}            find a regex in the workspace
  replace  {"path", "old", "new"}          replace one exact, unique snippet
  write    {"path", "content"}             create a new file (not overwrite)
  test     {}                              run this task's check
  done     {"summary"}                     finish; only accepted after the check passed
  give_up  {"reason"}                      stop and explain what's blocking

Edits are limited to the task's files. Only the last few results stay in the
prompt, so a small model's context isn't flooded. The loop stops after
MAX_STEPS, and an unparseable reply counts as a step.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .split import Task

MAX_STEPS = 16
KEEP_RESULTS = 6
MAX_OUTPUT = 4000

LOOP_GUIDE = """You are Shana working on one small coding task in a repository. Use one tool per reply.
Reply with JSON only: {"tool": "<name>", ...arguments}. Tools:
read {"path","start","end"} | search {"pattern","path"} | replace {"path","old","new"} |
write {"path","content"} | test {} | done {"summary"} | give_up {"reason"}.
Read before you edit. "old" must match the file exactly and only once. You may only edit the task's files.
Run test after editing. Say done only after the test passes. If you are stuck, give_up and explain why."""


@dataclass
class TaskResult:
    task_id: str
    status: str  # done | gave_up | out_of_steps
    summary: str
    steps: int
    changed: set[str] = field(default_factory=set)
    transcript: list[dict] = field(default_factory=list)


class TaskLoop:
    def __init__(self, model, workspace: Path, run_check: Callable[[str], dict], *, max_steps: int = MAX_STEPS) -> None:
        self.model = model
        self.root = workspace.resolve()
        self.run_check = run_check
        self.max_steps = max_steps

    def run(self, task: Task, *, notes: str = "") -> TaskResult:
        history: list[str] = []
        transcript: list[dict] = []
        changed: set[str] = set()
        check_passed = False
        for step in range(1, self.max_steps + 1):
            prompt = (f"Task {task.id}: {task.title}\n{task.detail}\nFiles you may edit: {', '.join(task.files)}\n"
                      f"Check: {task.check}\n{notes}\n\nRecent results:\n" + ("\n\n".join(history[-KEEP_RESULTS:]) or "(none yet)"))
            reply = self.model.generate_reply(system_prompt=LOOP_GUIDE, user_text=prompt)
            call = _json(getattr(reply, "text", str(reply)))
            tool = str(call.get("tool") or "")
            if tool == "done":
                if not check_passed:
                    result = "Not done: the check has not passed since your last edit. Run test."
                else:
                    transcript.append({"step": step, "tool": tool})
                    return TaskResult(task.id, "done", str(call.get("summary") or "")[:500], step, changed, transcript)
            elif tool == "give_up":
                transcript.append({"step": step, "tool": tool})
                return TaskResult(task.id, "gave_up", str(call.get("reason") or "")[:500], step, changed, transcript)
            else:
                result, edited, passed = self._use(tool, call, task)
                if edited:
                    changed.add(edited)
                    check_passed = False
                if passed is not None:
                    check_passed = passed
            transcript.append({"step": step, "tool": tool or "(unparseable)", "result": result[:300]})
            history.append(f"[{step}] {tool or 'unparseable reply'} -> {result}")
        return TaskResult(task.id, "out_of_steps", "ran out of steps", self.max_steps, changed, transcript)

    # ---- tools -----------------------------------------------------------

    def _path(self, raw: object) -> Path | None:
        if not isinstance(raw, str) or not raw or raw.startswith("/") or ".." in Path(raw).parts:
            return None
        path = (self.root / raw).resolve()
        return path if self.root in path.parents or path == self.root else None

    def _use(self, tool: str, call: dict, task: Task) -> tuple[str, str | None, bool | None]:
        if tool == "read":
            path = self._path(call.get("path"))
            if path is None or not path.is_file():
                return "No such file.", None, None
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            start = max(1, int(call.get("start") or 1)); end = min(len(lines), int(call.get("end") or start + 119))
            body = "\n".join(f"{n}: {lines[n - 1]}" for n in range(start, end + 1))
            return _cap(f"{call['path']} lines {start}-{end} of {len(lines)}\n{body}"), None, None
        if tool == "search":
            try:
                pattern = re.compile(str(call.get("pattern") or ""))
            except re.error as exc:
                return f"Bad pattern: {exc}", None, None
            base = self._path(call.get("path") or ".") or self.root
            hits = []
            for file in sorted(base.rglob("*.py") if base.is_dir() else [base]):
                if ".git" in file.parts:
                    continue
                for n, line in enumerate(file.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if pattern.search(line):
                        hits.append(f"{file.relative_to(self.root)}:{n}: {line.strip()[:160]}")
                if len(hits) >= 40:
                    break
            return _cap("\n".join(hits) or "No matches."), None, None
        if tool in {"replace", "write"}:
            rel = call.get("path")
            if rel not in task.files:
                return f"Not allowed: you may only edit {', '.join(task.files)}.", None, None
            path = self._path(rel)
            if path is None:
                return "Bad path.", None, None
            if tool == "write":
                if path.exists():
                    return "File exists; use replace to edit it.", None, None
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(str(call.get("content") or ""), encoding="utf-8")
                return f"Created {rel}.", rel, None
            if not path.is_file():
                return "No such file; use write to create it.", None, None
            text = path.read_text(encoding="utf-8")
            old, new = str(call.get("old") or ""), str(call.get("new") or "")
            count = text.count(old) if old else 0
            if count != 1:
                return f"'old' matched {count} times; it must match exactly once. Read the file again.", None, None
            path.write_text(text.replace(old, new, 1), encoding="utf-8")
            return f"Replaced 1 snippet in {rel}.", rel, None
        if tool == "test":
            outcome = self.run_check(task.check)
            passed = bool(outcome.get("passed"))
            return _cap(("PASSED. " if passed else "FAILED. ") + str(outcome.get("output") or "")), None, passed
        return f"Unknown tool {tool!r}. Use one of read, search, replace, write, test, done, give_up.", None, None


def _json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    try:
        value = json.loads(text[start:end + 1]) if start >= 0 else {}
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _cap(text: str) -> str:
    return text if len(text) <= MAX_OUTPUT else text[:MAX_OUTPUT] + f"\n... ({len(text) - MAX_OUTPUT} more characters)"
