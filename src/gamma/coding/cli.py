"""Owner-invoked offline practice and capability assessment on the Gamma PC."""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from .curriculum import discord_foundations
from .learner import LocalPracticeLearner
from .practice import capability_summary, record_run, run_practice
from .sandbox import run_practice_check
from .lessons import LessonStore
from .evaluation import evaluate_candidate


def main(argv=None):
    from ..config import settings
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("practice", "assess"))
    parser.add_argument("--model")
    parser.add_argument("--idle", action="store_true", help="Yield to foreground conversation and assigned work between attempts")
    parser.add_argument("--maximum-attempts", type=int, default=5, choices=range(1, 11))
    parser.add_argument("--budget-seconds", type=int, default=300, choices=range(30, 1801))
    args = parser.parse_args(argv)
    root = settings.data_dir / "coding" / "practice"
    if args.action == "assess":
        print(json.dumps(capability_summary(root), indent=2))
        return 0
    if not args.model:
        parser.error("practice requires an explicitly selected local --model")
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError("invalid practice storage")
    root.mkdir(parents=True, exist_ok=True)
    lock = root / "practice.lock"
    if lock.is_symlink():
        raise RuntimeError("invalid practice lock")
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(str(os.getpid()))
        learner = LocalPracticeLearner(endpoint=settings.local_llm_endpoint, model=args.model,
                                       timeout_seconds=min(90, args.budget_seconds),
                                       lessons=LessonStore(settings.data_dir / "coding" / "lessons").list())
        # Availability is checked by the learner before each proposal. Keep
        # deferrals inside the runner so they produce an honest blocked record.
        deadline = time.monotonic() + args.budget_seconds
        def admission_checkpoint():
            if time.monotonic() >= deadline:
                raise TimeoutError("Authorized time budget ended")
            from .candidate_resources import candidate_busy
            from .learner import PracticeInferenceUnavailable
            if candidate_busy(settings.data_dir / 'coding'):
                raise PracticeInferenceUnavailable('candidate_trial_has_inference_resources')
            if args.idle:
                from .learner import PracticeInferenceUnavailable
                from .worker import read_policy
                from ..improvement.work_queue import ImprovementWorkStore
                if not read_policy(settings.data_dir / "coding")["enabled"]:
                    raise PracticeInferenceUnavailable("idle_practice_disabled")
                presence_path = settings.data_dir / "runtime" / "presence" / "state.json"
                if presence_path.is_symlink() or not presence_path.is_file() or presence_path.stat().st_size > 65536:
                    raise PracticeInferenceUnavailable("foreground_state_unknown")
                state = json.loads(presence_path.read_text(encoding="utf-8"))
                if state.get("mode") != "sleep" or state.get("activity", {}).get("current_turn_id"):
                    raise PracticeInferenceUnavailable("foreground_conversation")
                requests = ImprovementWorkStore(settings.data_dir / "improvement" / "control").list(limit=100)
                from .repository import list_jobs
                from .autonomous_store import list_jobs as autonomous_jobs, ACTIVE
                if any(j.get('status') in ACTIVE for j in autonomous_jobs(settings.data_dir/'coding'/'autonomous')):
                    raise PracticeInferenceUnavailable("assigned_repository_work_has_priority")
                if any(j.get('status') in {'queued','running'} for j in list_jobs(settings.data_dir/'coding'/'projects')):
                    raise PracticeInferenceUnavailable("assigned_repository_work_has_priority")
                if any(r.status in {"queued", "running", "paused"} for r in requests):
                    raise PracticeInferenceUnavailable("assigned_work_has_priority")
        learner.checkpoint = admission_checkpoint
        def propose(task, files, feedback):
            admission_checkpoint()
            return learner(task, files, feedback)
        samples = []
        def collect(task, files, outcome):
            samples.append({"task_id": task.id, "skill": task.skill, "instructions": task.instructions,
                            "source_files": files, "editable": list(task.editable),
                            "source_sha256": outcome["source_sha256"], "evaluation": outcome["evaluation"],
                            "attempt_count": outcome["attempt_count"]})
        from .practice_priority import prioritize
        curriculum = prioritize(discord_foundations(), capability_summary(root)['skills'])
        result = run_practice(curriculum, propose=propose, check=run_practice_check,
                              maximum_attempts=args.maximum_attempts, budget_seconds=args.budget_seconds,
                              evaluate=lambda task,files:evaluate_candidate(task,files,check=run_practice_check),
                              collect=collect)
        result["model"] = args.model
        path = record_run(root, result)
        if samples:
            # Fictional exercise solutions only. Independent human/semantic
            # review is still needed before these become training data.
            bundle = {"version": 1, "run_id": result["id"], "model": args.model,
                      "training_approved": False, "samples": samples}
            temporary = path.parent / "candidate-solutions.tmp"
            temporary.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path.parent / "candidate-solutions.json")
        LessonStore(settings.data_dir / "coding" / "lessons").ingest(result)
        print(json.dumps(result, indent=2))
        return 0 if result["passed"] else 1
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
