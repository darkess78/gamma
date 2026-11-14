"""Conversation, presence, system status and vision routes."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from . import deps
from ..errors import ConfigurationError, ConversationError, ExternalServiceError, GammaError
from ..schemas.conversation import ConversationRequest
from ..schemas.presence import PresenceModeRequest, PresenceWakeRequest
from ..schemas.response import AssistantResponse, VisionAnalysis


router = APIRouter()

@router.get("/v1/assistant/demo", response_model=AssistantResponse)
def assistant_demo() -> AssistantResponse:
    return AssistantResponse(
        spoken_text="Hey. Gamma's scaffold is alive.",
        emotion="neutral",
        motions=[],
        tool_calls=[],
        memory_candidates=[],
    )


@router.get("/v1/system/status")
def system_status() -> dict:
    try:
        return deps.get_system_status_service().build_status()
    except ConfigurationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except GammaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/v1/conversation/respond", response_model=AssistantResponse)
def conversation_respond(request: ConversationRequest) -> AssistantResponse:
    try:
        return deps.get_conversation_service().respond(
            user_text=request.user_text,
            session_id=request.session_id,
            synthesize_speech=request.synthesize_speech,
            speaker_ctx=request.speaker,
            fast_mode=request.fast_mode,
            evaluation_mode=request.evaluation_mode,
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
    return deps.get_presence_service().status()


@router.get("/v1/conversation/sessions/{session_id}/continuity")
def conversation_continuity(session_id: str) -> dict:
    return deps.get_conversation_service().continuity_snapshot(session_id)


@router.delete("/v1/conversation/sessions/{session_id}/continuity")
def conversation_continuity_delete(session_id: str) -> dict:
    deleted = deps.get_conversation_service().delete_continuity_session(session_id)
    return {"ok": True, "session_id": session_id, "deleted_entries": deleted}


@router.post("/v1/presence/mode")
def shana_presence_mode(request: PresenceModeRequest) -> dict:
    try:
        return deps.get_presence_service().transition(
            request.mode,
            audience=request.audience,
            confirm_public_output=request.confirm_public_output,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/v1/presence/wake")
def shana_presence_wake(request: PresenceWakeRequest) -> dict:
    try:
        return deps.get_presence_service().wake(audience=request.audience, session_id=request.session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GammaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


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
        return deps.get_conversation_service().respond_with_image(
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
        return deps.get_conversation_service().analyze_image(
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
