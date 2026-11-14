"""TTS, STT, LLM and voice-roundtrip test routes, audio files, assistant settings and vision."""
from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter
from fastapi import File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from . import deps
from ..config import settings as _app_settings
from ..schemas.response import AssistantResponse, VisionAnalysis
from ..schemas.voice import VoiceRoundtripResponse

router = APIRouter()

@router.post("/api/providers/tts/start")
def start_tts() -> dict:
    """Start TTS service.
    
    Returns:
        dict: Confirmation response.
    """
    return deps.get_dashboard_service().start_tts()


@router.post("/api/providers/tts/stop")
def stop_tts() -> dict:
    """Stop TTS service.
    
    Returns:
        dict: Confirmation response.
    """
    return deps.get_dashboard_service().stop_tts()


@router.post("/api/providers/stt/test")
def test_stt() -> dict:
    """Test STT provider.
    
    Returns:
        dict: STT test results.
    """
    return deps.get_dashboard_service().test_stt()


@router.post("/api/providers/tts/test")
def test_tts() -> dict:
    """Test TTS provider.
    
    Returns:
        dict: TTS test results.
    """
    return deps.get_dashboard_service().test_tts()


@router.post("/api/providers/tts/synthesize")
async def tts_synthesize_file(
    text_file: UploadFile = File(...),
) -> dict:
    """Synthesize text from file with TTS.
    
    Args:
        text_file: UploadFile containing text to synthesize.
    
    Returns:
        dict: Synthesis results including audio path.
        Raises:
            HTTPException: 400 if file not valid UTF-8 or empty, 500 on failure.
    """
    raw = await text_file.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="file must be UTF-8 encoded text")
    text = text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="file is empty")
    result = deps.get_dashboard_service().synthesize_text(text)
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result.get("detail", "synthesis failed"))
    return result


