from __future__ import annotations

import asyncio
import secrets
import mimetypes
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from ..config import settings
from ..conversation.service import ConversationService
from ..errors import ConfigurationError, ConversationError, ExternalServiceError, GammaError
from ..integrations.twitch.trust import VALID_TRUST_LEVELS, ViewerTrustStore
from ..memory.service import MemoryService
from ..schemas.conversation import ConversationRequest
from ..schemas.memory import KnownPersonPayload, MemoryClearRequest, MemoryItemCreate, MemoryItemUpdate, ViewerTrustPayload
from ..schemas.presence import PresenceModeRequest, PresenceWakeRequest
from ..schemas.response import AssistantResponse, VisionAnalysis
from ..schemas.voice import LiveVoiceJobResponse, VoiceRoundtripResponse, VoiceTranscriptionResponse
from ..stream.brain import StreamBrain
from ..stream.models import StreamInputEvent, StreamTurnResult
from ..stream.output import StreamOutputLogService
from ..integrations.discord import DiscordRuntime
from ..performer.bus import PerformerEventBus, get_performer_event_bus
from ..performer.models import DEFAULT_TARGET_POLICY, KNOWN_TARGET_POLICIES
from ..presence import PresenceService, apply_presence_to_stream_event, load_presence_state, save_presence_state
from ..proactive import ProactiveScheduler
from ..performer.vtube_studio import VTubeStudioAdapter, VTubeStudioRunner
from ..observability import current_request_id
from ..stream.replay import StreamEvalReport, StreamReplayService
from ..stream.self_goals import StreamSelfGoalStore
from ..stream.temp_memory import StreamTempMemoryStore
from ..system.lazy_singleton import LazySingleton
from ..system.status import SystemStatusService
from ..voice.live_runtime import LiveTurnRuntime, SubprocessLiveTurnRuntime
from ..voice.roundtrip import VoiceRoundtripService

router = APIRouter()
PERFORMER_STATIC_DIR = Path(__file__).resolve().parents[1] / "performer" / "static"
DASHBOARD_DIR = Path(__file__).resolve().parents[1] / "dashboard"
DASHBOARD_STATIC_DIR = DASHBOARD_DIR / "static"
SHANA_DEFAULT_IMAGE = settings.project_root / "images" / "shana" / "jacket shana mouth closed eyes open.png"
conversation_service = LazySingleton[ConversationService]()
presence_service = LazySingleton[PresenceService]()
proactive_scheduler = LazySingleton[ProactiveScheduler]()
system_status_service = LazySingleton[SystemStatusService]()
memory_service = LazySingleton[MemoryService]()
viewer_trust_store = LazySingleton[ViewerTrustStore]()
voice_roundtrip_service = LazySingleton[VoiceRoundtripService]()
live_turn_runtime = LazySingleton[LiveTurnRuntime]()
stream_brain = LazySingleton[StreamBrain]()
stream_replay_service = LazySingleton[StreamReplayService]()
stream_output_log_service = LazySingleton[StreamOutputLogService]()
stream_temp_memory_store = LazySingleton[StreamTempMemoryStore]()
stream_self_goal_store = LazySingleton[StreamSelfGoalStore]()
performer_event_bus = LazySingleton[PerformerEventBus]()
vtube_studio_adapter = LazySingleton[VTubeStudioAdapter]()
vtube_studio_runner = LazySingleton[VTubeStudioRunner]()
discord_runtime = LazySingleton[DiscordRuntime]()
_vtube_studio_runner_task: asyncio.Task[None] | None = None


def get_conversation_service() -> ConversationService:
    """Get conversation service instance.
    
    Returns:
        ConversationService: Lazy singleton instance.
    """
    return conversation_service.get(ConversationService)


def get_presence_service() -> PresenceService:
    return presence_service.get(lambda: PresenceService(conversation=get_conversation_service(), bus=get_performer_bus()))


def get_proactive_scheduler() -> ProactiveScheduler:
    return proactive_scheduler.get(lambda: ProactiveScheduler(stream_brain=get_stream_brain(), bus=get_performer_bus()))


def get_system_status_service() -> SystemStatusService:
    """Get system status service instance.
    
    Returns:
        SystemStatusService: Lazy singleton instance.
    """
    return system_status_service.get(SystemStatusService)


