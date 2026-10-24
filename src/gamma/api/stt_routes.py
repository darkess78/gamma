"""Controls for the core-owned persistent recognizer."""
from fastapi import APIRouter, HTTPException, Query

from ..config import settings
from ..supervisor.worker_events import read_worker_logs

router = APIRouter()


def service():
    from .routes import get_voice_roundtrip_service
    return get_voice_roundtrip_service()


@router.get("/v1/voice/stt/status")
def status():
    return service().stt_status()


@router.get("/v1/voice/stt/logs")
def logs(event_type: str = Query("", max_length=40), trace_id: str = Query("", max_length=128),
         severity: str = Query("", max_length=20), limit: int = Query(100, ge=1, le=200)):
    return read_worker_logs(settings.data_dir / "runtime", ("stt",), event_type=event_type,
                            trace_id=trace_id, severity=severity, limit=limit)


@router.post("/v1/voice/stt/{action}")
def control(action: str):
    try:
        return service().stt_control(action)
    except ValueError as exc:
        raise HTTPException(400, "unsupported STT configuration or action") from exc
    except RuntimeError as exc:
        raise HTTPException(409, "STT worker busy or unavailable") from exc
    except Exception as exc:
        raise HTTPException(502, "STT worker control failed") from exc
