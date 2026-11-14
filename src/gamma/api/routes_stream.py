"""Stream events, traces, queue, temporary memory, self-goals and viewer trust."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from . import deps
from ..system import owner_channel
from ..errors import ConfigurationError, ConversationError, ExternalServiceError, GammaError
from ..integrations.twitch.trust import VALID_TRUST_LEVELS
from ..observability import current_request_id
from ..presence import apply_presence_to_stream_event, load_presence_state
from ..schemas.memory import ViewerTrustPayload
from ..stream.models import StreamInputEvent, StreamTurnResult
from ..stream.replay import StreamEvalReport


router = APIRouter()

@router.get("/v1/stream/viewer-trust")
def stream_viewer_trust(platform: str = "twitch", limit: int = 100) -> dict:
    records = deps.get_viewer_trust_store().list_records(platform=platform, limit=limit)
    return {
        "items": [deps._viewer_trust_payload(record) for record in records],
        "trust_levels": sorted(VALID_TRUST_LEVELS),
    }


@router.put("/v1/stream/viewer-trust")
def stream_viewer_trust_save(payload: ViewerTrustPayload) -> dict:
    trust_level = payload.trust_level.strip().lower()
    if trust_level not in VALID_TRUST_LEVELS:
        raise HTTPException(status_code=400, detail="unsupported trust_level")
    record = deps.get_viewer_trust_store().upsert(
        platform=payload.platform.strip().lower() or "twitch",
        platform_user_id=payload.platform_user_id.strip(),
        display_name=payload.display_name,
        trust_level=trust_level,  # type: ignore[arg-type]
        notes=payload.notes,
        pronunciation_alias=payload.pronunciation_alias,
    )
    return {"ok": True, "record": deps._viewer_trust_payload(record)}


@router.post("/v1/stream/events", response_model=StreamTurnResult)
def stream_event(
    event: StreamInputEvent, synthesize_speech: bool = False, fast_mode: bool = True, http_request: Request = None,
) -> StreamTurnResult:
    try:
        request_id = current_request_id()
        if request_id:
            event.metadata.setdefault("request_id", request_id)
        presence = load_presence_state(downgrade_stale_live=True)
        event, synthesize_speech = apply_presence_to_stream_event(
            event,
            synthesize_speech=synthesize_speech,
            state=presence,
        )
        return deps.get_stream_brain().handle_event(
            event,
            synthesize_speech=synthesize_speech,
            fast_mode=fast_mode,
            owner_channel=http_request is not None
            and owner_channel.is_owner_channel(http_request.headers.get(owner_channel.HEADER)),
        )
    except ConversationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConfigurationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ExternalServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except GammaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/v1/stream/traces/recent")
def stream_recent_traces(limit: int = 50) -> dict[str, list[dict]]:
    try:
        return {"items": deps.get_stream_replay_service().recent_traces(limit=limit)}
    except GammaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/v1/stream/eval/recent", response_model=StreamEvalReport)
def stream_eval_recent(limit: int = 50) -> StreamEvalReport:
    try:
        return deps.get_stream_replay_service().evaluate_recent(limit=limit)
    except GammaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/v1/stream/outputs/recent")
def stream_recent_outputs(limit: int = 50) -> dict[str, list[dict]]:
    try:
        return {"items": deps.get_stream_output_log_service().recent_outputs(limit=limit)}
    except GammaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/v1/stream/queue")
def stream_pending_queue() -> dict:
    try:
        return deps.get_stream_brain().pending_queue()
    except GammaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/v1/stream/temp-memory")
def stream_temp_memory(bucket: str | None = None, limit: int = 100) -> dict:
    try:
        return deps.get_stream_temp_memory_store().list_records(bucket=bucket, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GammaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/v1/stream/temp-memory")
def stream_temp_memory_clear(bucket: str | None = None) -> dict:
    try:
        return deps.get_stream_temp_memory_store().clear(bucket=bucket)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GammaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/v1/stream/self-goals")
def stream_self_goals(status: str | None = None, limit: int = 100) -> dict:
    try:
        return deps.get_stream_self_goal_store().list_goals(status=status, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GammaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/v1/stream/self-goals/{goal_id}/approve")
def stream_self_goal_approve(goal_id: int) -> dict:
    try:
        return deps.get_stream_self_goal_store().set_status(goal_id, status="approved").as_payload()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown goal_id: {goal_id}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/v1/stream/self-goals/{goal_id}/reject")
def stream_self_goal_reject(goal_id: int) -> dict:
    try:
        return deps.get_stream_self_goal_store().set_status(goal_id, status="rejected").as_payload()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown goal_id: {goal_id}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/v1/stream/self-goals/clear")
def stream_self_goals_clear() -> dict:
    try:
        return deps.get_stream_self_goal_store().clear()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/v1/stream/stop", response_model=StreamTurnResult)
def stream_stop(reason: str = "operator_stop") -> StreamTurnResult:
    try:
        live_cancellations = deps._cancel_active_live_turns(reason=reason)
        return deps.get_stream_brain().stop_stream(reason=reason, live_cancellations=live_cancellations)
    except GammaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
