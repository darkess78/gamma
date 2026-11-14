"""Voice roundtrip, transcription and live voice turn routes."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from . import deps
from ..errors import ConfigurationError, ConversationError, ExternalServiceError, GammaError
from ..schemas.voice import LiveVoiceJobResponse, VoiceRoundtripResponse, VoiceTranscriptionResponse


router = APIRouter()

@router.post("/v1/voice/roundtrip", response_model=VoiceRoundtripResponse)
async def voice_roundtrip(
    audio_file: UploadFile = File(...),
    session_id: str | None = Form(default=None),
    synthesize_speech: bool = Form(default=True),
) -> VoiceRoundtripResponse:
    try:
        return await deps.get_voice_roundtrip_service().run(
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
        return await deps.get_voice_roundtrip_service().run_transcription(audio_file=audio_file)
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
        return await deps.get_live_turn_runtime().start_turn(
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
        return {"items": deps.get_live_turn_runtime().get_recent_history(limit=limit)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/v1/voice/live/{turn_id}", response_model=LiveVoiceJobResponse)
def voice_live_status(turn_id: str) -> LiveVoiceJobResponse:
    try:
        return deps.get_live_turn_runtime().get_turn(turn_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown turn_id: {turn_id}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/v1/voice/live/{turn_id}/cancel", response_model=LiveVoiceJobResponse)
def voice_live_cancel(turn_id: str, reason: str = Form(default="interrupted")) -> LiveVoiceJobResponse:
    try:
        return deps.get_live_turn_runtime().cancel_turn(turn_id, reason=reason)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown turn_id: {turn_id}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
