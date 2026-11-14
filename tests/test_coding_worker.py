"""The background worker runs queued owner goals through the workflow (fake workflow, no model)."""
import json
import time

import pytest

from gamma.coding import autonomous, autonomous_store as store
from gamma.coding.workflow import WorkflowReport
from gamma.tools.coding import _coding_progress, coding_status_reply


def _queue(tmp_path, budget=1800):
    root = tmp_path / "jobs"
    return root, store.create_job(root, "Make add() in calc.py add", "coder", budget)


def _report(out_dir, status="awaiting_owner_review"):
    diff = out_dir / "proposal.diff"
    diff.write_text("--- a/src/gamma/calc.py\n+++ b/src/gamma/calc.py\n")
    return WorkflowReport(
        "goal", status, "All tasks done and the full suite passes.",
        plan=[{"id": "t1", "title": "Fix add", "files": ["src/gamma/calc.py"], "check": "tests/test_calc.py"},
              {"id": "t2", "title": "Docs", "files": ["src/gamma/calc.py"], "check": "tests/test_calc.py"}],
        tasks=[{"id": "t1", "status": "done", "summary": "add adds", "steps": 4, "changed": ["src/gamma/calc.py"]}],
        suite={"passed": True, "output": "1 passed"}, diff_path=str(diff))


def test_finished_goal_waits_for_owner_review_with_its_proposal(tmp_path):
    root, record = _queue(tmp_path)
    seen = {}
    def run(goal, project, model, out_dir, *, checkpoint, on_event):
        seen.update(goal=goal, model=model)
        on_event("working", "Task t1: Fix add", task="t1")
        return _report(out_dir)
    done = autonomous.execute(root, record["id"], project=tmp_path, endpoint="http://127.0.0.1:11434",
                              run=run, model="fake-model")
    assert seen == {"goal": "Make add() in calc.py add", "model": "fake-model"}
    assert done["status"] == "awaiting_owner_review" and done["proposal"] == "proposal.diff"
    assert [t["status"] for t in done["tasks"]] == ["done", "pending"] and done["tasks"][0]["title"] == "Fix add"
    assert done["suite"]["passed"] and "working" in [e["stage"] for e in done["events"]]
    assert "calc.py" in store.read_proposal(root, record["id"])
    assert store.read_job(root, record["id"])["status"] == "awaiting_owner_review"


@pytest.mark.parametrize("control, status", [("stop", "stopped"), ("budget", "blocked")])
def test_stop_and_spent_budget_end_the_job_before_any_work(tmp_path, control, status):
    root, record = _queue(tmp_path, budget=60)
    if control == "stop":
        store.control_job(root, record["id"], "stop")
    else:
        state = store.read_job(root, record["id"]); state["created_at"] = time.time() - 120
        store.write_json(store.job_path(root, record["id"]) / "state.json", state)
    ran = []
    done = autonomous.execute(root, record["id"], project=tmp_path, endpoint="http://127.0.0.1:11434",
                              run=lambda *a, **k: ran.append(1), model="fake-model")
    assert done["status"] == status and not ran and done["completed_at"]


def test_pause_waits_and_resume_continues(tmp_path, monkeypatch):
    root, record = _queue(tmp_path)
    job = autonomous.Job(root, record["id"])
    store.control_job(root, record["id"], "pause")
    sleeps = []
    def sleep(_seconds):
        sleeps.append(1)
        store.write_json(job.path / "control.json", {"action": "resume", "at": time.time()})
    monkeypatch.setattr(autonomous.time, "sleep", sleep)
    job.record["status"] = "running"
    job.checkpoint()
    assert sleeps and job.record["status"] == "running"
    assert [e["stage"] for e in job.record["events"]] == ["paused", "resumed"]


def test_a_failing_workflow_is_recorded_not_raised(tmp_path):
    root, record = _queue(tmp_path)
    def run(*_a, **_k):
        raise RuntimeError("sandbox unavailable")
    done = autonomous.execute(root, record["id"], project=tmp_path, endpoint="http://127.0.0.1:11434",
                              run=run, model="fake-model")
    assert done["status"] == "blocked" and done["reason"] == "sandbox unavailable"


