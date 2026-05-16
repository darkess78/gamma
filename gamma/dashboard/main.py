from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .service import DashboardService
from ..config import settings as _app_settings
from .auth import auth_config, dashboard_auth_ready, is_authenticated, session_cookie_value, verify_login, websocket_is_authenticated
from ..schemas.response import AssistantResponse, VisionAnalysis
from ..schemas.voice import VoiceRoundtripResponse
from ..system.lazy_singleton import LazySingleton
from ..voice.live import LiveVoiceSession
from ..voice.roundtrip import VoiceRoundtripService

app = FastAPI(title="Gamma Dashboard")
service = LazySingleton[DashboardService]()
voice_roundtrip_service = LazySingleton[VoiceRoundtripService]()
live_voice_session = LazySingleton[LiveVoiceSession]()
STATIC_DIR = Path(__file__).resolve().parent / "static"

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="dashboard-static")


def get_dashboard_service() -> DashboardService:
    return service.get(DashboardService)


def get_voice_roundtrip_service() -> VoiceRoundtripService:
    return voice_roundtrip_service.get(VoiceRoundtripService)


def get_live_voice_session() -> LiveVoiceSession:
    def _build_live_voice_session() -> LiveVoiceSession:
        dashboard_service = get_dashboard_service()
        return LiveVoiceSession(
            job_starter=dashboard_service.start_remote_live_job,
            job_fetcher=dashboard_service.get_remote_live_job,
            job_canceler=dashboard_service.cancel_remote_live_job,
            partial_transcriber=dashboard_service.transcribe_remote_live_audio,
            idle_settings_provider=dashboard_service.live_idle_settings,
            idle_event_recorder=dashboard_service.record_remote_stream_event,
        )

    return live_voice_session.get(_build_live_voice_session)


@app.middleware("http")
async def require_dashboard_auth(request: Request, call_next):
    path = request.url.path
    if path in {"/health", "/login", "/logout"} or path.startswith("/static/"):
        return await call_next(request)
    if is_authenticated(request):
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"detail": "authentication required"}, status_code=401)
    return RedirectResponse(url="/login", status_code=303)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/login", response_class=HTMLResponse)
def login_page() -> str:
    config = auth_config()
    if not config.enabled:
        return """<!doctype html><html lang="en"><body style="font-family: Georgia, serif; padding: 40px;"><h1>Dashboard Auth Disabled</h1><p>Set SHANA_DASHBOARD_AUTH_ENABLED=true to enable login.</p><p><a href="/">Open dashboard</a></p></body></html>"""
    ready = dashboard_auth_ready()
    status = "" if ready else "<p style='color:#b91c1c;'>Dashboard auth is enabled but credentials are not fully configured.</p>"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Gamma Login</title>
  <style>
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: linear-gradient(145deg, #0e1218 0%, #111720 45%, #16202d 100%);
      color: #ecf2f8;
      font-family: Georgia, "Times New Roman", serif;
    }}
    .card {{
      width: min(420px, 92vw);
      padding: 28px;
      border-radius: 20px;
      background: rgba(19, 26, 35, 0.92);
      border: 1px solid rgba(182, 206, 230, 0.1);
      box-shadow: 0 18px 40px rgba(0, 0, 0, 0.32);
    }}
    label {{
      display: block;
      margin: 14px 0 6px;
      color: #9aa8b7;
      font-size: 14px;
    }}
    input {{
      width: 100%;
      box-sizing: border-box;
      padding: 12px 14px;
      border-radius: 12px;
      border: 1px solid rgba(182, 206, 230, 0.1);
      background: rgba(24, 32, 43, 0.92);
      color: #ecf2f8;
      font: inherit;
    }}
    button {{
      margin-top: 18px;
      width: 100%;
      padding: 12px 16px;
      border: 0;
      border-radius: 999px;
      background: #33c3b3;
      color: white;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
    }}
  </style>
</head>
<body>
  <form class="card" method="post" action="/login">
    <h1>Gamma Dashboard Login</h1>
    <p>Sign in before using the dashboard.</p>
    {status}
    <label for="username">Username</label>
    <input id="username" name="username" autocomplete="username" required>
    <label for="password">Password</label>
    <input id="password" name="password" type="password" autocomplete="current-password" required>
    <button type="submit">Sign In</button>
  </form>
