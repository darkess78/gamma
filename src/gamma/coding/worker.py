"""Idle-only bounded practice. No project mutation or model-weight training."""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path



def default_policy():
    return {"enabled": False, "model": "qwen3.8:27b", "interval_seconds": 3600,
            "maximum_runs_per_day": 2, "maximum_attempts": 5, "budget_seconds": 300}


def read_policy(root):
    path = Path(root) / "policy.json"
    if path.is_symlink() or not path.is_file():
        return default_policy()
    if path.stat().st_size > 4096:
        raise ValueError("invalid practice policy")
    data = json.loads(path.read_text(encoding="utf-8"))
    expected = default_policy()
    if set(data) != set(expected) or type(data["enabled"]) is not bool:
        raise ValueError("invalid practice policy")
    from .learner import LocalPracticeLearner
    LocalPracticeLearner(endpoint="http://127.0.0.1:11434", model=data["model"])
    for name, low, high in (("interval_seconds", 600, 86400), ("maximum_runs_per_day", 1, 10),
                            ("maximum_attempts", 1, 10), ("budget_seconds", 30, 1800)):
        if type(data[name]) is not int or not low <= data[name] <= high:
            raise ValueError("invalid practice policy bounds")
    return data


def write_policy(root, data):
    root = Path(root)
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError("invalid coding policy storage")
    root.mkdir(parents=True, exist_ok=True)
    # Validate in an isolated directory before replacing the active policy.
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        (Path(directory) / "policy.json").write_text(json.dumps(data), encoding="utf-8")
        validated = read_policy(Path(directory))
    _write(root / "policy.json", validated)
    return validated


def _write(path, data):
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        json.dump(data, stream)
        temporary = Path(stream.name)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def admission_reason(*, policy, presence, assigned_work, inference_busy, last_started, daily_runs, now):
    if not policy["enabled"]:
        return "disabled"
    if assigned_work:
        return "assigned_work_has_priority"
    if presence.get("mode") != "sleep" or presence.get("activity", {}).get("current_turn_id"):
        return "conversation_has_priority"
    if inference_busy:
        return "local_inference_occupied"
    if daily_runs >= policy["maximum_runs_per_day"]:
        return "daily_budget_reached"
    if last_started is not None and now - last_started < policy["interval_seconds"]:
        return "practice_interval"
    return "ready"


def tick(data_dir):
    from ..config import settings
    from ..improvement.work_queue import ImprovementWorkStore
    from .cli import main as practice_main
    from .learner import LocalPracticeLearner
    root = Path(data_dir) / "coding"
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError("invalid coding storage")
    root.mkdir(parents=True, exist_ok=True)
    policy = read_policy(root)
    state_path = root / "idle-state.json"
    state = {}
    if state_path.exists():
        if state_path.is_symlink() or state_path.stat().st_size > 4096:
            raise ValueError("invalid idle practice state")
        state = json.loads(state_path.read_text())
    now = time.time()
    day = datetime.now(timezone.utc).date().isoformat()
    daily_runs = int(state.get("daily_runs", 0)) if state.get("day") == day else 0
    presence_path = Path(data_dir) / "runtime" / "presence" / "state.json"
    presence = {}
    if presence_path.is_file() and not presence_path.is_symlink() and presence_path.stat().st_size <= 65536:
        presence = json.loads(presence_path.read_text())
    assigned = any(r.status in {"queued", "running", "paused"} for r in
                   ImprovementWorkStore(Path(data_dir) / "improvement" / "control").list(limit=100))
    assigned = assigned or (root / "practice" / "practice.lock").exists()
    from .repository import list_jobs
    assigned = assigned or any(j.get('status') in {'queued', 'running'} for j in list_jobs(root / 'projects'))
    from .autonomous_store import list_jobs as autonomous_jobs, ACTIVE
    assigned = assigned or any(j.get('status') in ACTIVE for j in autonomous_jobs(root / 'autonomous'))
    from .candidate_resources import candidate_busy
    assigned = assigned or candidate_busy(root)
    busy = True
    if policy["enabled"] and not assigned and presence.get("mode") == "sleep":
        try:
            LocalPracticeLearner(endpoint=settings.local_llm_endpoint, model=policy["model"], timeout_seconds=5).require_idle()
            busy = False
        except (OSError, ValueError, RuntimeError):
            pass
    reason = admission_reason(policy=policy, presence=presence, assigned_work=assigned,
                              inference_busy=busy, last_started=state.get("last_started"),
                              daily_runs=daily_runs, now=now)
    state.update(day=day, daily_runs=daily_runs, reason=reason, checked_at=now)
    if reason == "ready":
        state.update(last_started=now, daily_runs=daily_runs + 1, reason="running")
        _write(state_path, state)
        # Practice remains limited to the fixed curriculum.
        exit_code = practice_main(["practice", "--idle", "--model", policy["model"], "--maximum-attempts",
                                   str(policy["maximum_attempts"]), "--budget-seconds", str(policy["budget_seconds"])])
        state.update(reason="practice_finished" if exit_code == 0 else "practice_needs_attention")
    _write(state_path, state)
    return state


def main():
    from ..config import settings
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    while True:
        try:
            state = tick(settings.data_dir)
            print(json.dumps(state), flush=True)
        except Exception as exc:
            print(json.dumps({"reason": "idle_worker_error", "error_type": type(exc).__name__}), flush=True)
        if args.once:
            return
        time.sleep(30)


if __name__ == "__main__":
    main()
