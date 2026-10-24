"""Operator controls for core-owned direct text inference."""
from fastapi import APIRouter, HTTPException, Query

from ..config import settings
from ..brain.model_runtime import get_model_worker
from ..supervisor.worker_events import read_worker_logs

router = APIRouter()


@router.get("/v1/brain-model/logs")
def logs(event_type: str = Query("", max_length=40), trace_id: str = Query("", max_length=128),
         severity: str = Query("", max_length=20), limit: int = Query(100, ge=1, le=200)):
    return read_worker_logs(settings.data_dir / "runtime", ("brain-model",), event_type=event_type,
                            trace_id=trace_id, severity=severity, limit=limit)


@router.get("/v1/brain-model/status")
def status():
    if settings.brain_model_runtime != "worker":
        return {"runtime": settings.brain_model_runtime, "controls": []}
    try:
        return {"runtime": "worker", "controls": ["start", "stop", "restart"], **get_model_worker().status()}
    except ValueError:
        return {"runtime": "worker", "controls": [], "configuration_supported": False}


@router.post("/v1/brain-model/{action}")
def control(action: str):
    if action not in {"start", "stop", "restart"}:
        raise HTTPException(400, "unsupported Model action")
    if settings.brain_model_runtime != "worker":
        raise HTTPException(409, "Model worker mode is not enabled")
    try:
        return get_model_worker().control(action)
    except ValueError as exc:
        raise HTTPException(400, "unsupported model worker configuration") from exc
    except RuntimeError as exc:
        raise HTTPException(409, "Model worker busy or awaiting reconciliation") from exc
    except Exception as exc:
        raise HTTPException(502, "Model worker control failed") from exc
