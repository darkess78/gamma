from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse

from ..config import settings
from ..conversation.service import ConversationService
from ..errors import ConfigurationError, ConversationError, ExternalServiceError, GammaError
from ..schemas.conversation import ConversationRequest
from ..schemas.response import AssistantResponse, VisionAnalysis
from ..schemas.voice import LiveVoiceJobResponse, VoiceRoundtripResponse, VoiceTranscriptionResponse
from ..stream.brain import StreamBrain
from ..stream.models import StreamInputEvent, StreamTurnResult
from ..stream.output import StreamOutputLogService
from ..stream.replay import StreamEvalReport, StreamReplayService
from ..system.lazy_singleton import LazySingleton
from ..system.status import SystemStatusService
from ..voice.live_runtime import LiveTurnRuntime, SubprocessLiveTurnRuntime
from ..voice.roundtrip import VoiceRoundtripService

router = APIRouter()
conversation_service = LazySingleton[ConversationService]()
system_status_service = LazySingleton[SystemStatusService]()
voice_roundtrip_service = LazySingleton[VoiceRoundtripService]()
live_turn_runtime = LazySingleton[LiveTurnRuntime]()
stream_brain = LazySingleton[StreamBrain]()
stream_replay_service = LazySingleton[StreamReplayService]()
stream_output_log_service = LazySingleton[StreamOutputLogService]()


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
