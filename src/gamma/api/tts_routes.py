"""Operator controls for core-owned synthesis without lease overrides."""
from fastapi import APIRouter, HTTPException, Query

from ..config import settings
from ..voice.tts_runtime import get_tts_worker
from ..supervisor.worker_events import read_worker_logs

router = APIRouter()


@router.get("/v1/voice/tts/logs")
def logs(event_type: str = Query("", max_length=40), trace_id: str = Query("", max_length=128),
         severity: str = Query("", max_length=20), limit: int = Query(100, ge=1, le=200)):
    return read_worker_logs(settings.data_dir / "runtime", ("tts",), event_type=event_type,
                            trace_id=trace_id, severity=severity, limit=limit)


@router.get("/v1/voice/tts/status")
def status():
    if settings.tts_runtime != "worker":
        return {"runtime": settings.tts_runtime, "controls": []}
    return {"runtime": "worker", **get_tts_worker().status()}


@router.post("/v1/voice/tts/{action}")
def control(action: str):
    if action not in {"start", "stop", "restart", "reconcile"}:
        raise HTTPException(400, "unsupported TTS action")
    if settings.tts_runtime != "worker":
        raise HTTPException(409, "TTS worker mode is not enabled")
    try:
        return get_tts_worker().control(action)
    except RuntimeError as exc:
        raise HTTPException(409, "TTS worker busy or awaiting reconciliation") from exc
    except Exception as exc:
        raise HTTPException(502, "TTS worker control failed") from exc
