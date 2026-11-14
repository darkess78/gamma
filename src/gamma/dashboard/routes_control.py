"""Status, presence, Monitor status, client logs, coding projects and Shana lifecycle routes."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi import HTTPException, Request

from . import deps

router = APIRouter()

@router.get("/api/status")
def status() -> dict:
    """Return dashboard service status.
    
    Returns:
        dict: Status information including version and services.
    """
    return deps.get_dashboard_service().build_status()


@router.get("/api/status/runtime")
def runtime_status() -> dict:
    """Return dashboard runtime status.
    
    Returns:
        dict: Runtime status including services and workers.
    """
    return deps.get_dashboard_service().build_runtime_status()


@router.get("/api/status/summary")
def status_summary() -> dict:
    """Return compact dashboard status for all-page polling."""
    return deps.get_dashboard_service().build_status_summary()


@router.get("/api/status/header")
def status_header() -> dict:
    """Return inexpensive state for navigation and overview polling."""
    return deps.get_dashboard_service().build_header_status()


@router.get("/api/status/diagnostics")
def status_diagnostics() -> dict:
    """Return the bounded payload used by the Status dashboard page."""
    return deps.get_dashboard_service().build_diagnostics_status()


@router.get("/api/status/settings")
def status_settings() -> dict:
    """Return the bounded payload required by the Settings page."""
    return deps.get_dashboard_service().build_settings_status()


@router.get("/api/status/memory")
def status_memory() -> dict:
    """Return the bounded payload required by the Memory page."""
    return deps.get_dashboard_service().build_memory_status()


@router.get("/api/presence")
def presence_status() -> dict:
    """Return Shana Presence runtime state."""
    return deps.get_dashboard_service().presence_status()


@router.post("/api/presence/mode")
async def presence_mode(request: Request) -> dict:
    """Set Shana Presence mode."""
    payload = await deps.json_object_payload(request)
    mode = str(payload.get("mode") or "").strip()
    try:
        return deps.get_dashboard_service().set_presence_mode(
            mode,
            confirm_public_output=bool(payload.get("confirm_public_output", False)),
            audience=payload.get("audience") if isinstance(payload.get("audience"), dict) else {"kind": "unknown"},
            session_id=str(payload.get("session_id") or "") or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/presence/break")
def presence_break() -> dict:
    """Set Shana Presence to Break."""
    return deps.get_dashboard_service().set_presence_mode("break")


@router.post("/api/presence/sleep")
def presence_sleep() -> dict:
    """Set Shana Presence to Sleep."""
    return deps.get_dashboard_service().set_presence_mode("sleep")


@router.get("/api/monitor/status")
def monitor_status() -> dict:
    """Return lightweight status for the long-running monitor page."""
    return deps.get_dashboard_service().build_monitor_status()


@router.post("/api/client-log")
async def client_log(request: Request) -> dict[str, bool]:
    """Append client-side log entry.
    
    Args:
        request: HTTP request with JSON payload containing log entry.
    
    Returns:
        dict[str, bool]: {"ok": True} on success.
    """
    payload = await request.json()
    if isinstance(payload, dict):
        deps.get_dashboard_service().append_client_log(payload)
    return {"ok": True}


@router.post("/api/shana/start")
def start_shana() -> dict:
    """Start Shana application via WebSocket.
    
    Returns:
        dict: Confirmation response.
    """
    return deps.get_dashboard_service().start_shana()


@router.post("/api/shana/stop")
def stop_shana() -> dict:
    """Stop Shana application via WebSocket.
    
    Returns:
        dict: Confirmation response.
    """
    return deps.get_dashboard_service().stop_shana()


@router.post("/api/shana/restart")
def restart_shana() -> dict:
    """Restart Shana application via WebSocket.
    
    Returns:
        dict: Confirmation response.
    """
    return deps.get_dashboard_service().restart_shana()


@router.post("/api/dashboard/stop")
def stop_dashboard() -> dict:
    """Stop dashboard application via WebSocket.
    
    Returns:
        dict: Confirmation response.
    """
    return deps.get_dashboard_service().stop_dashboard()


@router.post("/api/all/stop")
def stop_all() -> dict:
    """Stop all services (Shana and dashboard) via WebSocket.
    
    Returns:
        dict: Confirmation response.
    """
    return deps.get_dashboard_service().stop_all()
