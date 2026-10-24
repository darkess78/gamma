"""Owner-only coding tools; conversation binds authority before invocation."""
import json
from .base import Tool, ToolResult

CODING_TOOLS = {'coding_goal','coding_status','coding_control'}


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
    description = 'Owner only: read the current coding task list, progress and verified completion evidence.'

    def run(self, **kwargs):
        from ..coding.autonomous_store import list_jobs
        from ..config import settings
        rows = list_jobs(settings.data_dir/'coding'/'autonomous')[:5]
        safe = [{k:r.get(k) for k in ('id','goal','status','stage','reason','tasks')} for r in rows]
        return ToolResult(ok=True,output=json.dumps(safe),metadata={'jobs':safe})


class CodingControlTool(Tool):
    name = 'coding_control'
    description = 'Owner only: pause, resume or stop an existing coding goal. Args: job_id, action.'

    def run(self, **kwargs):
        from ..coding.autonomous_store import control_job
        from ..config import settings
        result=control_job(settings.data_dir/'coding'/'autonomous',kwargs.get('job_id'),kwargs.get('action'))
        return ToolResult(ok=True,output='Coding control requested at the next safe boundary.',metadata=result)
