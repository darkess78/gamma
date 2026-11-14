"""Shared queue admission for owner conversation and dashboard controls."""
import fcntl
from contextlib import contextmanager

from . import autonomous_store as store


@contextmanager
def submission_lock(root):
    """One goal admission at a time; a second concurrent submission is refused."""
    root = store.safe_root(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / 'submission.lock'
    if path.is_symlink():
        raise ValueError('Invalid queue lock')
    with path.open('a+b') as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError('Coding queue busy') from None
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def enqueue_goal(goal, model='qwen3.8:27b', checker_model='gpt-oss:20b', budget_seconds=1800, *, manager=None, acceptance_tests=None, acceptance_mode='checker'):
    from ..config import settings
    from ..supervisor.manager import ProcessManager
    root = settings.data_dir / 'coding' / 'autonomous'
    with submission_lock(root):
        record = store.create_job(root,goal,model,checker_model,budget_seconds,acceptance_tests=acceptance_tests,acceptance_mode=acceptance_mode)
        process = (manager or ProcessManager()).start_module('coding_autonomous','gamma.coding.autonomous')
        if not process.get('ok'):
            record.update(status='blocked',reason='Autonomous coding worker could not start')
            store.write_json(store.job_path(root,record['id'])/'state.json',record)
        return {'job':record,'process':process}
