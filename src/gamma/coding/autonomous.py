"""Background worker for owner coding goals (queued from the dashboard or by Shana).

Each queued goal runs through ``workflow.run``: split into small tasks, work
each one with a local model, check it in the sandbox, run the full suite, and
leave ``proposal.diff`` for the owner. The worker never applies changes or
restarts anything (D5). Model calls go through ``local_model.BackgroundModel``,
which yields to Shana's conversation and waits while any model is loaded.

The owner can pause, resume or stop a job (``control.json``); each takes effect
at the next step. The job's time budget counts from when it was queued. A job
that was running when the worker died is marked interrupted, never replayed.

    python -m gamma.coding.autonomous [--once]
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from . import autonomous_store as store
from .local_model import BackgroundModel

POLL_SECONDS = 5
MAX_EVENTS = 100


class Stopped(Exception):
    pass


class Job:
    """One job's record, progress events and owner controls."""

    def __init__(self, root: Path, identifier: str) -> None:
        self.root = Path(root)
        self.path = store.job_path(root, identifier)
        self.record = store.read_job(root, identifier)
        elapsed = time.time() - self.record['created_at']
        self.deadline = time.monotonic() + max(0.0, self.record['budget_seconds'] - elapsed)

    def save(self) -> None:
        store.write_json(self.path / 'state.json', self.record)

    def event(self, stage: str, message: str = '', **values) -> None:
        self.record['stage'] = stage
        self.record['events'] = (self.record['events'] + [
            {'stage': stage, 'message': message[:500], 'at': time.time(), **values}])[-MAX_EVENTS:]
        self.save()

    def _action(self) -> str | None:
        control = self.path / 'control.json'
        if control.is_file() and not control.is_symlink():
            return json.loads(control.read_text()).get('action')
        return None

    def ready(self) -> bool:
        """False while paused; raises on stop or when the time budget is used up."""
        if time.monotonic() >= self.deadline:
            raise TimeoutError('The time budget ran out')
        action = self._action()
        if action == 'stop':
            raise Stopped()
        return action != 'pause'

    def checkpoint(self) -> None:
        """Return when work may continue; wait here while paused."""
        while not self.ready():
            if self.record['status'] != 'paused':
                self.record['status'] = 'paused'
                self.event('paused', 'Waiting for the owner to resume')
            time.sleep(1)
        if self.record['status'] == 'paused':
            self.record['status'] = 'running'
            self.event('resumed')


def _tasks(report) -> list[dict]:
    results = {t['id']: t for t in report.tasks}
    rows = []
    for task in report.plan:
        result = results.get(task['id'], {})
        rows.append({'id': task['id'], 'title': task['title'], 'files': task['files'], 'check': task['check'],
                     'status': result.get('status', 'pending'), 'summary': result.get('summary', ''),
                     'steps': result.get('steps', 0)})
    return rows


def execute(root: Path, identifier: str, *, project: Path, endpoint: str, run=None, model=None) -> dict:
    from . import workflow
    run = run or workflow.run
    job = Job(root, identifier)
    if job.record['status'] not in {'queued', 'deferred'}:
        return job.record
    job.record['status'] = 'running'
    job.record.pop('reason', None)
    job.event('starting', 'Starting work')
    try:
        job.checkpoint()
        coder = model or BackgroundModel(endpoint, job.record['model'], job)
        report = run(job.record['goal'], project, coder, job.path, checkpoint=job.checkpoint, on_event=job.event)
        job.record.update(status=report.status, stage=report.status, reason=report.message, tasks=_tasks(report))
        if report.suite:
            job.record['suite'] = {'passed': bool(report.suite.get('passed')),
                                   'output_tail': str(report.suite.get('output') or '')[-1500:]}
        diff = Path(report.diff_path) if report.diff_path else None
        if diff and diff.is_file() and diff.stat().st_size:
            job.record['proposal'] = 'proposal.diff'
    except Stopped:
        job.record.update(status='stopped', reason='Stopped by the owner')
    except TimeoutError as exc:
        job.record.update(status='blocked', reason=str(exc)[:200] or 'Timed out')
    except Exception as exc:  # the job records why; the worker keeps serving others
        job.record.update(status='blocked', error=type(exc).__name__,
                          reason=str(exc)[:200] if isinstance(exc, (ValueError, RuntimeError, OSError)) else 'Stopped safely after an error')
    finally:
        if job.record['status'] not in store.ACTIVE:
            job.record['completed_at'] = time.time()
        job.save()
    return job.record


def mark_interrupted(root: Path) -> None:
    """Jobs left running by a dead worker are reported, not resumed."""
    for record in store.list_jobs(root):
        if record['status'] in {'running', 'paused'}:
            record.update(status='interrupted', reason='The worker restarted; this work was not replayed',
                          completed_at=time.time())
            store.write_json(store.job_path(root, record['id']) / 'state.json', record)


def main() -> int:
    from ..config import PROJECT_ROOT, settings
    parser = argparse.ArgumentParser(description="Run queued owner coding goals.")
    parser.add_argument('--once', action='store_true', help='run what is queued, then exit')
    args = parser.parse_args()
    root = settings.data_dir / 'coding' / 'autonomous'
    with store.execution_lock(root):
        mark_interrupted(root)
        while True:
            for record in reversed(store.list_jobs(root)):
                if record['status'] in {'queued', 'deferred'}:
                    execute(root, record['id'], project=PROJECT_ROOT, endpoint=settings.local_llm_endpoint)
            if args.once:
                return 0
            time.sleep(POLL_SECONDS)


if __name__ == '__main__':
    raise SystemExit(main())