</body>
</html>"""


@app.post("/login", response_model=None)
async def login_submit(request: Request):
    form = await request.form()
    username = str(form.get("username", ""))
    password = str(form.get("password", ""))
    if not dashboard_auth_ready() or not verify_login(username, password):
        return HTMLResponse(login_page().replace("</form>", "<p style='color:#fda4af; margin-top: 14px;'>Login failed.</p></form>"), status_code=401)
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        "gamma_dashboard_session",
        session_cookie_value(username),
        httponly=True,
        samesite="lax",
        secure=auth_config().cookie_secure,
        path="/",
    )
    return response


@app.get("/logout")
def logout() -> RedirectResponse:
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("gamma_dashboard_session", path="/")
    return response


@app.get("/api/status")
def status() -> dict:
    return get_dashboard_service().build_status()


@app.get("/api/status/runtime")
def runtime_status() -> dict:
    return get_dashboard_service().build_runtime_status()


@app.post("/api/client-log")
async def client_log(request: Request) -> dict[str, bool]:
    payload = await request.json()
    if isinstance(payload, dict):
        get_dashboard_service().append_client_log(payload)
    return {"ok": True}


@app.post("/api/shana/start")
def start_shana() -> dict:
    return get_dashboard_service().start_shana()


@app.post("/api/shana/stop")
def stop_shana() -> dict:
    return get_dashboard_service().stop_shana()


@app.post("/api/shana/restart")
def restart_shana() -> dict:
    return get_dashboard_service().restart_shana()


@app.post("/api/dashboard/stop")
def stop_dashboard() -> dict:
    return get_dashboard_service().stop_dashboard()


@app.post("/api/all/stop")
def stop_all() -> dict:
    return get_dashboard_service().stop_all()


@app.post("/api/memory/clear")
def clear_memory() -> dict:
    return get_dashboard_service().clear_memory()


@app.post("/api/memory/clear-recent")
async def clear_recent_memory(request: Request) -> dict:
    payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    minutes = int(payload.get("minutes", 10) or 10) if isinstance(payload, dict) else 10
    return get_dashboard_service().clear_recent_memory(minutes=minutes)


@app.post("/api/memory/clear-selected")
async def clear_selected_memory(request: Request) -> dict:
    payload = await request.json()
    selections = payload.get("items", []) if isinstance(payload, dict) else []
    return get_dashboard_service().clear_selected_memory(selections if isinstance(selections, list) else [])


@app.post("/api/providers/tts/start")
def start_tts() -> dict:
    return get_dashboard_service().start_tts()


@app.post("/api/providers/tts/stop")
def stop_tts() -> dict:
    return get_dashboard_service().stop_tts()


@app.post("/api/providers/stt/test")
def test_stt() -> dict:
    return get_dashboard_service().test_stt()


@app.post("/api/providers/tts/test")
def test_tts() -> dict:
    return get_dashboard_service().test_tts()


@app.post("/api/providers/tts/synthesize")
async def tts_synthesize_file(
    text_file: UploadFile = File(...),
) -> dict:
    raw = await text_file.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="file must be UTF-8 encoded text")
    text = text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="file is empty")
    result = get_dashboard_service().synthesize_text(text)
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result.get("detail", "synthesis failed"))
    return result


@app.post("/api/providers/tts/select")
async def select_tts_provider(request: Request) -> dict:
    payload = await request.json()
    provider = str(payload.get("provider", "")).strip()
    if not provider:
        raise HTTPException(status_code=400, detail="provider is required")
    try:
        return get_dashboard_service().set_tts_provider(provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/providers/tts/profile")
async def select_tts_profile(request: Request) -> dict:
    payload = await request.json()
    profile = str(payload.get("profile", "")).strip()
    try:
        return get_dashboard_service().set_tts_profile(profile)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/providers/tts/profile/save")
async def save_tts_profile(request: Request) -> dict:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="object payload is required")
    try:
        return get_dashboard_service().save_tts_profile(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/audio/{filename}")
def serve_audio(filename: str) -> FileResponse:
    safe_name = Path(filename).name
    audio_path = _app_settings.audio_output_dir / safe_name
    if not audio_path.exists() or not audio_path.is_file():
        raise HTTPException(status_code=404, detail="audio file not found")
    if audio_path.resolve().parent != _app_settings.audio_output_dir.resolve():
        raise HTTPException(status_code=400, detail="invalid path")
    media_type = mimetypes.guess_type(str(audio_path))[0] or "application/octet-stream"
    return FileResponse(str(audio_path), media_type=media_type)


@app.delete("/api/audio/{filename}")
def delete_audio(filename: str) -> dict:
    safe_name = Path(filename).name
    audio_path = _app_settings.audio_output_dir / safe_name
    if not audio_path.exists() or not audio_path.is_file():
        raise HTTPException(status_code=404, detail="audio file not found")
    if audio_path.resolve().parent != _app_settings.audio_output_dir.resolve():
        raise HTTPException(status_code=400, detail="invalid path")
    audio_path.unlink()
    return {"ok": True, "deleted": safe_name}


@app.post("/api/providers/llm/test")
def test_llm() -> dict:
    return get_dashboard_service().test_llm()


@app.post("/api/providers/voice/test")
def test_voice_roundtrip() -> dict:
    return get_dashboard_service().test_voice_roundtrip()


@app.get("/api/assistant/settings")
def assistant_settings() -> dict:
    return {"settings": get_dashboard_service().assistant_runtime_settings()}


@app.post("/api/assistant/settings")
async def save_assistant_settings(request: Request) -> dict:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="object payload is required")
    try:
        return get_dashboard_service().save_assistant_runtime_settings(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/voice/roundtrip", response_model=VoiceRoundtripResponse)
async def dashboard_voice_roundtrip(
    audio_file: UploadFile = File(...),
    session_id: str | None = Form(default=None),
    synthesize_speech: bool = Form(default=True),
) -> VoiceRoundtripResponse:
    return await get_voice_roundtrip_service().run(
        audio_file=audio_file,
        session_id=session_id,
        synthesize_speech=synthesize_speech,
    )


@app.get("/api/voice/live/history")
def dashboard_live_voice_history(limit: int = 20) -> dict:
    return get_dashboard_service().remote_live_history(limit=limit)


@app.get("/api/stream/traces/recent")
def dashboard_stream_recent_traces(limit: int = 50) -> dict:
    return get_dashboard_service().stream_recent_traces(limit=limit)


@app.get("/api/stream/eval/recent")
def dashboard_stream_recent_eval(limit: int = 50) -> dict:
    return get_dashboard_service().stream_recent_eval(limit=limit)


@app.get("/api/stream/outputs/recent")
def dashboard_stream_recent_outputs(limit: int = 50) -> dict:
    return get_dashboard_service().stream_recent_outputs(limit=limit)


@app.post("/api/vision/analyze", response_model=VisionAnalysis)
async def dashboard_vision_analyze(
    image_file: UploadFile = File(...),
    user_text: str = Form(...),
    vision_mode: str | None = Form(default="auto"),
) -> VisionAnalysis:
    try:
        image_bytes = await image_file.read()
        return get_dashboard_service().analyze_remote_image(
            image_bytes=image_bytes,
            filename=image_file.filename or "dashboard-image",
            content_type=image_file.content_type or "application/octet-stream",
            user_text=user_text,
            vision_mode=vision_mode,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/vision/respond", response_model=AssistantResponse)
async def dashboard_vision_respond(
    image_file: UploadFile = File(...),
    user_text: str = Form(...),
    vision_mode: str | None = Form(default="auto"),
    session_id: str | None = Form(default=None),
    synthesize_speech: bool = Form(default=False),
) -> AssistantResponse:
    try:
        image_bytes = await image_file.read()
        return get_dashboard_service().respond_remote_image(
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


@app.websocket("/api/voice/live")
async def dashboard_live_voice(websocket: WebSocket) -> None:
    if not websocket_is_authenticated(websocket):
        await websocket.close(code=4401, reason="authentication required")
        return
    try:
        await get_live_voice_session().handle(websocket)
    except WebSocketDisconnect:
        return
