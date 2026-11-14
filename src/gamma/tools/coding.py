"""Owner-only coding tools; conversation binds authority before invocation."""
import json
from .base import Tool, ToolResult

CODING_TOOLS = {'coding_goal','coding_status','coding_control'}


_ACTIVE = {'queued', 'running', 'deferred', 'paused'}
_JOB_STATES = _ACTIVE | {'awaiting_owner_review', 'needs_owner', 'blocked', 'failed', 'stopped', 'interrupted'}
_STAGES = _JOB_STATES | {'starting', 'splitting', 'working', 'suite', 'waiting_for_gpu', 'resumed'}
_TASK_STATES = {'pending', 'done', 'gave_up', 'out_of_steps'}
MAX_TASKS = 6


def _finite(value, choices):
    return value if type(value) is str and value in choices else 'unknown'


def _coding_progress(record):
    """What Shana may say about a job: fixed words and counts, never the goal, files or code."""
    import re
    identifier = record.get('id')
    if type(identifier) is not str or not re.fullmatch(r'work-[0-9a-f]{32}', identifier):
        identifier = None
    raw_tasks = record.get('tasks')
    bounded = type(raw_tasks) is list and len(raw_tasks) <= MAX_TASKS
    tasks = [_finite((t if type(t) is dict else {}).get('status'), _TASK_STATES) for t in raw_tasks] if bounded else []
    return {'id': identifier, 'status': _finite(record.get('status'), _JOB_STATES),
            'stage': _finite(record.get('stage'), _STAGES),
            'task_count': len(tasks) if bounded else None,
            'done_count': tasks.count('done') if bounded else None,
            'has_proposal': record.get('proposal') == 'proposal.diff'}


def coding_status_reply(jobs):
    """Fixed wording for the private direct reply, never raw metadata."""
    if type(jobs) is not list or not jobs:
        return 'No coding goals are recorded yet.'
    row = jobs[0] if type(jobs[0]) is dict else {}
    status = _finite(row.get('status'), _JOB_STATES)
    done, total = row.get('done_count'), row.get('task_count')
    progress = (f'{done} of {total} tasks are done' if type(done) is int and type(total) is int and total
                else 'it has no task list yet')
    if status == 'awaiting_owner_review':
        return f'The latest coding goal is finished: {progress}, and a proposal is waiting for your review on the Coding page.'
    if status == 'needs_owner':
        return 'The latest coding goal needs a decision from you before it can be split into tasks; the question is on the Coding page.'
    if status in _ACTIVE:
        return f'The latest coding goal is {status.replace("_", " ")}; {progress}.'
    if status == 'unknown':
        return "The latest coding goal has a status I can't read."
    return f'The latest coding goal is {status.replace("_", " ")}; {progress}. The Coding page has the reason.'


class CodingGoalTool(Tool):
    name = 'coding_goal'
    description = ('Owner only: queue a concrete repository improvement goal. A local model splits it into '
                   'small tasks, works each with tests, and leaves a proposal for owner review. Args: goal, optional budget_seconds. '
                   'Queued work is not finished work. Never submit while brainstorming or without an owner instruction.')

    def run(self, **kwargs):
        from ..coding.operator import enqueue_goal
        result = enqueue_goal(kwargs.get('goal',''),budget_seconds=kwargs.get('budget_seconds',1800))
        job = result['job']
        return ToolResult(ok=job['status'] == 'queued',output='Coding goal queued for background work.',
                          metadata={'job_id':job['id'],'status':job['status']})


class CodingStatusTool(Tool):
    name = 'coding_status'
    description = 'Owner only: read coding progress (status and task counts).'

    def run(self, **kwargs):
        from ..coding.autonomous_store import list_jobs
        from ..config import settings
        try:
            rows = list_jobs(settings.data_dir/'coding'/'autonomous')[:5]
            safe = [_coding_progress(r) for r in rows if type(r) is dict]
        except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
            import logging
            logging.getLogger(__name__).warning('coding_status_read_failed exception=%s', type(exc).__name__)
            return ToolResult(ok=False, output='Coding status is unavailable.', metadata={'jobs': []})
        return ToolResult(ok=True,output=json.dumps(safe),metadata={'jobs':safe})


class CodingControlTool(Tool):
    name = 'coding_control'
    description = 'Owner only: pause, resume or stop an existing coding goal. Args: job_id, action.'

    def run(self, **kwargs):
        from ..coding.autonomous_store import control_job
        from ..config import settings
        result=control_job(settings.data_dir/'coding'/'autonomous',kwargs.get('job_id'),kwargs.get('action'))
        return ToolResult(ok=True,output='Coding control requested at the next safe boundary.',metadata=result)
