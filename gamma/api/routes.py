from __future__ import annotations

import asyncio
import secrets
import mimetypes
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse

from ..config import settings
from ..conversation.service import ConversationService
from ..errors import ConfigurationError, ConversationError, ExternalServiceError, GammaError
from ..schemas.conversation import ConversationRequest
from ..schemas.response import AssistantResponse, VisionAnalysis
from ..schemas.voice import LiveVoiceJobResponse, VoiceRoundtripResponse, VoiceTranscriptionResponse
from ..stream.brain import StreamBrain
from ..stream.models import StreamInputEvent, StreamTurnResult
from ..stream.output import StreamOutputLogService
from ..integrations.discord import DiscordRuntime
from ..performer.bus import PerformerEventBus, get_performer_event_bus
from ..performer.models import DEFAULT_TARGET_POLICY, KNOWN_TARGET_POLICIES
from ..performer.vtube_studio import VTubeStudioAdapter, VTubeStudioRunner
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
SHANA_DEFAULT_IMAGE = Path(__file__).resolve().parents[2] / "images" / "shana" / "jacket shana mouth closed eyes open.png"
conversation_service = LazySingleton[ConversationService]()
system_status_service = LazySingleton[SystemStatusService]()
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
    return conversation_service.get(ConversationService)


def get_system_status_service() -> SystemStatusService:
    return system_status_service.get(SystemStatusService)


def get_voice_roundtrip_service() -> VoiceRoundtripService:
    return voice_roundtrip_service.get(VoiceRoundtripService)


def get_live_turn_runtime() -> LiveTurnRuntime:
    return live_turn_runtime.get(SubprocessLiveTurnRuntime)


def get_stream_brain() -> StreamBrain:
    return stream_brain.get(StreamBrain)


def get_stream_replay_service() -> StreamReplayService:
    return stream_replay_service.get(StreamReplayService)


def get_stream_output_log_service() -> StreamOutputLogService:
    return stream_output_log_service.get(StreamOutputLogService)


def get_performer_bus() -> PerformerEventBus:
    return performer_event_bus.get(get_performer_event_bus)


def get_vtube_studio_adapter() -> VTubeStudioAdapter:
    return vtube_studio_adapter.get(VTubeStudioAdapter)


def get_vtube_studio_runner() -> VTubeStudioRunner:
    return vtube_studio_runner.get(lambda: VTubeStudioRunner(get_performer_bus(), get_vtube_studio_adapter()))


def get_discord_runtime() -> DiscordRuntime:
    return discord_runtime.get(DiscordRuntime)


def get_stream_temp_memory_store() -> StreamTempMemoryStore:
    return stream_temp_memory_store.get(StreamTempMemoryStore)


def get_stream_self_goal_store() -> StreamSelfGoalStore:
    return stream_self_goal_store.get(StreamSelfGoalStore)


def _cancel_active_live_turns(*, reason: str) -> dict:
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


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Dashboard Moved</title>
  <style>
    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: linear-gradient(135deg, #f5ecdf 0%, #dfd3c1 100%);
      color: #241a12;
      font-family: Georgia, "Times New Roman", serif;
    }
    .card {
      max-width: 720px;
      padding: 28px;
      border-radius: 18px;
      background: rgba(255,255,255,0.75);
      box-shadow: 0 18px 40px rgba(36,26,18,0.12);
    }
    a { color: #8c2f1b; }
  </style>
</head>
<body>
  <div class="card">
    <h1>Dashboard Runs Separately Now</h1>
    <p>The browser dashboard is no longer hosted by the main Shana API process.</p>
    <p>Start the standalone dashboard and open <a href="{settings.dashboard_base_url}/">{settings.dashboard_base_url}/</a>.</p>
  </div>
</body>
</html>"""


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


@router.post("/v1/stream/events", response_model=StreamTurnResult)
def stream_event(event: StreamInputEvent, synthesize_speech: bool = False, fast_mode: bool = True) -> StreamTurnResult:
    try:
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
    try:
        while True:
            payload = await queue.get()
            await websocket.send_json(payload)
    except WebSocketDisconnect:
        pass
    finally:
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
