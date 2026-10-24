from __future__ import annotations

import asyncio
import anyio
import secrets
import mimetypes
from concurrent.futures import CancelledError
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from ..config import settings
from ..conversation.service import ConversationService
from ..brain.runtime import get_brain_queue, await_brain_job
from ..brain.runtime import stream_ingress_epoch, invalidate_stream_ingress
from ..brain.queue import BrainQueueClosed, BrainQueueFull
from ..voice.uploads import VoiceUploadTooLarge
from ..delivery.executor import DeliveryExecutor, DeliveryQueueFull
from ..delivery.playback import get_playback_ledger
from ..delivery.authority import DeliveryDenied
from ..delivery.digest import fingerprint
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
from ..integrations.minecraft.coordinator import MinecraftCoordinator
from ..integrations.minecraft.websocket import create_minecraft_control_router
from ..performer.bus import PerformerEventBus, get_performer_event_bus
from ..performer.models import DEFAULT_TARGET_POLICY, KNOWN_TARGET_POLICIES, PerformerOutputEvent
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
_image_delivery = DeliveryExecutor(capacity=8)
_wake_delivery = DeliveryExecutor(capacity=8)
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
minecraft_coordinator = LazySingleton[MinecraftCoordinator]()
_minecraft_lifespan_active = False
_vtube_studio_runner_task: asyncio.Task[None] | None = None


def get_conversation_service() -> ConversationService:
    """Get conversation service instance.
    
    Returns:
        ConversationService: Lazy singleton instance.
    """
    return conversation_service.get(lambda: ConversationService(use_worker=True))


def get_presence_service() -> PresenceService:
    return presence_service.get(lambda: PresenceService(conversation=get_conversation_service(), bus=get_performer_bus()))


def get_proactive_scheduler() -> ProactiveScheduler:
    from ..brain.silent_work import SilentWork
    return proactive_scheduler.get(lambda: ProactiveScheduler(stream_brain=get_stream_brain(), bus=get_performer_bus(),
        silent_prepare=get_conversation_service().prepare_silent_cycle, silent_work=SilentWork()))


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
    return voice_roundtrip_service.get(lambda: VoiceRoundtripService(conversation=get_conversation_service()))


def get_live_turn_runtime() -> LiveTurnRuntime:
    """Get live turn runtime instance.
    
    Returns:
        LiveTurnRuntime: Lazy singleton instance.
    """
    def create():
        if settings.live_voice_runtime in {"core", "subprocess"}:
            from ..voice.core_live import CoreLiveTurnRuntime
            return CoreLiveTurnRuntime(media=get_voice_roundtrip_service(),
                conversation=get_conversation_service(), bus=get_performer_bus(), compatibility_modes=True)
        raise ConfigurationError("unsupported live voice runtime")
    return live_turn_runtime.get(create)


def stop_voice_transcription() -> None:
    value = voice_roundtrip_service._value
    if value is not None:
        value.close()


def stop_core_live_runtime() -> None:
    # Shutdown must not construct an unused runtime or touch legacy subprocesses.
    value = live_turn_runtime._value
    if value is not None and hasattr(value, "close"):
        value.close()


def get_stream_brain() -> StreamBrain:
    """Get stream brain instance.
    
    Returns:
        StreamBrain: Lazy singleton instance.
    """
    return stream_brain.get(lambda: StreamBrain(conversation=get_conversation_service()))


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


def get_minecraft_coordinator() -> MinecraftCoordinator:
    """Return the authoritative Shana-owned Minecraft coordinator."""

    return minecraft_coordinator.get(
        lambda: MinecraftCoordinator(enabled=settings.minecraft.configured)
    )


def start_minecraft_coordinator() -> MinecraftCoordinator:
    """Claim the process-local coordinator for one Shana application lifespan."""

    global _minecraft_lifespan_active
    if _minecraft_lifespan_active:
        raise RuntimeError("a Shana application lifespan is already active")
    coordinator = get_minecraft_coordinator()
    _minecraft_lifespan_active = True
    return coordinator


