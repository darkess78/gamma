"""Dashboard controls for owner coding goals: queue one, watch it, pause/stop it, read its proposal."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..config import settings
from ..coding import autonomous_store as store
router = APIRouter(prefix='/api/coding')


class GoalRequest(BaseModel):
    goal: str = Field(min_length=12, max_length=2000)
    model: str = Field(default='qwen3.8:27b', pattern=r'^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,199}$')
    budget_seconds: int = Field(default=1800, ge=60, le=28800)


class ControlRequest(BaseModel):
    action: str = Field(pattern=r'^(pause|resume|stop)$')


def start_goal(request):
    from ..coding.operator import enqueue_goal
    return enqueue_goal(request.goal, request.model, request.budget_seconds)


@router.get('/work')
async def jobs():
    return {'jobs':await run_in_threadpool(store.list_jobs, settings.data_dir/'coding'/'autonomous')}


@router.post('/work')
async def create(request: GoalRequest):
    try:
        return await run_in_threadpool(start_goal, request)
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc) if isinstance(exc,ValueError) else 'Work storage unavailable') from None


@router.get('/work/{identifier}/proposal')
async def proposal(identifier: str):
    try:
        diff = await run_in_threadpool(store.read_proposal, settings.data_dir/'coding'/'autonomous', identifier)
    except (ValueError, OSError):
        raise HTTPException(404, 'No proposal is available for this work') from None
    return {'id': identifier, 'diff': diff}


@router.post('/work/{identifier}/control')
async def control(identifier: str, request: ControlRequest):
    try:
        return await run_in_threadpool(store.control_job,settings.data_dir/'coding'/'autonomous',identifier,request.action)
    except (ValueError, OSError):
        raise HTTPException(409, 'Work cannot accept that control') from None


