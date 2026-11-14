"""Twitch and Discord worker routes, viewer trust and replay."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from . import deps

router = APIRouter()

class DiscordVoiceControlRequest(BaseModel):
    command: str = Field(pattern=r"^(join|leave|pause|resume|status)$")


@router.get("/api/twitch/worker/status")
def twitch_worker_status() -> dict:
    """Return Twitch worker status.
    
    Returns:
        dict: Worker run state and error status.
    """
    return deps.get_dashboard_service().twitch_worker_status()


@router.get("/api/twitch/settings")
def twitch_settings() -> dict:
    """Return current Twitch runtime settings.
    
    Returns:
        dict: Settings dict including oauth_token, eventsub, and trusted viewers.
    """
    return {"settings": deps.get_dashboard_service().twitch_runtime_settings()}


@router.post("/api/twitch/settings")
async def save_twitch_settings(request: Request) -> dict:
    """Save new Twitch runtime settings.
    
    Args:
        request: HTTP request with JSON payload of settings.
    
    Returns:
        dict: Confirmation response.
    """
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="object payload is required")
    return deps.get_dashboard_service().save_twitch_runtime_settings(payload)


@router.post("/api/twitch/worker/start")
def start_twitch_worker() -> dict:
    """Start Twitch worker.
    
    Returns:
        dict: Confirmation response.
    """
    return deps.get_dashboard_service().start_twitch_worker()


@router.post("/api/twitch/worker/stop")
def stop_twitch_worker() -> dict:
    """Stop Twitch worker.
    
    Returns:
        dict: Confirmation response.
    """
    return deps.get_dashboard_service().stop_twitch_worker()


@router.get("/api/twitch/eventsub/status")
def twitch_eventsub_status() -> dict:
    """Return Twitch EventSub worker status.
    
    Returns:
        dict: EventSub registration run state and error status.
    """
    return deps.get_dashboard_service().twitch_eventsub_status()


@router.post("/api/twitch/eventsub/start")
def start_twitch_eventsub_worker() -> dict:
    """Start Twitch EventSub worker.
    
    Returns:
        dict: Confirmation response.
    """
    return deps.get_dashboard_service().start_twitch_eventsub_worker()


@router.post("/api/twitch/eventsub/stop")
def stop_twitch_eventsub_worker() -> dict:
    """Stop Twitch EventSub worker.
    
    Returns:
        dict: Confirmation response.
    """
    return deps.get_dashboard_service().stop_twitch_eventsub_worker()


@router.post("/api/discord/voice/control")
def discord_voice_control(payload: DiscordVoiceControlRequest) -> dict:
    try:
        return deps.get_dashboard_service().discord_voice_control(payload.command)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/api/discord/voice/commands/{command_id}")
def discord_voice_command_status(command_id: str) -> dict:
    try:
        return deps.get_dashboard_service().discord_voice_command_status(command_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Command not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid command ID") from exc


@router.get("/api/discord/text-worker/status")
def discord_text_worker_status() -> dict:
    """Return Discord text worker status."""
    return deps.get_dashboard_service().discord_text_worker_status()


@router.post("/api/discord/text-worker/start")
def start_discord_text_worker() -> dict:
    """Start the Discord text ingestion worker."""
    return deps.get_dashboard_service().start_discord_text_worker()


@router.post("/api/discord/text-worker/stop")
def stop_discord_text_worker() -> dict:
    """Stop the Discord text ingestion worker."""
    return deps.get_dashboard_service().stop_discord_text_worker()


@router.get("/api/twitch/viewer-trust")
def twitch_viewer_trust(limit: int = 100) -> dict:
    """Return list of Twitch viewer trust info.
    
    Args:
        limit: Optional max number of viewers to return (default 100).
    
    Returns:
        dict: List of viewer info including trusted/untrusted status.
    """
    return deps.get_dashboard_service().twitch_viewer_trust(limit=limit)


@router.post("/api/twitch/viewer-trust")
async def save_twitch_viewer_trust(request: Request) -> dict:
    """Save a new viewer trust configuration.
    
    Args:
        request: HTTP request with JSON payload of viewer trust data.
    
    Returns:
        dict: Confirmation response.
        Raises:
            HTTPException: 400 if payload invalid.
    """
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="object payload is required")
    try:
        return deps.get_dashboard_service().save_twitch_viewer_trust(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/twitch/replay")
async def run_twitch_replay(request: Request) -> dict:
    """Run a Twitch replay.
    
    Args:
        request: HTTP request with JSON replay payload.
    
    Returns:
        dict: Replay status results.
        Raises:
            HTTPException: 400 if payload invalid, 500 on failure.
    """
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="object payload is required")
    try:
        return deps.get_dashboard_service().run_twitch_replay(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/twitch/replay/dry-run")
def run_twitch_dry_run_replay() -> dict:
    """Run a dry-run Twitch replay check.
    
    Returns:
        dict: Replay status results.
    """
    try:
        return deps.get_dashboard_service().run_twitch_dry_run_replay()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