@router.post("/api/providers/tts/select")
async def select_tts_provider(request: Request) -> dict:
    """Select TTS provider.
    
    Args:
        request: HTTP request with JSON payload containing provider name.
    
    Returns:
        dict: Selected provider info.
        Raises:
            HTTPException: 400 if provider name invalid.
    """
    payload = await request.json()
    provider = str(payload.get("provider", "")).strip()
    if not provider:
        raise HTTPException(status_code=400, detail="provider is required")
    try:
        return deps.get_dashboard_service().set_tts_provider(provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/providers/tts/profile")
async def select_tts_profile(request: Request) -> dict:
    """Select TTS profile.
    
    Args:
        request: HTTP request with JSON payload containing profile name.
    
    Returns:
        dict: Selected profile info.
        Raises:
            HTTPException: 400 if profile name invalid.
    """
    payload = await request.json()
    profile = str(payload.get("profile", "")).strip()
    try:
        return deps.get_dashboard_service().set_tts_profile(profile)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/providers/tts/profile/save")
async def save_tts_profile(request: Request) -> dict:
    """Save TTS profile configuration.
    
    Args:
        request: HTTP request with JSON payload containing profile data.
    
    Returns:
        dict: Saved profile info.
        Raises:
            HTTPException: 400 if payload invalid.
    """
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="object payload is required")
    try:
        return deps.get_dashboard_service().save_tts_profile(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/audio/{filename}")
def serve_audio(filename: str) -> FileResponse:
    """Serve audio file.
    
    Args:
        filename: Audio filename to serve.
    
    Returns:
        FileResponse: Audio file with appropriate MIME type.
        Raises:
            HTTPException: 404 if file not found, 400 for invalid paths.
    """
    safe_name = Path(filename).name
    audio_path = _app_settings.audio_output_dir / safe_name
    if not audio_path.exists() or not audio_path.is_file():
        raise HTTPException(status_code=404, detail="audio file not found")
    if audio_path.resolve().parent != _app_settings.audio_output_dir.resolve():
        raise HTTPException(status_code=400, detail="invalid path")
    media_type = mimetypes.guess_type(str(audio_path))[0] or "application/octet-stream"
    return FileResponse(str(audio_path), media_type=media_type)


@router.delete("/api/audio/{filename}")
def delete_audio(filename: str) -> dict:
    """Delete audio file.
    
    Args:
        filename: Audio filename to delete.
    
    Returns:
        dict: Confirmation with deleted filename.
        Raises:
            HTTPException: 404 if file not found, 400 for invalid paths.
    """
    safe_name = Path(filename).name
    audio_path = _app_settings.audio_output_dir / safe_name
    if not audio_path.exists() or not audio_path.is_file():
        raise HTTPException(status_code=404, detail="audio file not found")
    if audio_path.resolve().parent != _app_settings.audio_output_dir.resolve():
        raise HTTPException(status_code=400, detail="invalid path")
    audio_path.unlink()
    return {"ok": True, "deleted": safe_name}


@router.post("/api/providers/llm/test")
def test_llm() -> dict:
    """Test LLM provider.
    
    Returns:
        dict: LLM test results.
    """
    return deps.get_dashboard_service().test_llm()


@router.post("/api/providers/voice/test")
def test_voice_roundtrip() -> dict:
    """Test voice roundtrip.
    
    Returns:
        dict: Voice roundtrip test results.
    """
    return deps.get_dashboard_service().test_voice_roundtrip()


@router.get("/api/assistant/settings")
def assistant_settings() -> dict:
    """Return assistant runtime settings.
    
    Returns:
        dict: Settings dict including targets and mute states.
    """
    return {"settings": deps.get_dashboard_service().assistant_runtime_settings()}


@router.post("/api/assistant/settings")
async def save_assistant_settings(request: Request) -> dict:
    """Save assistant runtime settings.
    
    Args:
        request: HTTP request with JSON payload containing settings data.
    
    Returns:
        dict: Saved settings.
        Raises:
            HTTPException: 400 if payload invalid.
    """
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="object payload is required")
    try:
        return deps.get_dashboard_service().save_assistant_runtime_settings(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/voice/roundtrip", response_model=VoiceRoundtripResponse)
async def dashboard_voice_roundtrip(
    audio_file: UploadFile = File(...),
    session_id: str | None = Form(default=None),
    synthesize_speech: bool = Form(default=True),
) -> VoiceRoundtripResponse:
    """Run voice roundtrip test via WebSocket.
    
    Args:
        audio_file: Audio file to test.
        session_id: Optional session ID.
        synthesize_speech: Whether to synthesize speech.
    
    Returns:
        VoiceRoundtripResponse: Test results including voice analysis.
    """
    return deps.get_dashboard_service().run_remote_voice_roundtrip(
        audio_bytes=await audio_file.read(),
        filename=audio_file.filename or "dashboard-voice.wav",
        content_type=audio_file.content_type or "application/octet-stream",
        session_id=session_id,
        synthesize_speech=synthesize_speech,
    )


@router.get("/api/voice/live/history")
def dashboard_live_voice_history(limit: int = 20) -> dict:
    """Get live voice history.
    
    Args:
        limit: Optional max history items (default 20).
    
    Returns:
        dict: Live voice history records.
    """
    return deps.get_dashboard_service().remote_live_history(limit=limit)


@router.post("/api/vision/analyze", response_model=VisionAnalysis)
async def dashboard_vision_analyze(
    image_file: UploadFile = File(...),
    user_text: str = Form(...),
    vision_mode: str | None = Form(default="auto"),
) -> VisionAnalysis:
    """Analyze an image via vision service.
    
    Args:
        image_file: Image file to analyze.
        user_text: Optional user text for analysis.
        vision_mode: Vision mode (default "auto").
    
    Returns:
        VisionAnalysis: Analysis results with image and text.
    """
    try:
        image_bytes = await image_file.read()
        return deps.get_dashboard_service().analyze_remote_image(
            image_bytes=image_bytes,
            filename=image_file.filename or "dashboard-image",
            content_type=image_file.content_type or "application/octet-stream",
            user_text=user_text,
            vision_mode=vision_mode,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/vision/respond", response_model=AssistantResponse)
async def dashboard_vision_respond(
    image_file: UploadFile = File(...),
    user_text: str = Form(...),
    vision_mode: str | None = Form(default="auto"),
    session_id: str | None = Form(default=None),
    synthesize_speech: bool = Form(default=False),
) -> AssistantResponse:
    """Generate an AI response to an image via vision service.
    
    Args:
        image_file: Image file to respond to.
        user_text: User text prompt.
        vision_mode: Vision mode (default "auto").
        session_id: Optional session ID.
        synthesize_speech: Whether to synthesize speech.
    
    Returns:
        AssistantResponse: AI response with audio/speech info.
    """
    try:
        image_bytes = await image_file.read()
        return deps.get_dashboard_service().respond_remote_image(
            image_bytes=image_bytes,
            filename=image_file.filename or "dashboard-image",
            content_type=image_file.content_type or "application/octet-stream",
            user_text=user_text,
            vision_mode=vision_mode,
            session_id=session_id,
            synthesize_speech=synthesize_speech,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
