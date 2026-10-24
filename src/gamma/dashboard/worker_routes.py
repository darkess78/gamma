"""Worker operations inherit the dashboard application's session middleware."""
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pathlib import Path

from ..supervisor.workers import WorkerRuntime
from .shana_client import ShanaApiClient, ShanaClientError

router = APIRouter()


@router.get("/api/autonomy/silent")
def silent_status():
    return silent_request("GET", "")


@router.post("/api/autonomy/silent/{action}")
def silent_control(action: str):
    if action not in {"enable", "disable"}:
        raise HTTPException(400, "unsupported silent cycle action")
    return silent_request("POST", "/" + action)


def silent_request(method, suffix):
    client = ShanaApiClient()
    try:
        return client.request_json(method, "/v1/autonomy/silent" + suffix,
            json_payload={"budget": 1, "cooldown_seconds": 60} if suffix == "/enable" else None, timeout=5)
    except ShanaClientError as exc:
        raise HTTPException(exc.status_code, "silent cycle control unavailable") from exc
    finally:
        client.close()


@router.get("/api/tts/status")
def tts_status():
    return tts_request("GET", "status")


@router.get("/api/tts/logs")
def tts_logs(event_type: str = Query("", max_length=40), trace_id: str = Query("", max_length=128),
             severity: str = Query("", max_length=20), limit: int = Query(100, ge=1, le=200)):
    return tts_request("GET", "logs", params={"event_type": event_type, "trace_id": trace_id,
                                             "severity": severity, "limit": limit})


@router.post("/api/tts/{action}")
def tts_control(action: str):
    if action not in {"start", "stop", "restart", "reconcile"}:
        raise HTTPException(400, "unsupported TTS action")
    return tts_request("POST", action)


def tts_request(method, action, params=None):
    client = ShanaApiClient()
    try:
        return client.request_json(method, f"/v1/voice/tts/{action}", params=params, timeout=10)
    except ShanaClientError as exc:
        raise HTTPException(exc.status_code, "TTS worker unavailable or awaiting reconciliation") from exc
    finally:
        client.close()


@router.get("/api/brain-model/status")
def model_status():
    return model_request("GET", "status")


@router.get("/api/brain-model/logs")
def model_logs(event_type: str = Query("", max_length=40), trace_id: str = Query("", max_length=128),
             severity: str = Query("", max_length=20), limit: int = Query(100, ge=1, le=200)):
    return model_request("GET", "logs", params={"event_type": event_type, "trace_id": trace_id,
                                             "severity": severity, "limit": limit})


@router.post("/api/brain-model/{action}")
def model_control(action: str):
    if action not in {"start", "stop", "restart"}:
        raise HTTPException(400, "unsupported Model action")
    return model_request("POST", action)


def model_request(method, action, params=None):
    client = ShanaApiClient()
    try:
        return client.request_json(method, f"/v1/brain-model/{action}", params=params, timeout=10)
    except ShanaClientError as exc:
        raise HTTPException(exc.status_code, "Model worker unavailable") from exc
    finally:
        client.close()


@router.get("/api/stt/status")
def stt_status():
    return stt_request("GET", "status")


@router.get("/api/stt/logs")
def stt_logs(event_type: str = Query("", max_length=40), trace_id: str = Query("", max_length=128),
             severity: str = Query("", max_length=20), limit: int = Query(100, ge=1, le=200)):
    return stt_request("GET", "logs", params={"event_type": event_type, "trace_id": trace_id,
                                             "severity": severity, "limit": limit})


@router.post("/api/stt/{action}")
def stt_control(action: str):
    if action not in {"start", "stop", "restart"}:
        raise HTTPException(400, "unsupported STT action")
    return stt_request("POST", action)


def stt_request(method, action, params=None):
    client = ShanaApiClient()
    try:
        return client.request_json(method, f"/v1/voice/stt/{action}", params=params, timeout=10)
    except ShanaClientError as exc:
        raise HTTPException(exc.status_code, "STT worker unavailable") from exc
    finally:
        client.close()


@router.get("/api/brain/status")
def brain_status():
    client = ShanaApiClient()
    try:
        return client.get("/v1/brain/status", timeout=5)
    except ShanaClientError as exc:
        raise HTTPException(502, "brain status unavailable") from exc
    finally:
        client.close()


@router.post("/api/brain/{action}")
def brain_control(action: str):
    if action not in {"start", "stop", "pause", "resume"}:
        raise HTTPException(400, "unsupported brain action")
    client = ShanaApiClient()
    try:
        return client.request_json("POST", f"/v1/brain/{action}", timeout=5)
    except ShanaClientError as exc:
        raise HTTPException(exc.status_code, "brain control unavailable") from exc
    finally:
        client.close()


@router.get("/dashboard/workers")
def worker_page():
    return FileResponse(Path(__file__).parent / "static" / "workers.html")


@router.get("/api/resources/admission")
def admission_status():
    from ..resources.shared_execution import status
    return status()


@router.post("/api/resources/admission/reconcile")
def admission_reconcile():
    from ..resources.shared_execution import reconcile
    return reconcile()


@router.get("/api/workers")
def worker_inventory():
    from ..resources.shared_execution import status
    inventory = WorkerRuntime().inventory()
    inventory["admission"] = status()
    return inventory


@router.post("/api/workers/{worker}/{action}")
def worker_control(worker: str, action: str):
    try:
        return WorkerRuntime().control(worker, action)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(409, "lifecycle control busy") from exc


@router.get("/api/worker-logs")
def worker_logs(workers: str = Query("", max_length=256), event_type: str = Query("", max_length=40),
                severity: str = Query("", max_length=20), trace_id: str = Query("", max_length=128),
                before: str = Query("", max_length=256), since: str = Query("", max_length=40),
                limit: int = Query(100, ge=1, le=200)):
    try:
        return WorkerRuntime().logs(workers, event_type=event_type, severity=severity,
                                    trace_id=trace_id, before=before, since=since, limit=limit)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