def get_active_minecraft_coordinator() -> MinecraftCoordinator | None:
    """Return the coordinator only while its owning lifespan remains active."""

    if not _minecraft_lifespan_active:
        return None
    return minecraft_coordinator._value


def reset_minecraft_coordinator(coordinator: MinecraftCoordinator) -> None:
    """Detach all authority and discard the process-local coordinator."""

    global _minecraft_lifespan_active
    if minecraft_coordinator._value is not coordinator:
        return
    _minecraft_lifespan_active = False
    coordinator.detach_transport()
    coordinator.reset()
    minecraft_coordinator.set(None)


router.include_router(
    create_minecraft_control_router(
        coordinator_provider=get_active_minecraft_coordinator,
        control_token_provider=lambda: settings.minecraft.control_token.get_secret_value(),
        maximum_inbound_bytes_provider=lambda: settings.minecraft.maximum_inbound_bytes,
    )
)


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


@router.get("/performer/assets/playback-requests.js")
def playback_client_asset() -> FileResponse:
    return FileResponse(DASHBOARD_STATIC_DIR / "playback-requests.js", media_type="text/javascript")


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
        def decide():
            service = get_conversation_service()
            response = service.respond(
                user_text=request.user_text,
                session_id=request.session_id,
                synthesize_speech=request.synthesize_speech,
                speaker_ctx=request.speaker,
                fast_mode=request.fast_mode,
                evaluation_mode=request.evaluation_mode,
                defer_delivery=True,
            )
            return service, response
        service, response = get_brain_queue().submit(decide).result()
        if not request.evaluation_mode:
            service.deliver_response(response, synthesize_speech=request.synthesize_speech)
        return response
    except (BrainQueueClosed, BrainQueueFull, CancelledError) as exc:
        raise HTTPException(status_code=503, detail="brain queue unavailable", headers={"Retry-After": "1"}) from exc
    except ConversationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConfigurationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ExternalServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except GammaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/v1/brain/status")
def brain_status() -> dict:
    from ..brain.process import get_brain_process
    return {**get_brain_queue().status(), "worker": get_brain_process().status()}


class SilentBudget(BaseModel):
    budget: int = Field(default=1, ge=1, le=8, strict=True)
    cooldown_seconds: int = Field(default=60, ge=5, le=3600, strict=True)
    cycle_seconds: int = Field(default=60, ge=1, le=60, strict=True)


class SilentObservation(BaseModel):
    key: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=2000)
    goal_id: int = Field(gt=0, strict=True)
    ttl_seconds: int = Field(default=120, ge=1, le=3600, strict=True)


@router.post("/v1/autonomy/silent/observations")
def silent_observe(payload: SilentObservation):
    try:
        return get_proactive_scheduler().silent.work.observe(**payload.model_dump())
    except ValueError as exc:
        raise HTTPException(400, "observation requires valid input and an approved goal") from exc


@router.post("/v1/autonomy/silent/pause")
def silent_pause():
    return get_proactive_scheduler().silent.pause()


@router.post("/v1/autonomy/silent/resume")
def silent_resume():
    return get_proactive_scheduler().silent.resume()


@router.get("/v1/autonomy/silent")
def silent_status():
    return get_proactive_scheduler().silent.status()


@router.post("/v1/autonomy/silent/enable")
def silent_enable(payload: SilentBudget):
    try:
        return get_proactive_scheduler().silent.enable(**payload.model_dump())
    except RuntimeError as exc:
        raise HTTPException(409, "silent cycle is active") from exc


@router.post("/v1/autonomy/silent/disable")
def silent_disable():
    return get_proactive_scheduler().silent.disable()