def test_work_left_running_by_a_dead_worker_is_not_replayed(tmp_path):
    root, record = _queue(tmp_path)
    state = store.read_job(root, record["id"]); state["status"] = "running"
    store.write_json(store.job_path(root, record["id"]) / "state.json", state)
    autonomous.mark_interrupted(root)
    assert store.read_job(root, record["id"])["status"] == "interrupted"
    ran = []
    autonomous.execute(root, record["id"], project=tmp_path, endpoint="x", run=lambda *a, **k: ran.append(1), model="m")
    assert not ran


def test_only_one_goal_at_a_time(tmp_path):
    root, _ = _queue(tmp_path)
    with pytest.raises(ValueError, match="already active"):
        store.create_job(root, "Another goal that is long enough", "coder", 1800)


def test_status_for_shana_has_counts_but_not_the_goal_or_files(tmp_path):
    root, record = _queue(tmp_path)
    done = autonomous.execute(root, record["id"], project=tmp_path, endpoint="x",
                              run=lambda goal, project, model, out_dir, **_: _report(out_dir), model="m")
    row = _coding_progress(done)
    assert row == {"id": record["id"], "status": "awaiting_owner_review", "stage": "awaiting_owner_review",
                   "task_count": 2, "done_count": 1, "has_proposal": True}
    assert "calc" not in json.dumps(row) and "add" not in json.dumps(row)
    reply = coding_status_reply([row])
    assert "1 of 2 tasks" in reply and "review" in reply
    assert coding_status_reply([]) == "No coding goals are recorded yet."
    assert "decision" in coding_status_reply([{"status": "needs_owner"}])
    assert "can't read" in coding_status_reply([{"status": "<script>"}])


def _git_repo(tmp_path):
    import subprocess
    repo = tmp_path / "repo"; (repo / "src/gamma").mkdir(parents=True)
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True).stdout
    git("init", "-q"); git("config", "user.email", "t@t"); git("config", "user.name", "t")
    (repo / "src/gamma/calc.py").write_text("def add(a, b):\n    return a - b\n")
    git("add", "-A"); git("commit", "-qm", "init")
    return repo, git


def _finished_job(tmp_path, diff):
    root, record = _queue(tmp_path)
    def run(goal, project, model, out_dir, **_):
        report = _report(out_dir)
        (out_dir / "proposal.diff").write_text(diff)
        return report
    return root, autonomous.execute(root, record["id"], project=tmp_path, endpoint="x", run=run, model="m")


GOOD_DIFF = """diff --git a/src/gamma/calc.py b/src/gamma/calc.py
--- a/src/gamma/calc.py
+++ b/src/gamma/calc.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a - b
+    return a + b
"""


def test_applying_a_proposal_commits_a_branch_and_leaves_the_checkout_alone(tmp_path):
    from gamma.coding.proposal import apply_to_branch
    repo, git = _git_repo(tmp_path)
    root, job = _finished_job(tmp_path, GOOD_DIFF)
    head_before = git("rev-parse", "HEAD")
    result = apply_to_branch(repo, root, job["id"])
    assert result["branch"].startswith("shana/")
    assert "a + b" in git("show", f"{result['branch']}:src/gamma/calc.py")
    assert git("rev-parse", "HEAD") == head_before and "a - b" in (repo / "src/gamma/calc.py").read_text()
    assert git("worktree", "list").count("\n") == 1
    assert store.read_job(root, job["id"])["status"] == "applied"
    with pytest.raises(ValueError, match="waiting for review"):
        apply_to_branch(repo, root, job["id"])


def test_a_stale_proposal_creates_nothing(tmp_path):
    from gamma.coding.proposal import apply_to_branch
    repo, git = _git_repo(tmp_path)
    root, job = _finished_job(tmp_path, GOOD_DIFF.replace("return a - b", "return a * b"))
    with pytest.raises(ValueError, match="no longer applies"):
        apply_to_branch(repo, root, job["id"])
    assert "shana/" not in git("branch", "--list") and git("worktree", "list").count("\n") == 1
    assert store.read_job(root, job["id"])["status"] == "awaiting_owner_review"
