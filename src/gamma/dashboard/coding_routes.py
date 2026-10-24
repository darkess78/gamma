"""Dashboard-owned controls for general autonomous coding goals."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..config import settings
from ..coding import autonomous_store as store
router = APIRouter(prefix='/api/coding')


class GoalRequest(BaseModel):
    goal: str = Field(min_length=12, max_length=2000)
    model: str = Field(default='qwen3.8:27b', pattern=r'^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,199}$')
    checker_model: str = Field(default='gpt-oss:20b', pattern=r'^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,199}$')
    budget_seconds: int = Field(default=1800, ge=60, le=28800)
    acceptance_tests: dict[str, str] = Field(default_factory=dict, max_length=3)
    acceptance_mode: str = Field(default='checker', pattern=r'^(checker|owner_contract)$')


class ControlRequest(BaseModel):
    action: str = Field(pattern=r'^(pause|resume|stop)$')


def start_goal(request):
    from ..coding.operator import enqueue_goal
    return enqueue_goal(request.goal,request.model,request.checker_model,request.budget_seconds,
                        acceptance_tests=request.acceptance_tests, acceptance_mode=request.acceptance_mode)


@router.get('/work')
async def jobs():
    return {'jobs':await run_in_threadpool(store.list_jobs, settings.data_dir/'coding'/'autonomous')}


@router.post('/work')
async def create(request: GoalRequest):
    try:
        return await run_in_threadpool(start_goal, request)
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc) if isinstance(exc,ValueError) else 'Work storage unavailable') from None


@router.post('/work/{identifier}/control')
async def control(identifier: str, request: ControlRequest):
    try:
        return await run_in_threadpool(store.control_job,settings.data_dir/'coding'/'autonomous',identifier,request.action)
    except (ValueError, OSError):
        raise HTTPException(409, 'Work cannot accept that control') from None


@router.get('/lessons/{identifier}/history')
async def lesson_history(identifier: str):
    def read():
        from ..coding.lessons import LessonStore
        return LessonStore(settings.data_dir/'coding'/'lessons').history(identifier)
    try:
        return {'history':await run_in_threadpool(read)}
    except (ValueError, OSError, AttributeError):
        raise HTTPException(409, 'Lesson history unavailable') from None


@router.get('/candidates')
async def candidates():
    from ..coding.candidate_operator import list_candidates
    return {'candidates': await run_in_threadpool(list_candidates, settings.data_dir / 'coding')}


@router.post('/candidates')
async def candidate_start():
    from ..coding.candidate_operator import start_trial
    from ..config import PROJECT_ROOT
    try:
        return await run_in_threadpool(start_trial, settings.data_dir / 'coding', PROJECT_ROOT)
    except (OSError, ValueError):
        raise HTTPException(409, 'Candidate trial unavailable while assigned work or inference is active') from None


@router.post('/candidates/{identifier}/stop')
async def candidate_stop(identifier: str):
    from ..coding.candidate_operator import stop_trial
    try:
        return await run_in_threadpool(stop_trial, settings.data_dir / 'coding', identifier)
    except (OSError, ValueError):
        raise HTTPException(409, 'Candidate cannot accept that control') from None
