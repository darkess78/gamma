"""Shared queue admission for owner conversation and dashboard controls."""
from . import autonomous_store as store
from .repository import submission_lock


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
