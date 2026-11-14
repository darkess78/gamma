"""Shana splits a coding goal into small tasks and works each one with tools (scripted model)."""
import json
import subprocess
from pathlib import Path

from gamma.coding.split import MAX_FILES, validate
from gamma.coding.task_loop import TaskLoop
from gamma.coding.split import Task
from gamma.coding.workflow import run


class Script:
    """A fake model that replies from a list, recording prompts."""

    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def generate_reply(self, *, system_prompt, user_text):
        self.prompts.append(user_text)
        return type("R", (), {"text": json.dumps(self.replies.pop(0)) if self.replies else "{}"})()


def test_split_rules_are_enforced():
    big = {"tasks": [{"id": "t1", "title": "x", "detail": "do it", "files": [f"f{i}.py" for i in range(MAX_FILES + 1)],
                      "check": "tests/test_x.py"}]}
    assert any("at most" in p for p in validate(big).problems)
    no_check = {"tasks": [{"id": "t1", "title": "x", "detail": "do it", "files": ["a.py"], "check": "run it"}]}
    assert any("pytest check" in p for p in validate(no_check).problems)
    bad_order = {"tasks": [{"id": "t1", "detail": "a", "files": ["a.py"], "check": "tests/test_a.py", "depends_on": ["t2"]},
                           {"id": "t2", "detail": "b", "files": ["b.py"], "check": "tests/test_b.py"}]}
    assert any("doesn't come before" in p for p in validate(bad_order).problems)
    assert validate({"question": "Which channel should this use?"}).question


def test_task_loop_needs_a_passing_check_and_stays_in_its_files(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    checks = []
    def check(_test):
        checks.append(1)
        return {"passed": "a + b" in (tmp_path / "calc.py").read_text(), "output": ""}
    model = Script([
        {"tool": "read", "path": "calc.py"},
        {"tool": "replace", "path": "other.py", "old": "x", "new": "y"},
        {"tool": "done", "summary": "too early"},
        {"tool": "replace", "path": "calc.py", "old": "a - b", "new": "a + b"},
        {"tool": "test"},
        {"tool": "done", "summary": "fixed add"},
    ])
    task = Task("t1", "Fix add", "add() subtracts", ["calc.py"], "tests/test_calc.py")
    result = TaskLoop(model, tmp_path, check).run(task)
    assert result.status == "done" and result.changed == {"calc.py"}
    assert "Not allowed" in model.prompts[2] and "check has not passed" in model.prompts[3]
    assert (tmp_path / "calc.py").read_text() == "def add(a, b):\n    return a + b\n"


def test_workflow_produces_a_diff_for_review(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    git("init", "-q"); git("config", "user.email", "t@t"); git("config", "user.name", "t")
    (repo / "src").mkdir(); (repo / "tests").mkdir()
    (repo / "src" / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    git("add", "-A"); git("commit", "-qm", "init")
    model = Script([
        {"tasks": [{"id": "t1", "title": "Test add", "detail": "Add a failing test for add", "files": ["tests/test_calc.py"],
                    "check": "tests/test_calc.py"},
                   {"id": "t2", "title": "Fix add", "detail": "Make add() add", "files": ["src/calc.py"],
                    "check": "tests/test_calc.py", "depends_on": ["t1"]}]},
        {"tool": "write", "path": "tests/test_calc.py", "content": "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"},
        {"tool": "done", "summary": "added test"},  # refused: no passing check yet
        {"tool": "give_up", "reason": "the test fails until t2 fixes add, which is expected"},
    ])
    report = run("Fix add() in calc.py", repo, model, tmp_path / "out",
                 run_tests=lambda ws, args, t: {"passed": "a + b" in (ws / "src/calc.py").read_text(), "output": ""})
    assert report.status == "blocked" and "t1 gave up" in report.message
    assert "tests/test_calc.py" in Path(report.diff_path).read_text()
    assert json.loads((tmp_path / "out" / "report.json").read_text())["status"] == "blocked"
    assert subprocess.run(["git", "-C", str(repo), "worktree", "list"], capture_output=True, text=True).stdout.count("\n") == 1
