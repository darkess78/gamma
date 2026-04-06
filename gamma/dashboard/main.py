from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .service import DashboardService
from .auth import auth_config, dashboard_auth_ready, is_authenticated, session_cookie_value, verify_login, websocket_is_authenticated
from ..schemas.response import AssistantResponse, VisionAnalysis
from ..schemas.voice import VoiceRoundtripResponse
from ..voice.live import LiveVoiceSession
from ..voice.roundtrip import VoiceRoundtripService

app = FastAPI(title="Gamma Dashboard")
service = DashboardService()
voice_roundtrip_service = VoiceRoundtripService()
live_voice_session = LiveVoiceSession(
    job_starter=service.start_remote_live_job,
    job_fetcher=service.get_remote_live_job,
    job_canceler=service.cancel_remote_live_job,
    partial_transcriber=service.transcribe_remote_live_audio,
)
STATIC_DIR = Path(__file__).resolve().parent / "static"

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="dashboard-static")


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
    return service.build_status()


@app.post("/api/client-log")
async def client_log(request: Request) -> dict[str, bool]:
    payload = await request.json()
    if isinstance(payload, dict):
        service.append_client_log(payload)
    return {"ok": True}


@app.post("/api/shana/start")
def start_shana() -> dict:
    return service.start_shana()


@app.post("/api/shana/stop")
def stop_shana() -> dict:
    return service.stop_shana()


@app.post("/api/shana/restart")
def restart_shana() -> dict:
    return service.restart_shana()


@app.post("/api/dashboard/stop")
def stop_dashboard() -> dict:
    return service.stop_dashboard()


@app.post("/api/all/stop")
def stop_all() -> dict:
    return service.stop_all()


@app.post("/api/providers/tts/start")
def start_tts() -> dict:
    return service.start_tts()


@app.post("/api/providers/tts/stop")
def stop_tts() -> dict:
    return service.stop_tts()


@app.post("/api/providers/stt/test")
def test_stt() -> dict:
    return service.test_stt()


@app.post("/api/providers/tts/test")
def test_tts() -> dict:
    return service.test_tts()


@app.post("/api/providers/llm/test")
def test_llm() -> dict:
    return service.test_llm()


@app.post("/api/providers/voice/test")
def test_voice_roundtrip() -> dict:
    return service.test_voice_roundtrip()


@app.post("/api/voice/roundtrip", response_model=VoiceRoundtripResponse)
async def dashboard_voice_roundtrip(
    audio_file: UploadFile = File(...),
    session_id: str | None = Form(default=None),
    synthesize_speech: bool = Form(default=True),
) -> VoiceRoundtripResponse:
    return await voice_roundtrip_service.run(
        audio_file=audio_file,
        session_id=session_id,
        synthesize_speech=synthesize_speech,
    )


@app.post("/api/vision/analyze", response_model=VisionAnalysis)
async def dashboard_vision_analyze(
    image_file: UploadFile = File(...),
    user_text: str = Form(...),
    vision_mode: str | None = Form(default="auto"),
) -> VisionAnalysis:
    try:
        image_bytes = await image_file.read()
        return service.analyze_remote_image(
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
        return service.respond_remote_image(
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
        await live_voice_session.handle(websocket)
    except WebSocketDisconnect:
        return
