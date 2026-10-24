"""Owner-only coding tools; conversation binds authority before invocation."""
import json
from .base import Tool, ToolResult

CODING_TOOLS = {'coding_goal','coding_status','coding_control'}


_JOB_STATES = {'queued', 'running', 'deferred', 'paused', 'interrupted', 'blocked',
               'stopped', 'needs_checker', 'already_satisfied', 'review_rejected',
               'completed', 'application_failed'}
_STAGES = _JOB_STATES | {'snapshot', 'baseline_checks', 'planning', 'acceptance_design',
                         'coding', 'feature_checks', 'checker', 'regression_checks',
                         'waiting_for_inference', 'applying', 'resumed'}
_TASK_STATES = {'pending', 'running', 'verified', 'already_satisfied', 'needs_checker', 'blocked_dependency'}
_RECOVERY = {'completed_current', 'missing', 'incomplete', 'invalid', 'source_conflict',
             'baseline_conflict', 'rolled_back', 'rollback_conflict', 'record_budget'}


def _finite(value, choices):
    return value if type(value) is str and value in choices else 'unknown'


def _coding_progress(record):
    from ..coding.autonomous_store import _valid_completed_receipt
    import re
    identifier = record.get('id')
    if type(identifier) is not str or not re.fullmatch(r'work-[0-9a-f]{32}', identifier):
        identifier = None
    status = _finite(record.get('status'), _JOB_STATES)
    raw_tasks = record.get('tasks')
    bounded = type(raw_tasks) is list and len(raw_tasks) <= 4
    tasks = []
    if bounded:
        for item in raw_tasks:
            item = item if type(item) is dict else {}
            attempt = item.get('attempts')
            tasks.append({'status': _finite(item.get('status'), _TASK_STATES),
                          'attempts': attempt if type(attempt) is int and 0 <= attempt <= 7 else None})
    recovery = record.get('application_recovery')
    recovery = _finite(recovery.get('status'), _RECOVERY) if type(recovery) is dict else 'unknown'
    application = record.get('application')
    outcome, file_count = 'unconfirmed', None
    if _valid_completed_receipt(application):
        if status == 'interrupted' and recovery == 'completed_current':
            outcome = 'verified_at_recovery'
        elif status == 'completed' and recovery == 'unknown':
            outcome = 'completed_receipt'
        if outcome != 'unconfirmed':
            file_count = len(application['files'])
    return {'id': identifier, 'status': status, 'stage': _finite(record.get('stage'), _STAGES),
            'tasks': tasks, 'task_count': len(tasks) if bounded else None,
            'verified_task_count': sum(t['status'] == 'verified' for t in tasks) if bounded else None,
            'recovery_outcome': recovery, 'application_outcome': outcome,
            'application_file_count': file_count, 'followup_completion': 'unconfirmed'}


def coding_status_reply(jobs):
    """Finite wording for the existing private direct reply, never raw metadata."""
    if type(jobs) is not list or not jobs:
        return 'No coding goals are recorded yet.'
    row = jobs[0] if type(jobs[0]) is dict else {}
    reply = 'The latest coding goal is ' + _finite(row.get('status'), _JOB_STATES) + '.'
    outcome = row.get('application_outcome')
    if outcome == 'verified_at_recovery':
        detail = 'The application was verified at recovery.'
    elif outcome == 'completed_receipt':
        detail = 'The stored application receipt records completion.'
    else:
        details = {'completed_current': 'Stored recovery records a checked application, but its completion payload is unconfirmed.',
                   'source_conflict': 'Source conflicted with application evidence at recovery.',
                   'record_budget': 'Application evidence is omitted because of the record budget.',
                   'missing': 'No application journal was found at recovery.',
                   'incomplete': 'Application evidence was incomplete at recovery.',
                   'invalid': 'Application evidence was invalid at recovery.',
                   'baseline_conflict': 'The application recorded a baseline conflict.',
                   'rolled_back': 'The application recorded a rollback.',
                   'rollback_conflict': 'The application recorded a rollback conflict.'}
        recovery = _finite(row.get('recovery_outcome'), _RECOVERY)
        detail = details.get(recovery, 'Application completion evidence is unconfirmed.')
    return reply + ' ' + detail + ' Follow-up completion is unconfirmed.'



class CodingGoalTool(Tool):
    name = 'coding_goal'
    description = ('Owner only: accept a concrete repository improvement goal, create its task list, '
                   'run local coding and independent checks in the background. Args: goal, optional budget_seconds. '
                   'Queued work is not finished work. Never submit while brainstorming or without an owner instruction.')

    def run(self, **kwargs):
        from ..coding.operator import enqueue_goal
        result = enqueue_goal(kwargs.get('goal',''),budget_seconds=kwargs.get('budget_seconds',1800))
        job = result['job']
        return ToolResult(ok=job['status'] == 'queued',output='Coding goal queued for background work.',
                          metadata={'job_id':job['id'],'status':job['status']})


class CodingStatusTool(Tool):
    name = 'coding_status'
    description = 'Owner only: read bounded coding progress and stored application evidence.'

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