@router.post("/v1/brain/{action}")
def brain_control(action: str) -> dict:
    if action not in {"start", "stop", "pause", "resume"}:
        raise HTTPException(status_code=400, detail="unsupported brain action")
    queue = get_brain_queue()
    try:
        from ..brain.process import get_brain_process, BrainUnavailable
        from ..brain.runtime import start_brain_queue
        if action == "stop":
            get_proactive_scheduler().silent.disable()
            get_brain_process().control(action)
            queue.close(timeout=0)
        elif action == "start":
            get_brain_process().control(action)
            queue = start_brain_queue()
        elif action == "pause":
            get_proactive_scheduler().silent.pause()
            queue.pause()
            get_brain_process().control(action)
        else:
            get_brain_process().control(action)
            queue.resume()
            get_proactive_scheduler().silent.resume()
    except (BrainQueueClosed, BrainUnavailable) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {**queue.status(), "worker": get_brain_process().status()}


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
        service = get_presence_service()
        prepared = service.prepare_wake(audience=request.audience)
        decision = get_brain_queue().submit(lambda: service.decide_wake(
            audience=request.audience, session_id=request.session_id, prepared_state=prepared)).result()
        return _wake_delivery.run_sync(lambda: service.deliver_wake(decision), on_cancel=lambda: service.cancel_wake(decision))
    except DeliveryQueueFull as exc:
        raise HTTPException(503, "wake delivery queue full", headers={"Retry-After": "1"}) from exc
    except (BrainQueueClosed, BrainQueueFull, CancelledError) as exc:
        raise HTTPException(status_code=503, detail="brain queue unavailable", headers={"Retry-After": "1"}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GammaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def submit_stream_event(event: StreamInputEvent, synthesize_speech: bool = False, fast_mode: bool = True, *, wrapper=None):
    event = event.model_copy(deep=True)
    accepted_epoch = stream_ingress_epoch()

    def execute():
        if accepted_epoch != stream_ingress_epoch():
            raise HTTPException(status_code=409, detail="stream request superseded by stop")
        request_id = current_request_id()
        if request_id:
            event.metadata.setdefault("request_id", request_id)
        presence = load_presence_state(downgrade_stale_live=True)
        if presence.get("requires_confirmation"):
            save_presence_state(presence)
        prepared_event, speech = apply_presence_to_stream_event(
            event,
            synthesize_speech=synthesize_speech,
            state=presence,
        )
        def delivery_allowed():
            current = load_presence_state(downgrade_stale_live=True)
            return accepted_epoch == stream_ingress_epoch() and all(
                current.get(key) == presence.get(key) for key in ("control_revision", "mode", "audience", "outputs"))
        return get_stream_brain().handle_event(
            prepared_event,
            synthesize_speech=speech,
            fast_mode=fast_mode,
            defer_delivery=True,
            delivery_allowed=delivery_allowed,
        )

    return get_brain_queue().submit(lambda: wrapper(execute) if wrapper else execute())


@router.post("/v1/stream/events", response_model=StreamTurnResult)
def stream_event(event: StreamInputEvent, synthesize_speech: bool = False, fast_mode: bool = True) -> StreamTurnResult:
    try:
        return submit_stream_event(event, synthesize_speech, fast_mode).result().result()
    except (BrainQueueClosed, BrainQueueFull, CancelledError) as exc:
        raise HTTPException(status_code=503, detail="brain queue unavailable", headers={"Retry-After": "1"}) from exc
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


@router.get("/v1/delivery/{grant_id}/audio")
def approved_audio(grant_id: str) -> Response:
    try:
        data, content_type, digest = get_playback_ledger().read_audio(grant_id)
    except DeliveryDenied as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    return Response(content=data, media_type=content_type,
        headers={"Cache-Control": "no-store", "X-Content-SHA256": digest})


@router.get("/v1/audio/artifacts/{filename}")
def audio_artifact(filename: str) -> FileResponse:
    raise HTTPException(status_code=410, detail="Use a current approved delivery audio URL")


def _playback_presence():
    state = load_presence_state()
    return state, fingerprint({key: state.get(key) for key in
        ("mode", "audience", "outputs", "requires_confirmation", "last_confirmed_live_at")})


def _offer_playback(ledger, bus, lease_id, subscriber_id, event):
    _, revision = _playback_presence()
    return ledger.offer(lease_id, subscriber_id, event,
        target_epoch=bus.audio_target_epoch(event["target_policy"], subscriber_id), presence=revision)


def _playback_context(bus, subscriber_id):
    state, revision = _playback_presence()
    outputs = state.get("outputs") or {}
    def eligible(target):
        allowed = bool(outputs.get("voice")) and state.get("mode") in {"wake", "go_live"}
        if target == "stream_public":
            allowed = allowed and state.get("mode") == "go_live" and bool(outputs.get("stream_public")) and not state.get("requires_confirmation")
        else:
            allowed = allowed and bool(outputs.get("dashboard_monitor"))
        return allowed and bus.audio_delivery_allowed(subscriber_id, target)
    return dict(eligible=eligible,
        target_epoch=lambda target: bus.audio_target_epoch(target, subscriber_id), presence=revision)


def _prepare_playback(ledger, bus, lease_id, subscriber_id, message):
    return ledger.prepare(lease_id, subscriber_id, message, **_playback_context(bus, subscriber_id))


def _revoke_playback(ledger, bus, lease_id, subscriber_id):
    return ledger.revoke_obsolete(lease_id, subscriber_id, **_playback_context(bus, subscriber_id))


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
    if target_policy.strip().lower() not in KNOWN_TARGET_POLICIES:
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
    ledger = get_playback_ledger()
    lease_id = await asyncio.to_thread(ledger.open, receiver=subscriber_id, target=subscriber_target_policy)
    await websocket.send_json(
        {
            "type": "ready",
            "subscriber_id": subscriber_id,
            "playback_lease": {"schema_version": 1, "lease_id": lease_id},
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
    loop = asyncio.get_running_loop()
    next_revocation_check = loop.time() + 0.5
    try:
        while True:
            done, _pending = await asyncio.wait(
                {queue_task, receive_task},
                return_when=asyncio.FIRST_COMPLETED,
                timeout=max(0, next_revocation_check - loop.time()),
            )
            if receive_task in done and receive_task.exception() is not None:
                receive_task.result()
            if loop.time() >= next_revocation_check:
                revoked = await asyncio.to_thread(_revoke_playback, ledger, bus, lease_id, subscriber_id)
                for event_id in revoked:
                    await websocket.send_json({"type": "playback_revoked", "lease_id": lease_id, "event_id": event_id})
                next_revocation_check = loop.time() + 0.5
            if queue_task in done:
                event = queue_task.result()
                from ..delivery.execution import validate_event
                try:
                    if not event.get("replay"):
                        await asyncio.to_thread(validate_event, PerformerOutputEvent(**event), ledger=ledger, bus=bus)
                except DeliveryDenied:
                    queue_task = asyncio.create_task(queue.get())
                    continue
                audio_payload = event.get("payload", {})
                has_audio = any(audio_payload.get(key) for key in ("audio_url", "audio_base64", "audio_path"))
                if has_audio:
                    if event.get("replay") or not bus.audio_delivery_allowed(subscriber_id, event.get("target_policy")):
                        event = {**event, "payload": {key: value for key, value in audio_payload.items()
                            if key not in {"audio_url", "audio_base64", "audio_path"}}}
                    else:
                        try:
                            event = {**event, "playback": await asyncio.to_thread(_offer_playback, ledger, bus, lease_id, subscriber_id, event)}
                        except DeliveryDenied:
                            event = None
                if event is not None:
                    text_claim = None
                    from ..delivery.execution import claim_event, finish_event
                    try:
                        if event.get("type") == "subtitle_update" and not event.get("replay"):
                            text_claim = await asyncio.to_thread(claim_event, PerformerOutputEvent(**event),
                                f"websocket_text:{lease_id}", ledger=ledger, bus=bus)
                        await websocket.send_json(event)
                    except DeliveryDenied:
                        pass
                    except BaseException:
                        await asyncio.to_thread(finish_event, text_claim, succeeded=False)
                        raise
                    else:
                        await asyncio.to_thread(finish_event, text_claim, succeeded=True)
                queue_task = asyncio.create_task(queue.get())
            if receive_task in done:
                message = receive_task.result()
                if isinstance(message, dict) and message.get("type") == "subscriber_capabilities":
                    await asyncio.to_thread(ledger.renew, lease_id, subscriber_id)
                    capabilities = bus.update_subscriber_capabilities(
                        subscriber_id,
                        text_ready=message.get("text_ready"),
                        audio_ready=message.get("audio_ready"),
                    )
                    await websocket.send_json({"type": "subscriber_capabilities", "capabilities": capabilities})
                elif isinstance(message, dict) and message.get("type") == "delivery_offer":
                    grant = message.get("grant_id")
                    claim = None
                    try:
                        if not isinstance(grant, str) or not grant or len(grant) > 128:
                            raise DeliveryDenied("invalid_delivery_grant")
                        response, context = await asyncio.to_thread(ledger.response_for_grant, grant)
                        if context["target"] != subscriber_target_policy:
                            raise DeliveryDenied("delivery_target_mismatch")
                        item = PerformerOutputEvent(type="speech_chunk_ready", turn_id=response.route_trace_id,
                            target_policy=context["target"], payload={"text": response.speech_text or "",
                                "delivery_grant": grant, "audio_url": response.tts_metadata.get("delivery_audio_url")})
                        from ..delivery.execution import validate_event
                        await asyncio.to_thread(validate_event, item, ledger=ledger, bus=bus)
                        if not item.payload.get("audio_url"):
                            raise DeliveryDenied("delivery_has_no_audio")
                        from ..delivery.execution import claim_event, finish_event
                        claim = await asyncio.to_thread(claim_event, item, "explicit_playback", ledger=ledger, bus=bus)
                        event = item.model_dump()
                        event["playback"] = await asyncio.to_thread(_offer_playback, ledger, bus, lease_id, subscriber_id, event)
                        await websocket.send_json(event)
                        await asyncio.to_thread(finish_event, claim, succeeded=True)
                    except (ValueError, DeliveryDenied):
                        if claim is not None:
                            await asyncio.to_thread(finish_event, claim, succeeded=False)
                        await websocket.send_json({"type": "playback_rejected", "grant_id": grant})
                elif isinstance(message, dict) and message.get("type") == "playback_prepare":
                    try:
                        permit = await asyncio.to_thread(_prepare_playback, ledger, bus, lease_id, subscriber_id, message)
                        await websocket.send_json(permit)
                    except (ValueError, DeliveryDenied):
                        await websocket.send_json({"type": "playback_rejected", "lease_id": lease_id,
                            "event_id": str(message.get("event_id", ""))[:128]})
                elif isinstance(message, dict) and message.get("type") == "playback_ack":
                    try:
                        await asyncio.to_thread(_revoke_playback, ledger, bus, lease_id, subscriber_id)
                        receipt = await asyncio.to_thread(ledger.acknowledge, lease_id, subscriber_id, message)
                        await websocket.send_json({"type": "playback_receipt", **receipt})
                    except (ValueError, DeliveryDenied):
                        await websocket.send_json({"type": "playback_rejected", "lease_id": lease_id,
                            "event_id": str(message.get("event_id", ""))[:128]})
                receive_task = asyncio.create_task(websocket.receive_json())
    except DeliveryDenied:
        await websocket.close(code=1008)
    except WebSocketDisconnect:
        pass
    except RuntimeError as exc:
        # ASGI may observe transport closure before receive_json delivers disconnect.
        # This is a closed receiver, never permission to retain or replay its audio.
        if not (str(exc).startswith("Unexpected ASGI message 'websocket.send'")
                or str(exc).startswith('Cannot call "send" once a close message has been sent')):
            raise
    finally:
        queue_task.cancel()
        receive_task.cancel()
        bus.unsubscribe(subscriber_id)
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(ledger.close, lease_id, subscriber_id)


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
        invalidate_stream_ingress()
        live_cancellations = _cancel_active_live_turns(reason=reason)
        return get_stream_brain().stop_stream(reason=reason, live_cancellations=live_cancellations)
    except GammaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/v1/delivery/{grant_id}/cancel")
def cancel_delivery(grant_id: str) -> dict:
    return {"cancelled": get_conversation_service().cancel_delivery(grant_id)}


@router.post("/v1/conversation/respond-with-image", response_model=AssistantResponse)
async def conversation_respond_with_image(
    user_text: str = Form(...),
    image_file: UploadFile = File(...),
    vision_mode: str | None = Form(default="auto"),
    session_id: str | None = Form(default=None),
    synthesize_speech: bool = Form(default=False),
) -> AssistantResponse:
    try:
        image_bytes = await image_file.read(settings.vision_max_image_bytes + 1)
        if len(image_bytes) > settings.vision_max_image_bytes:
            raise ConversationError(f"Image exceeds SHANA_VISION_MAX_IMAGE_BYTES ({settings.vision_max_image_bytes} bytes).")
        def decide():
            service = get_conversation_service()
            response = service.respond_with_image(
                user_text=user_text,
                image_bytes=image_bytes,
                image_media_type=image_file.content_type or "",
                image_filename=image_file.filename,
                vision_mode=vision_mode,
                session_id=session_id,
                synthesize_speech=synthesize_speech,
                defer_delivery=True,
            )
            return service, response
        service, response = await await_brain_job(decide)
        grant = response.tts_metadata.get("delivery_grant")
        await _image_delivery.run(lambda: service.deliver_response(response, synthesize_speech=synthesize_speech),
            on_cancel=lambda: service.cancel_delivery(grant) if grant else None)
        return response
    except (BrainQueueClosed, BrainQueueFull, DeliveryQueueFull) as exc:
        raise HTTPException(status_code=503, detail="brain queue unavailable", headers={"Retry-After": "1"}) from exc
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
        image_bytes = await image_file.read(settings.vision_max_image_bytes + 1)
        if len(image_bytes) > settings.vision_max_image_bytes:
            raise ConversationError(f"Image exceeds SHANA_VISION_MAX_IMAGE_BYTES ({settings.vision_max_image_bytes} bytes).")
        return await await_brain_job(lambda: get_conversation_service().analyze_image(
            user_text=user_text,
            image_bytes=image_bytes,
            image_media_type=image_file.content_type or "",
            image_filename=image_file.filename,
            vision_mode=vision_mode,
        ))
    except (BrainQueueClosed, BrainQueueFull) as exc:
        raise HTTPException(status_code=503, detail="brain queue unavailable", headers={"Retry-After": "1"}) from exc
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
    except VoiceUploadTooLarge as exc:
        raise HTTPException(status_code=413, detail="voice upload exceeds limit") from exc
    except (BrainQueueClosed, BrainQueueFull) as exc:
        raise HTTPException(status_code=503, detail="brain queue unavailable", headers={"Retry-After": "1"}) from exc
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
    except VoiceUploadTooLarge as exc:
        raise HTTPException(status_code=413, detail="voice upload exceeds limit") from exc
    except (BrainQueueClosed, BrainQueueFull) as exc:
        raise HTTPException(status_code=503, detail="transcription queue unavailable", headers={"Retry-After": "1"}) from exc
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
    except VoiceUploadTooLarge as exc:
        raise HTTPException(status_code=413, detail="voice upload exceeds limit") from exc
    except BrainQueueFull as exc:
        raise HTTPException(status_code=503, detail="live turn capacity unavailable", headers={"Retry-After": "1"}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
