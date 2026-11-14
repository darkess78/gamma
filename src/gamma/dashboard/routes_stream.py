"""Stream, rehearsal, Monitor input and performer routes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from . import deps

router = APIRouter()

@router.get("/api/stream/traces/recent")
def dashboard_stream_recent_traces(limit: int = 50) -> dict:
    """Get recent stream traces.
    
    Args:
        limit: Optional max traces (default 50).
    
    Returns:
        dict: Recent stream trace data.
    """
    return deps.get_dashboard_service().stream_recent_traces(limit=limit)


@router.get("/api/stream/eval/recent")
def dashboard_stream_recent_eval(limit: int = 50) -> dict:
    """Get recent stream evaluations.
    
    Args:
        limit: Optional max evaluations (default 50).
    
    Returns:
        dict: Recent stream eval data.
    """
    return deps.get_dashboard_service().stream_recent_eval(limit=limit)


@router.get("/api/stream/outputs/recent")
def dashboard_stream_recent_outputs(limit: int = 50) -> dict:
    """Get recent stream outputs.
    
    Args:
        limit: Optional max outputs (default 50).
    
    Returns:
        dict: Recent stream output data.
    """
    return deps.get_dashboard_service().stream_recent_outputs(limit=limit)


@router.get("/api/stream/queue")
def dashboard_stream_pending_queue() -> dict:
    """Get pending stream queue.
    
    Returns:
        dict: Pending stream queue data.
    """
    return deps.get_dashboard_service().stream_pending_queue()


@router.get("/api/stream/temp-memory")
def dashboard_stream_temp_memory(bucket: str | None = None, limit: int = 100) -> dict:
    """Get temp memory bucket entries.
    
    Args:
        bucket: Optional bucket name (default None for all).
        limit: Optional max items (default 100).
    
    Returns:
        dict: Temp memory entries.
    """
    return deps.get_dashboard_service().stream_temp_memory(bucket=bucket, limit=limit)


@router.delete("/api/stream/temp-memory")
def dashboard_stream_temp_memory_clear(bucket: str | None = None) -> dict:
    """Clear temp memory bucket.
    
    Args:
        bucket: Optional bucket name to clear (default None for all).
    
    Returns:
        dict: Confirmation response.
    """
    return deps.get_dashboard_service().clear_stream_temp_memory(bucket=bucket)


@router.get("/api/stream/self-goals")
def dashboard_stream_self_goals(status: str | None = None, limit: int = 100) -> dict:
    """Get stream self-goals.
    
    Args:
        status: Optional status filter (default None for all).
        limit: Optional max goals (default 100).
    
    Returns:
        dict: Stream self-goals data.
    """
    return deps.get_dashboard_service().stream_self_goals(status=status, limit=limit)


@router.post("/api/stream/self-goals/{goal_id}/approve")
def dashboard_stream_self_goal_approve(goal_id: int) -> dict:
    """Approve a stream self-goal.
    
    Args:
        goal_id: Self-goal ID to approve.
    
    Returns:
        dict: Confirmation response.
    """
    return deps.get_dashboard_service().set_stream_self_goal_status(goal_id, status="approve")


@router.post("/api/stream/self-goals/{goal_id}/reject")
def dashboard_stream_self_goal_reject(goal_id: int) -> dict:
    """Reject a stream self-goal.
    
    Args:
        goal_id: Self-goal ID to reject.
    
    Returns:
        dict: Confirmation response.
    """
    return deps.get_dashboard_service().set_stream_self_goal_status(goal_id, status="reject")


@router.post("/api/stream/self-goals/clear")
def dashboard_stream_self_goals_clear() -> dict:
    """Clear all stream self-goals.
    
    Returns:
        dict: Confirmation response.
    """
    return deps.get_dashboard_service().clear_stream_self_goals()


@router.post("/api/stream/stop")
def dashboard_stream_stop() -> dict:
    """Stop stream speech with reason 'dashboard_stop'.
    
    Returns:
        dict: Confirmation response.
    """
    return deps.get_dashboard_service().stop_stream_speech(reason="dashboard_stop")


@router.get("/api/stream/rehearsal")
def dashboard_stream_rehearsal_status() -> dict:
    """Return local stream rehearsal state."""
    return deps.get_dashboard_service().stream_rehearsal_status()


@router.post("/api/stream/rehearsal/start")
async def dashboard_stream_rehearsal_start(request: Request) -> dict:
    """Start local stream rehearsal state."""
    payload = await deps.json_object_payload(request)
    return deps.get_dashboard_service().start_stream_rehearsal(payload)


@router.post("/api/stream/rehearsal/stop")
def dashboard_stream_rehearsal_stop() -> dict:
    """Stop local stream rehearsal state."""
    return deps.get_dashboard_service().stop_stream_rehearsal()


@router.post("/api/stream/rehearsal/events")
async def dashboard_stream_rehearsal_event(request: Request) -> dict:
    """Inject a local rehearsal event into the stream brain."""
    payload = await deps.json_object_payload(request)
    try:
        return deps.get_dashboard_service().inject_stream_rehearsal_event(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/monitor/input")
async def dashboard_monitor_input(request: Request) -> dict:
    """Submit local monitor input through the stream/performer path."""
    payload = await deps.json_object_payload(request)
    try:
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(deps.get_dashboard_service().submit_monitor_stream_input, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/performer/targets/{target_policy}/mute")
def dashboard_performer_target_mute(target_policy: str) -> dict:
    """Mute a performer target policy.
    
    Args:
        target_policy: Policy name to mute (all, voice, memory).
    
    Returns:
        dict: Confirmation response.
    """
    return deps.get_dashboard_service().set_performer_target_mute(target_policy, muted=True, reason="dashboard")


@router.post("/api/performer/targets/{target_policy}/unmute")
def dashboard_performer_target_unmute(target_policy: str) -> dict:
    """Unmute a performer target policy.
    
    Args:
        target_policy: Policy name to unmute (all, voice, memory).
    
    Returns:
        dict: Confirmation response.
    """
    return deps.get_dashboard_service().set_performer_target_mute(target_policy, muted=False, reason="dashboard")


@router.post("/api/performer/targets/{target_policy}/clear")
def dashboard_performer_target_clear(target_policy: str) -> dict:
    """Clear a performer target policy.
    
    Args:
        target_policy: Policy name to clear (all, voice, memory).
    
    Returns:
        dict: Confirmation response.
    """
    return deps.get_dashboard_service().clear_performer_target(target_policy, reason="dashboard")