def get_memory_service() -> MemoryService:
    return memory_service.get(MemoryService)


def get_viewer_trust_store() -> ViewerTrustStore:
    return viewer_trust_store.get(ViewerTrustStore)


def _viewer_trust_payload(record) -> dict:
    return {
        "platform": record.platform,
        "platform_user_id": record.platform_user_id,
        "display_name": record.display_name,
        "trust_level": record.trust_level,
        "notes": record.notes,
        "pronunciation_alias": record.pronunciation_alias,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def get_voice_roundtrip_service() -> VoiceRoundtripService:
    """Get voice roundtrip service instance.
    
    Returns:
        VoiceRoundtripService: Lazy singleton instance.
    """
    return voice_roundtrip_service.get(VoiceRoundtripService)


def get_live_turn_runtime() -> LiveTurnRuntime:
    """Get live turn runtime instance.
    
    Returns:
        LiveTurnRuntime: Lazy singleton instance.
    """
    return live_turn_runtime.get(SubprocessLiveTurnRuntime)


def get_stream_brain() -> StreamBrain:
    """Get stream brain instance.
    
    Returns:
        StreamBrain: Lazy singleton instance.
    """
    return stream_brain.get(StreamBrain)


def get_stream_replay_service() -> StreamReplayService:
    """Get stream replay service instance.
    
    Returns:
        StreamReplayService: Lazy singleton instance.
    """
    return stream_replay_service.get(StreamReplayService)


def get_stream_output_log_service() -> StreamOutputLogService:
    """Get stream output log service instance.
    
    Returns:
        StreamOutputLogService: Lazy singleton instance.
    """
    return stream_output_log_service.get(StreamOutputLogService)


def get_performer_bus() -> PerformerEventBus:
    """Get performer event bus instance.
    
    Returns:
        PerformerEventBus: Lazy singleton instance.
    """
    return performer_event_bus.get(get_performer_event_bus)


def get_vtube_studio_adapter() -> VTubeStudioAdapter:
    """Get VTube Studio adapter instance.
    
    Returns:
        VTubeStudioAdapter: Lazy singleton instance.
    """
    return vtube_studio_adapter.get(VTubeStudioAdapter)


def get_vtube_studio_runner() -> VTubeStudioRunner:
    """Get VTube Studio runner instance.
    
    Returns:
        VTubeStudioRunner: Lazy singleton instance.
    """
    return vtube_studio_runner.get(lambda: VTubeStudioRunner(get_performer_bus(), get_vtube_studio_adapter()))


def get_discord_runtime() -> DiscordRuntime:
    """Get Discord runtime instance.
    
    Returns:
        DiscordRuntime: Lazy singleton instance.
    """
    return discord_runtime.get(DiscordRuntime)


def get_stream_temp_memory_store() -> StreamTempMemoryStore:
    """Get stream temp memory store instance.
    
    Returns:
        StreamTempMemoryStore: Lazy singleton instance.
    """
    return stream_temp_memory_store.get(StreamTempMemoryStore)


def get_stream_self_goal_store() -> StreamSelfGoalStore:
    """Get stream self goal store instance.
    
    Returns:
        StreamSelfGoalStore: Lazy singleton instance.
    """
    return stream_self_goal_store.get(StreamSelfGoalStore)


def _cancel_active_live_turns(*, reason: str) -> dict:
    """Cancel all active live turns with reason.
    
    Args:
        reason: Cancel reason string.
        
    Returns:
        dict: Cancellation results with counts and turn summaries.
    """
    cancel_reason = f"stream_stop:{reason}"
    try:
        cancelled = get_live_turn_runtime().cancel_active_turns(reason=cancel_reason)
    except Exception as exc:
        return {
            "cancel_reason": cancel_reason,
            "cancelled_count": 0,
            "cancelled_turns": [],
            "error": str(exc),
        }
    return {
        "cancel_reason": cancel_reason,
        "cancelled_count": len(cancelled),
        "cancelled_turns": [
            {
                "turn_id": turn.turn_id,
                "status": turn.status,
                "cancel_reason": turn.cancel_reason,
                "cancel_latency_ms": turn.cancel_latency_ms,
            }
            for turn in cancelled
        ],
    }


def _websocket_api_auth_ok(websocket: WebSocket) -> bool:
    """Check WebSocket API authentication.
    
    Args:
        websocket: WebSocket connection.
        
    Returns:
        bool: True if authenticated or API auth disabled.
    """
    if not settings.api_auth_enabled:
        return True
    expected = f"Bearer {settings.api_bearer_token}"
    auth_header = websocket.headers.get("authorization", "")
    if settings.api_bearer_token and secrets.compare_digest(auth_header, expected):
        return True
    token = websocket.query_params.get("token", "")
    return bool(settings.api_bearer_token and secrets.compare_digest(token, settings.api_bearer_token))


@router.get("/")
def root() -> dict[str, str]:
    return {"message": "gamma backend scaffold"}


@router.get("/dashboard")
def dashboard() -> RedirectResponse:
    return RedirectResponse(url=f"{settings.dashboard_base_url}/dashboard", status_code=307)


@router.get("/dashboard/{page_name}")
def dashboard_page_redirect(page_name: str) -> RedirectResponse:
    if page_name == "twitch":
        page_name = "stream"
    if page_name == "talk":
        page_name = "monitor"
    allowed = {"live", "monitor", "status", "presence", "stream", "memory", "settings"}
    if page_name not in allowed:
        raise HTTPException(status_code=404, detail="dashboard page not found")
    return RedirectResponse(url=f"{settings.dashboard_base_url}/dashboard/{page_name}", status_code=307)


@router.get("/presence")
def presence_page_redirect() -> RedirectResponse:
    return RedirectResponse(url=f"{settings.dashboard_base_url}/dashboard/presence", status_code=307)


@router.get("/talk")
def talk_page_redirect() -> RedirectResponse:
    return RedirectResponse(url=f"{settings.dashboard_base_url}/dashboard/monitor", status_code=307)


@router.get("/performer")
def performer_page() -> HTMLResponse:
    return _performer_page(PERFORMER_STATIC_DIR / "performer.html")


@router.get("/performer/assets/shana/default.png")
def performer_default_image() -> FileResponse:
    if not SHANA_DEFAULT_IMAGE.exists():
        raise HTTPException(status_code=404, detail="default performer image not found")
    return FileResponse(SHANA_DEFAULT_IMAGE, media_type="image/png")


@router.get("/v1/assistant/demo", response_model=AssistantResponse)
def assistant_demo() -> AssistantResponse:
    return AssistantResponse(
        spoken_text="Hey. Gamma's scaffold is alive.",
        emotion="neutral",
        motions=[],
        tool_calls=[],
        memory_candidates=[],
    )


@router.get("/v1/memory/stats")
def memory_stats() -> dict[str, str | int]:
    try:
        return get_conversation_service().memory_stats()
    except ConfigurationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ExternalServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except GammaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/v1/memory")
def memory_snapshot(limit: int = 100) -> dict:
    service = get_memory_service()
    return {
        "stats": service.stats(),
        "known_people": service.get_known_people(limit=max(1, min(limit, 1000))),
        "recent_items": service.recent_items(limit=max(1, min(limit, 1000))),
    }


@router.post("/v1/memory/items")
def memory_item_create(payload: MemoryItemCreate) -> dict:
    try:
        item = get_memory_service().create_item(payload.model_dump())
        return {"ok": True, "detail": "Memory created.", "item": item}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/v1/memory/items/{kind}/{item_id}")
def memory_item_update(kind: str, item_id: int, payload: MemoryItemUpdate) -> dict:
    try:
        item = get_memory_service().update_item(kind, item_id, payload.model_dump())
        return {"ok": True, "detail": "Memory updated.", "item": item}
    except ValueError as exc:
        status_code = 404 if "not found" in str(exc).lower() else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.post("/v1/memory/clear")
def memory_clear(payload: MemoryClearRequest) -> dict:
    service = get_memory_service()
    if payload.scope == "all":
        result = service.clear_all()
    elif payload.scope == "recent":
        result = service.clear_recent(minutes=payload.minutes)
    else:
        result = service.clear_selected([item.model_dump() for item in payload.selections])
    return {"ok": True, "detail": "Memory cleared.", **result}


@router.put("/v1/memory/people")
def memory_person_save(payload: KnownPersonPayload) -> dict:
    try:
        person = get_memory_service().save_known_person(payload.model_dump())
        return {"ok": True, "detail": "Known person saved.", "person": person}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/v1/memory/people/{person_id}")
def memory_person_delete(person_id: int) -> dict:
    if not get_memory_service().delete_known_person(person_id):
        raise HTTPException(status_code=404, detail="known person not found")
    return {"ok": True, "detail": "Known person deleted.", "id": person_id}


@router.get("/v1/stream/viewer-trust")
def stream_viewer_trust(platform: str = "twitch", limit: int = 100) -> dict:
    records = get_viewer_trust_store().list_records(platform=platform, limit=limit)
    return {
        "items": [_viewer_trust_payload(record) for record in records],
        "trust_levels": sorted(VALID_TRUST_LEVELS),
    }


@router.put("/v1/stream/viewer-trust")
def stream_viewer_trust_save(payload: ViewerTrustPayload) -> dict:
    trust_level = payload.trust_level.strip().lower()
    if trust_level not in VALID_TRUST_LEVELS:
        raise HTTPException(status_code=400, detail="unsupported trust_level")
    record = get_viewer_trust_store().upsert(
        platform=payload.platform.strip().lower() or "twitch",
        platform_user_id=payload.platform_user_id.strip(),
        display_name=payload.display_name,
        trust_level=trust_level,  # type: ignore[arg-type]
        notes=payload.notes,
        pronunciation_alias=payload.pronunciation_alias,
    )
    return {"ok": True, "record": _viewer_trust_payload(record)}


@router.get("/v1/system/status")
def system_status() -> dict:
    try:
        return get_system_status_service().build_status()
    except ConfigurationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except GammaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/v1/conversation/respond", response_model=AssistantResponse)
def conversation_respond(request: ConversationRequest) -> AssistantResponse:
    try:
        return get_conversation_service().respond(
            user_text=request.user_text,
            session_id=request.session_id,
            synthesize_speech=request.synthesize_speech,
            speaker_ctx=request.speaker,
            fast_mode=request.fast_mode,
        )
    except ConversationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConfigurationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ExternalServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except GammaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/v1/presence")
def shana_presence_status() -> dict:
    return get_presence_service().status()


@router.get("/v1/conversation/sessions/{session_id}/continuity")
def conversation_continuity(session_id: str) -> dict:
    return get_conversation_service().continuity_snapshot(session_id)


@router.delete("/v1/conversation/sessions/{session_id}/continuity")
def conversation_continuity_delete(session_id: str) -> dict:
    deleted = get_conversation_service().delete_continuity_session(session_id)
    return {"ok": True, "session_id": session_id, "deleted_entries": deleted}


@router.get("/v1/performer/targets/{target_policy}/last-durable-output")
def performer_last_durable_output(target_policy: str) -> dict:
    if target_policy not in KNOWN_TARGET_POLICIES:
        raise HTTPException(status_code=400, detail="unsupported target policy")
    return {
        "ok": True,
        "target_policy": target_policy,
        "output": get_conversation_service().durable_output_state(target_policy),
    }


@router.post("/v1/presence/mode")
def shana_presence_mode(request: PresenceModeRequest) -> dict:
    try:
        return get_presence_service().transition(
            request.mode,
            audience=request.audience,
            confirm_public_output=request.confirm_public_output,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/v1/presence/wake")
def shana_presence_wake(request: PresenceWakeRequest) -> dict:
    try:
        return get_presence_service().wake(audience=request.audience, session_id=request.session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GammaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/v1/stream/events", response_model=StreamTurnResult)
def stream_event(event: StreamInputEvent, synthesize_speech: bool = False, fast_mode: bool = True) -> StreamTurnResult:
    try:
        request_id = current_request_id()
        if request_id:
            event.metadata.setdefault("request_id", request_id)
        presence = load_presence_state(downgrade_stale_live=True)
        if presence.get("requires_confirmation"):
            save_presence_state(presence)
        event, synthesize_speech = apply_presence_to_stream_event(
            event,
            synthesize_speech=synthesize_speech,
            state=presence,
        )
        return get_stream_brain().handle_event(
            event,
            synthesize_speech=synthesize_speech,
            fast_mode=fast_mode,
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
        return {"items": get_stream_replay_service().recent_traces(limit=limit)}
    except GammaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/v1/stream/eval/recent", response_model=StreamEvalReport)
def stream_eval_recent(limit: int = 50) -> StreamEvalReport:
    try:
        return get_stream_replay_service().evaluate_recent(limit=limit)
    except GammaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/v1/stream/outputs/recent")
def stream_recent_outputs(limit: int = 50) -> dict[str, list[dict]]:
    try:
        return {"items": get_stream_output_log_service().recent_outputs(limit=limit)}
    except GammaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/v1/stream/queue")
def stream_pending_queue() -> dict:
    try:
        return get_stream_brain().pending_queue()
    except GammaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/v1/performer/events/recent")
def performer_recent_events(limit: int = 50, target_policy: str | None = None, after_sequence: int | None = None) -> dict:
    try:
        bus = get_performer_bus()
        items = [
            event.model_dump()
            for event in bus.recent(limit=limit, target_policy=target_policy, after_sequence=after_sequence)
        ]
        return {
            "items": items,
            "stats": bus.stats(),
            "replay": {
                **bus.replay_window(),
                "after_sequence": after_sequence,
                "gap": bus.replay_gap_after(after_sequence),
            },
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/v1/performer/status")
def performer_status() -> dict:
    try:
        bus = get_performer_bus()
        recent = bus.recent(limit=1)
        recent_by_target = {}
        for target_policy in KNOWN_TARGET_POLICIES:
            target_recent = bus.recent(limit=1, target_policy=target_policy)
            recent_by_target[target_policy] = target_recent[-1].model_dump() if target_recent else None
        return {
            "ok": True,
            "stats": bus.stats(),
            "recent_event": recent[-1].model_dump() if recent else None,
            "recent_by_target": recent_by_target,
            "recent_turns": bus.recent_turns(limit=5),
            "adapters": {
                "vtube_studio": {**get_vtube_studio_adapter().status(), "runner": get_vtube_studio_runner().status()},
                "discord": get_discord_runtime().status(),
            },
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/v1/performer/adapters/vtube-studio/start")
async def performer_vtube_studio_start() -> dict:
    global _vtube_studio_runner_task
    try:
        runner = get_vtube_studio_runner()
        if _vtube_studio_runner_task is not None and not _vtube_studio_runner_task.done():
            return {"ok": True, "already_running": True, "status": runner.status()}
        _vtube_studio_runner_task = asyncio.create_task(runner.run_until_stopped())
        await asyncio.sleep(0)
        return {"ok": True, "already_running": False, "status": runner.status()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/v1/performer/adapters/vtube-studio/stop")
async def performer_vtube_studio_stop() -> dict:
    try:
        runner = get_vtube_studio_runner()
        runner.stop()
        return {"ok": True, "status": runner.status()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/v1/performer/targets/{target_policy}/mute")
def performer_target_mute(target_policy: str, reason: str = "operator") -> dict:
    try:
        return get_performer_bus().set_target_muted(target_policy, True, reason=reason)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/v1/performer/targets/{target_policy}/unmute")
def performer_target_unmute(target_policy: str, reason: str = "operator") -> dict:
    try:
        return get_performer_bus().set_target_muted(target_policy, False, reason=reason)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/v1/performer/targets/{target_policy}/clear")
def performer_target_clear(target_policy: str, reason: str = "operator") -> dict:
    try:
        return get_performer_bus().clear_target(target_policy, reason=reason)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/v1/audio/artifacts/{filename}")
def audio_artifact(filename: str) -> FileResponse:
    safe_name = Path(filename).name
    audio_path = settings.audio_output_dir / safe_name
    try:
        if audio_path.resolve().parent != settings.audio_output_dir.resolve():
            raise HTTPException(status_code=400, detail="invalid audio artifact path")
    except OSError as exc:
        raise HTTPException(status_code=400, detail="invalid audio artifact path") from exc
    if not audio_path.exists() or not audio_path.is_file():
        raise HTTPException(status_code=404, detail="audio artifact not found")
    media_type = mimetypes.guess_type(str(audio_path))[0] or "application/octet-stream"
    return FileResponse(str(audio_path), media_type=media_type)


@router.websocket("/v1/performer/events")
async def performer_events(
    websocket: WebSocket,
    replay_recent: int = 0,
    after_sequence: int | None = None,
    target_policy: str = DEFAULT_TARGET_POLICY,
    client_name: str = "",
) -> None:
    if not _websocket_api_auth_ok(websocket):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    bus = get_performer_bus()
    subscriber_target_policy = target_policy.strip().lower() or DEFAULT_TARGET_POLICY
    subscriber_id, queue = await bus.subscribe(
        replay_recent=max(0, min(replay_recent, 100)),
        after_sequence=after_sequence,
        target_policy=subscriber_target_policy,
        client_name=client_name,
        client_host=websocket.client.host if websocket.client else None,
    )
    await websocket.send_json(
        {
            "type": "ready",
            "subscriber_id": subscriber_id,
            "target_policy": subscriber_target_policy,
            "client_name": client_name.strip().lower() or "unknown_client",
            "replay_recent": max(0, min(replay_recent, 100)),
            "after_sequence": after_sequence,
            "replay_window": bus.replay_window(),
            "replay_gap": bus.replay_gap_after(after_sequence),
            "stats": bus.stats(),
        }
    )
    queue_task = asyncio.create_task(queue.get())
    receive_task = asyncio.create_task(websocket.receive_json())
    try:
        while True:
            done, _pending = await asyncio.wait(
                {queue_task, receive_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if queue_task in done:
                await websocket.send_json(queue_task.result())
                queue_task = asyncio.create_task(queue.get())
            if receive_task in done:
                message = receive_task.result()
                if isinstance(message, dict) and message.get("type") == "subscriber_capabilities":
                    capabilities = bus.update_subscriber_capabilities(
                        subscriber_id,
                        text_ready=message.get("text_ready"),
                        audio_ready=message.get("audio_ready"),
                    )
                    await websocket.send_json({"type": "subscriber_capabilities", "capabilities": capabilities})
                receive_task = asyncio.create_task(websocket.receive_json())
    except WebSocketDisconnect:
        pass
    finally:
        queue_task.cancel()
        receive_task.cancel()
        bus.unsubscribe(subscriber_id)


@router.get("/v1/stream/temp-memory")
def stream_temp_memory(bucket: str | None = None, limit: int = 100) -> dict:
    try:
        return get_stream_temp_memory_store().list_records(bucket=bucket, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GammaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/v1/stream/temp-memory")
def stream_temp_memory_clear(bucket: str | None = None) -> dict:
    try:
        return get_stream_temp_memory_store().clear(bucket=bucket)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GammaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/v1/stream/self-goals")
def stream_self_goals(status: str | None = None, limit: int = 100) -> dict:
    try:
        return get_stream_self_goal_store().list_goals(status=status, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GammaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/v1/stream/self-goals/{goal_id}/approve")
def stream_self_goal_approve(goal_id: int) -> dict:
    try:
        return get_stream_self_goal_store().set_status(goal_id, status="approved").as_payload()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown goal_id: {goal_id}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/v1/stream/self-goals/{goal_id}/reject")
def stream_self_goal_reject(goal_id: int) -> dict:
    try:
        return get_stream_self_goal_store().set_status(goal_id, status="rejected").as_payload()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown goal_id: {goal_id}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/v1/stream/self-goals/clear")
def stream_self_goals_clear() -> dict:
    try:
        return get_stream_self_goal_store().clear()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/v1/stream/stop", response_model=StreamTurnResult)
def stream_stop(reason: str = "operator_stop") -> StreamTurnResult:
    try:
        live_cancellations = _cancel_active_live_turns(reason=reason)
        return get_stream_brain().stop_stream(reason=reason, live_cancellations=live_cancellations)
    except GammaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/v1/conversation/respond-with-image", response_model=AssistantResponse)
async def conversation_respond_with_image(
    user_text: str = Form(...),
    image_file: UploadFile = File(...),
    vision_mode: str | None = Form(default="auto"),
    session_id: str | None = Form(default=None),
    synthesize_speech: bool = Form(default=False),
) -> AssistantResponse:
    try:
        image_bytes = await image_file.read()
        return get_conversation_service().respond_with_image(
            user_text=user_text,
            image_bytes=image_bytes,
            image_media_type=image_file.content_type or "",
            image_filename=image_file.filename,
            vision_mode=vision_mode,
            session_id=session_id,
            synthesize_speech=synthesize_speech,
        )
    except ConversationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConfigurationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ExternalServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except GammaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/v1/vision/analyze", response_model=VisionAnalysis)
async def vision_analyze(
    user_text: str = Form(...),
    image_file: UploadFile = File(...),
    vision_mode: str | None = Form(default="auto"),
) -> VisionAnalysis:
    try:
        image_bytes = await image_file.read()
        return get_conversation_service().analyze_image(
            user_text=user_text,
            image_bytes=image_bytes,
            image_media_type=image_file.content_type or "",
            image_filename=image_file.filename,
            vision_mode=vision_mode,
        )
    except ConversationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConfigurationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ExternalServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except GammaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/v1/voice/roundtrip", response_model=VoiceRoundtripResponse)
async def voice_roundtrip(
    audio_file: UploadFile = File(...),
    session_id: str | None = Form(default=None),
    synthesize_speech: bool = Form(default=True),
) -> VoiceRoundtripResponse:
    try:
        return await get_voice_roundtrip_service().run(
            audio_file=audio_file,
            session_id=session_id,
            synthesize_speech=synthesize_speech,
        )
    except ConversationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConfigurationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ExternalServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except GammaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/v1/voice/transcribe", response_model=VoiceTranscriptionResponse)
async def voice_transcribe(
    audio_file: UploadFile = File(...),
) -> VoiceTranscriptionResponse:
    try:
        return await get_voice_roundtrip_service().run_transcription(audio_file=audio_file)
    except ConversationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConfigurationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ExternalServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except GammaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/v1/voice/live/start", response_model=LiveVoiceJobResponse)
async def voice_live_start(
    audio_file: UploadFile = File(...),
    session_id: str | None = Form(default=None),
    synthesize_speech: bool = Form(default=True),
    response_mode: str = Form(default="simple_chunked"),
    turn_id: str | None = Form(default=None),
) -> LiveVoiceJobResponse:
    try:
        return await get_live_turn_runtime().start_turn(
            audio_file=audio_file,
            session_id=session_id,
            synthesize_speech=synthesize_speech,
            response_mode=response_mode,
            turn_id=turn_id,
        )
    except GammaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/v1/voice/live/history")
def voice_live_history(limit: int = 20) -> dict[str, list[dict]]:
    try:
        return {"items": get_live_turn_runtime().get_recent_history(limit=limit)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(DASHBOARD_STATIC_DIR / "favicon.svg", media_type="image/svg+xml")


@router.get("/monitor")
def monitor_page() -> HTMLResponse:
    return _dashboard_output_page(DASHBOARD_STATIC_DIR / "monitor.html")


@router.get("/overlay/subtitles")
def subtitle_overlay_page() -> HTMLResponse:
    return _dashboard_output_page(DASHBOARD_STATIC_DIR / "overlay.html")


def _dashboard_output_page(path: Path) -> HTMLResponse:
    html = path.read_text(encoding="utf-8")
    config = (
        f'<script>window.GAMMA_SHANA_BASE_URL = "{settings.shana_base_url}";'
        f' window.GAMMA_DASHBOARD_BASE_URL = "{settings.dashboard_base_url}";</script>'
    )
    html = html.replace("</head>", f"  {config}\n</head>", 1)
    return HTMLResponse(html)


def _performer_page(path: Path) -> HTMLResponse:
    html = path.read_text(encoding="utf-8")
    config = (
        f'<script>window.GAMMA_SHANA_BASE_URL = "{settings.shana_base_url}";'
        f' window.GAMMA_DASHBOARD_BASE_URL = "{settings.dashboard_base_url}";</script>'
    )
    html = html.replace("</head>", f"  {config}\n</head>", 1)
    return HTMLResponse(html)


@router.get("/v1/voice/live/{turn_id}", response_model=LiveVoiceJobResponse)
def voice_live_status(turn_id: str) -> LiveVoiceJobResponse:
    try:
        return get_live_turn_runtime().get_turn(turn_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown turn_id: {turn_id}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/v1/voice/live/{turn_id}/cancel", response_model=LiveVoiceJobResponse)
def voice_live_cancel(turn_id: str, reason: str = Form(default="interrupted")) -> LiveVoiceJobResponse:
    try:
        return get_live_turn_runtime().cancel_turn(turn_id, reason=reason)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown turn_id: {turn_id}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
