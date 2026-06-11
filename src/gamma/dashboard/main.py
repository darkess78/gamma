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
INDEX_PAGE = STATIC_DIR / "index.html"
MONITOR_PAGE = STATIC_DIR / "monitor.html"
SUBTITLE_OVERLAY_PAGE = STATIC_DIR / "overlay.html"

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="dashboard-static")


def get_dashboard_service() -> DashboardService:
    """Get DashboardService singleton instance.

    Returns:
        DashboardService: Dashboard service instance.
    """
    return service.get(DashboardService)


def get_voice_roundtrip_service() -> VoiceRoundtripService:
    """Get VoiceRoundtripService singleton instance.

    Returns:
        VoiceRoundtripService: Voice roundtrip service instance.
    """
    return voice_roundtrip_service.get(VoiceRoundtripService)


def get_live_voice_session() -> LiveVoiceSession:
    """Get LiveVoiceSession singleton instance.

    Returns:
        LiveVoiceSession: Live voice session instance.
    """
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
    """Require dashboard authentication for all routes except static/auth/health.

    Skips auth for:
        /health
        /login
        /logout
        /static/*

    For /api/* paths, returns 401 if not authenticated.
    """
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
    """Health check endpoint.

    Returns:
        dict: {"status": "ok"}
    """
    return {"status": "ok"}


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    """Return dashboard favicon.

    Returns:
        FileResponse: SVG favicon from static directory.
    """
    return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")


@app.get("/")
@app.get("/dashboard")
def dashboard() -> HTMLResponse:
    """Return main dashboard page.

    Returns:
        HTMLResponse: Dashboard index HTML with GAMMA_SHANA_BASE_URL injected.
    """
    return _dashboard_page(INDEX_PAGE, dashboard_page="dashboard")


@app.get("/dashboard/live")
def dashboard_live_page() -> HTMLResponse:
    """Return live dashboard page.

    Returns:
        HTMLResponse: Live view HTML with base URLs injected.
    """
    return _dashboard_page(INDEX_PAGE, dashboard_page="live")


@app.get("/dashboard/status")
def dashboard_status_page() -> HTMLResponse:
    """Return dashboard status page.

    Returns:
        HTMLResponse: Status view HTML with base URLs injected.
    """
    return _dashboard_page(INDEX_PAGE, dashboard_page="status")


@app.get("/dashboard/stream")
def dashboard_stream_page() -> HTMLResponse:
    """Return stream dashboard page.

    Returns:
        HTMLResponse: Stream view HTML with base URLs injected.
    """
    return _dashboard_page(INDEX_PAGE, dashboard_page="stream")


@app.get("/dashboard/twitch")
def dashboard_twitch_page() -> RedirectResponse:
    """Redirect Twitch dashboard URL to stream page.

    Returns:
        RedirectResponse: 307 redirect to /dashboard/stream.
    """
    return RedirectResponse(url="/dashboard/stream", status_code=307)


@app.get("/dashboard/memory")
def dashboard_memory_page() -> HTMLResponse:
    """Return memory dashboard page.

    Returns:
        HTMLResponse: Memory view HTML with base URLs injected.
    """
    return _dashboard_page(INDEX_PAGE, dashboard_page="memory")


@app.get("/dashboard/settings")
def dashboard_settings_page() -> HTMLResponse:
    """Return settings dashboard page.

    Returns:
        HTMLResponse: Settings view HTML with base URLs injected.
    """
    return _dashboard_page(INDEX_PAGE, dashboard_page="settings")


@app.get("/monitor")
def monitor_page() -> RedirectResponse:
    """Redirect monitor URL to dashboard monitor page.
    
    Returns:
        RedirectResponse: 307 redirect to /dashboard/monitor.
    """
    return RedirectResponse(url="/dashboard/monitor", status_code=307)


@app.get("/dashboard/monitor")
def dashboard_monitor_page() -> HTMLResponse:
    """Return monitor dashboard page.
    
    Returns:
        HTMLResponse: Monitor view HTML with base URLs injected.
    """
    return _dashboard_output_page(MONITOR_PAGE, dashboard_page="monitor")


@app.get("/performer")
def performer_redirect() -> RedirectResponse:
    """Redirect performer URL to Shana performer endpoint.
    
    Returns:
        RedirectResponse: 307 redirect to Shana base URL + /performer.
    """
    return RedirectResponse(url=f"{_app_settings.shana_base_url}/performer", status_code=307)


@app.get("/overlay/subtitles")
def subtitle_overlay_page() -> HTMLResponse:
    """Return subtitle overlay dashboard page.
    
    Returns:
        HTMLResponse: Subtitle overlay HTML with base URLs injected.
    """
    return _dashboard_output_page(SUBTITLE_OVERLAY_PAGE)


def _dashboard_output_page(path: Path, *, dashboard_page: str = "") -> HTMLResponse:
    return _dashboard_page(path, dashboard_page=dashboard_page)


def _dashboard_page(path: Path, *, dashboard_page: str = "") -> HTMLResponse:
    """Return dashboard page with injected configuration.
    
    Args:
        path: HTML file path.
        dashboard_page: Dashboard page name for URL injection.
    
    Returns:
        HTMLResponse: Page with configuration injected.
    """
    html = path.read_text(encoding="utf-8")
    html = _with_dashboard_public_links(html)
    config = (
        f'<script>window.GAMMA_SHANA_BASE_URL = "{_app_settings.shana_base_url}";' 
        f' window.GAMMA_DASHBOARD_BASE_URL = "{_app_settings.dashboard_base_url}";' 
        f' window.GAMMA_DASHBOARD_PAGE = "{dashboard_page}";</script>'
    )
    html = html.replace("</head>", f"  {config}\n</head>", 1)
    return HTMLResponse(html)


def _with_dashboard_public_links(html: str) -> str:
    """Replace relative dashboard URLs with public base URLs.

    Args:
        html: HTML string to modify.

    Returns:
        str: HTML with public URLs injected.
    """
    dashboard_base = _app_settings.dashboard_base_url.rstrip("/")
    replacements = {
        'href="/dashboard': f'href="{dashboard_base}/dashboard',
        'href="/overlay/subtitles': f'href="{dashboard_base}/overlay/subtitles',
    }
    for old, new in replacements.items():
        html = html.replace(old, new)
    return html


@app.get("/login", response_class=HTMLResponse)
def login_page() -> str:
    """Return dashboard login page.
    
    Returns:
        str: HTML login form, or 'auth disabled' page if not enabled.
    """
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
    """Handle dashboard login submission.
    
    Args:
        request: HTTP request containing username and password form data.
    
    Returns:
        HTMLResponse: Re-rendered login page with error, or redirect to dashboard.
        Raises:
            HTTPException: 401 if login fails.
    """
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
    """Clear session cookie and redirect to login page.
    
    Returns:
        RedirectResponse: 303 redirect to /login with cookie deleted.
    """
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("gamma_dashboard_session", path="/")
    return response


@app.get("/api/status")
def status() -> dict:
    """Return dashboard service status.
    
    Returns:
        dict: Status information including version and services.
    """
    return get_dashboard_service().build_status()


@app.get("/api/status/runtime")
def runtime_status() -> dict:
    """Return dashboard runtime status.
    
    Returns:
        dict: Runtime status including services and workers.
    """
    return get_dashboard_service().build_runtime_status()


@app.post("/api/client-log")
async def client_log(request: Request) -> dict[str, bool]:
    """Append client-side log entry.
    
    Args:
        request: HTTP request with JSON payload containing log entry.
    
    Returns:
        dict[str, bool]: {"ok": True} on success.
    """
    payload = await request.json()
    if isinstance(payload, dict):
        get_dashboard_service().append_client_log(payload)
    return {"ok": True}


@app.post("/api/shana/start")
def start_shana() -> dict:
    """Start Shana application via WebSocket.
    
    Returns:
        dict: Confirmation response.
    """
    return get_dashboard_service().start_shana()


@app.post("/api/shana/stop")
def stop_shana() -> dict:
    """Stop Shana application via WebSocket.
    
    Returns:
        dict: Confirmation response.
    """
    return get_dashboard_service().stop_shana()


@app.post("/api/shana/restart")
def restart_shana() -> dict:
    """Restart Shana application via WebSocket.
    
    Returns:
        dict: Confirmation response.
    """
    return get_dashboard_service().restart_shana()


@app.post("/api/dashboard/stop")
def stop_dashboard() -> dict:
    """Stop dashboard application via WebSocket.
    
    Returns:
        dict: Confirmation response.
    """
    return get_dashboard_service().stop_dashboard()


@app.post("/api/all/stop")
def stop_all() -> dict:
    """Stop all services (Shana and dashboard) via WebSocket.
    
    Returns:
        dict: Confirmation response.
    """
    return get_dashboard_service().stop_all()


@app.get("/api/twitch/worker/status")
def twitch_worker_status() -> dict:
    """Return Twitch worker status.
    
    Returns:
        dict: Worker run state and error status.
    """
    return get_dashboard_service().twitch_worker_status()


@app.get("/api/twitch/settings")
def twitch_settings() -> dict:
    """Return current Twitch runtime settings.
    
    Returns:
        dict: Settings dict including oauth_token, eventsub, and trusted viewers.
    """
    return {"settings": get_dashboard_service().twitch_runtime_settings()}


@app.post("/api/twitch/settings")
async def save_twitch_settings(request: Request) -> dict:
    """Save new Twitch runtime settings.
    
    Args:
        request: HTTP request with JSON payload of settings.
    
    Returns:
        dict: Confirmation response.
    """
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="object payload is required")
    return get_dashboard_service().save_twitch_runtime_settings(payload)


@app.post("/api/twitch/worker/start")
def start_twitch_worker() -> dict:
    """Start Twitch worker.
    
    Returns:
        dict: Confirmation response.
    """
    return get_dashboard_service().start_twitch_worker()


@app.post("/api/twitch/worker/stop")
def stop_twitch_worker() -> dict:
    """Stop Twitch worker.
    
    Returns:
        dict: Confirmation response.
    """
    return get_dashboard_service().stop_twitch_worker()


@app.get("/api/twitch/eventsub/status")
def twitch_eventsub_status() -> dict:
    """Return Twitch EventSub worker status.
    
    Returns:
        dict: EventSub registration run state and error status.
    """
    return get_dashboard_service().twitch_eventsub_status()


@app.post("/api/twitch/eventsub/start")
def start_twitch_eventsub_worker() -> dict:
    """Start Twitch EventSub worker.
    
    Returns:
        dict: Confirmation response.
    """
    return get_dashboard_service().start_twitch_eventsub_worker()


@app.post("/api/twitch/eventsub/stop")
def stop_twitch_eventsub_worker() -> dict:
    """Stop Twitch EventSub worker.
    
    Returns:
        dict: Confirmation response.
    """
    return get_dashboard_service().stop_twitch_eventsub_worker()


@app.get("/api/twitch/viewer-trust")
def twitch_viewer_trust(limit: int = 100) -> dict:
    """Return list of Twitch viewer trust info.
    
    Args:
        limit: Optional max number of viewers to return (default 100).
    
    Returns:
        dict: List of viewer info including trusted/untrusted status.
    """
    return get_dashboard_service().twitch_viewer_trust(limit=limit)


@app.post("/api/twitch/viewer-trust")
async def save_twitch_viewer_trust(request: Request) -> dict:
    """Save a new viewer trust configuration.
    
    Args:
        request: HTTP request with JSON payload of viewer trust data.
    
    Returns:
        dict: Confirmation response.
        Raises:
            HTTPException: 400 if payload invalid.
    """
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="object payload is required")
    try:
        return get_dashboard_service().save_twitch_viewer_trust(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/twitch/replay")
async def run_twitch_replay(request: Request) -> dict:
    """Run a Twitch replay.
    
    Args:
        request: HTTP request with JSON replay payload.
    
    Returns:
        dict: Replay status results.
        Raises:
            HTTPException: 400 if payload invalid, 500 on failure.
    """
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="object payload is required")
    try:
        return get_dashboard_service().run_twitch_replay(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/twitch/replay/dry-run")
def run_twitch_dry_run_replay() -> dict:
    """Run a dry-run Twitch replay check.
    
    Returns:
        dict: Replay status results.
    """
    try:
        return get_dashboard_service().run_twitch_dry_run_replay()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/memory/clear")
def clear_memory() -> dict:
    """Clear all memory contents.
    
    Returns:
        dict: Confirmation response.
    """
    return get_dashboard_service().clear_memory()


@app.post("/api/memory/clear-recent")
async def clear_recent_memory(request: Request) -> dict:
    """Clear recent memory items.
    
    Args:
        request: HTTP request with JSON payload containing minutes to clear.
    
    Returns:
        dict: Confirmation of how many items were cleared.
    """
    payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    minutes = int(payload.get("minutes", 10) or 10) if isinstance(payload, dict) else 10
    return get_dashboard_service().clear_recent_memory(minutes=minutes)


@app.post("/api/memory/clear-selected")
async def clear_selected_memory(request: Request) -> dict:
    """Clear selected memory items.
    
    Args:
        request: HTTP request with JSON payload containing selections.
    
    Returns:
        dict: Confirmation of how many items were cleared.
    """
    payload = await request.json()
    selections = payload.get("items", []) if isinstance(payload, dict) else []
    return get_dashboard_service().clear_selected_memory(selections if isinstance(selections, list) else [])


@app.post("/api/memory/item")
async def update_memory_item(request: Request) -> dict:
    """Update a memory item.
    
    Args:
        request: HTTP request with JSON payload containing item data.
    
    Returns:
        dict: Updated memory item data.
        Raises:
            HTTPException: 400 if payload invalid or item not found.
    """
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="object payload is required")
    try:
        return get_dashboard_service().update_memory_item(payload)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/memory/item/create")
async def create_memory_item(request: Request) -> dict:
    """Create a new memory item.
    
    Args:
        request: HTTP request with JSON payload containing item data.
    
    Returns:
        dict: Created memory item data.
        Raises:
            HTTPException: 400 if payload invalid.
    """
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="object payload is required")
    try:
        return get_dashboard_service().create_memory_item(payload)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/memory/people")
async def save_known_person(request: Request) -> dict:
    """Save a known person.
    
    Args:
        request: HTTP request with JSON payload containing person data.
    
    Returns:
        dict: Confirmed known person data.
        Raises:
            HTTPException: 400 if payload invalid.
    """
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="object payload is required")
    try:
        return get_dashboard_service().save_known_person(payload)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/memory/people/{person_id}")
def delete_known_person(person_id: int) -> dict:
    """Delete a known person.
    
    Args:
        person_id: ID of known person to delete.
    
    Returns:
        dict: Confirmation response.
        Raises:
            HTTPException: 404 if person not found.
    """
    try:
        return get_dashboard_service().delete_known_person(person_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/providers/tts/start")
def start_tts() -> dict:
    """Start TTS service.
    
    Returns:
        dict: Confirmation response.
    """
    return get_dashboard_service().start_tts()


@app.post("/api/providers/tts/stop")
def stop_tts() -> dict:
    """Stop TTS service.
    
    Returns:
        dict: Confirmation response.
    """
    return get_dashboard_service().stop_tts()


@app.post("/api/providers/stt/test")
def test_stt() -> dict:
    """Test STT provider.
    
    Returns:
        dict: STT test results.
    """
    return get_dashboard_service().test_stt()


@app.post("/api/providers/tts/test")
def test_tts() -> dict:
    """Test TTS provider.
    
    Returns:
        dict: TTS test results.
    """
    return get_dashboard_service().test_tts()


@app.post("/api/providers/tts/synthesize")
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
    result = get_dashboard_service().synthesize_text(text)
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result.get("detail", "synthesis failed"))
    return result


@app.post("/api/providers/tts/select")
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
        return get_dashboard_service().set_tts_provider(provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/providers/tts/profile")
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
        return get_dashboard_service().set_tts_profile(profile)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/providers/tts/profile/save")
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
        return get_dashboard_service().save_tts_profile(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/audio/{filename}")
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


@app.delete("/api/audio/{filename}")
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


@app.post("/api/providers/llm/test")
def test_llm() -> dict:
    """Test LLM provider.
    
    Returns:
        dict: LLM test results.
    """
    return get_dashboard_service().test_llm()


@app.post("/api/providers/voice/test")
def test_voice_roundtrip() -> dict:
    """Test voice roundtrip.
    
    Returns:
        dict: Voice roundtrip test results.
    """
    return get_dashboard_service().test_voice_roundtrip()


@app.get("/api/assistant/settings")
def assistant_settings() -> dict:
    """Return assistant runtime settings.
    
    Returns:
        dict: Settings dict including targets and mute states.
    """
    return {"settings": get_dashboard_service().assistant_runtime_settings()}


@app.post("/api/assistant/settings")
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
        return get_dashboard_service().save_assistant_runtime_settings(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/voice/roundtrip", response_model=VoiceRoundtripResponse)
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
    return await get_voice_roundtrip_service().run(
        audio_file=audio_file,
        session_id=session_id,
        synthesize_speech=synthesize_speech,
    )


@app.get("/api/voice/live/history")
def dashboard_live_voice_history(limit: int = 20) -> dict:
    """Get live voice history.
    
    Args:
        limit: Optional max history items (default 20).
    
    Returns:
        dict: Live voice history records.
    """
    return get_dashboard_service().remote_live_history(limit=limit)


@app.get("/api/stream/traces/recent")
def dashboard_stream_recent_traces(limit: int = 50) -> dict:
    """Get recent stream traces.
    
    Args:
        limit: Optional max traces (default 50).
    
    Returns:
        dict: Recent stream trace data.
    """
    return get_dashboard_service().stream_recent_traces(limit=limit)


@app.get("/api/stream/eval/recent")
def dashboard_stream_recent_eval(limit: int = 50) -> dict:
    """Get recent stream evaluations.
    
    Args:
        limit: Optional max evaluations (default 50).
    
    Returns:
        dict: Recent stream eval data.
    """
    return get_dashboard_service().stream_recent_eval(limit=limit)


@app.get("/api/stream/outputs/recent")
def dashboard_stream_recent_outputs(limit: int = 50) -> dict:
    """Get recent stream outputs.
    
    Args:
        limit: Optional max outputs (default 50).
    
    Returns:
        dict: Recent stream output data.
    """
    return get_dashboard_service().stream_recent_outputs(limit=limit)


@app.get("/api/stream/queue")
def dashboard_stream_pending_queue() -> dict:
    """Get pending stream queue.
    
    Returns:
        dict: Pending stream queue data.
    """
    return get_dashboard_service().stream_pending_queue()


@app.get("/api/stream/temp-memory")
def dashboard_stream_temp_memory(bucket: str | None = None, limit: int = 100) -> dict:
    """Get temp memory bucket entries.
    
    Args:
        bucket: Optional bucket name (default None for all).
        limit: Optional max items (default 100).
    
    Returns:
        dict: Temp memory entries.
    """
    return get_dashboard_service().stream_temp_memory(bucket=bucket, limit=limit)


@app.delete("/api/stream/temp-memory")
def dashboard_stream_temp_memory_clear(bucket: str | None = None) -> dict:
    """Clear temp memory bucket.
    
    Args:
        bucket: Optional bucket name to clear (default None for all).
    
    Returns:
        dict: Confirmation response.
    """
    return get_dashboard_service().clear_stream_temp_memory(bucket=bucket)


@app.get("/api/stream/self-goals")
def dashboard_stream_self_goals(status: str | None = None, limit: int = 100) -> dict:
    """Get stream self-goals.
    
    Args:
        status: Optional status filter (default None for all).
        limit: Optional max goals (default 100).
    
    Returns:
        dict: Stream self-goals data.
    """
    return get_dashboard_service().stream_self_goals(status=status, limit=limit)


@app.post("/api/stream/self-goals/{goal_id}/approve")
def dashboard_stream_self_goal_approve(goal_id: int) -> dict:
    """Approve a stream self-goal.
    
    Args:
        goal_id: Self-goal ID to approve.
    
    Returns:
        dict: Confirmation response.
    """
    return get_dashboard_service().set_stream_self_goal_status(goal_id, status="approve")


@app.post("/api/stream/self-goals/{goal_id}/reject")
def dashboard_stream_self_goal_reject(goal_id: int) -> dict:
    """Reject a stream self-goal.
    
    Args:
        goal_id: Self-goal ID to reject.
    
    Returns:
        dict: Confirmation response.
    """
    return get_dashboard_service().set_stream_self_goal_status(goal_id, status="reject")


@app.post("/api/stream/self-goals/clear")
def dashboard_stream_self_goals_clear() -> dict:
    """Clear all stream self-goals.
    
    Returns:
        dict: Confirmation response.
    """
    return get_dashboard_service().clear_stream_self_goals()


@app.post("/api/stream/stop")
def dashboard_stream_stop() -> dict:
    """Stop stream speech with reason 'dashboard_stop'.
    
    Returns:
        dict: Confirmation response.
    """
    return get_dashboard_service().stop_stream_speech(reason="dashboard_stop")


@app.post("/api/performer/targets/{target_policy}/mute")
def dashboard_performer_target_mute(target_policy: str) -> dict:
    """Mute a performer target policy.
    
    Args:
        target_policy: Policy name to mute (all, voice, memory).
    
    Returns:
        dict: Confirmation response.
    """
    return get_dashboard_service().set_performer_target_mute(target_policy, muted=True, reason="dashboard")


@app.post("/api/performer/targets/{target_policy}/unmute")
def dashboard_performer_target_unmute(target_policy: str) -> dict:
    """Unmute a performer target policy.
    
    Args:
        target_policy: Policy name to unmute (all, voice, memory).
    
    Returns:
        dict: Confirmation response.
    """
    return get_dashboard_service().set_performer_target_mute(target_policy, muted=False, reason="dashboard")


@app.post("/api/performer/targets/{target_policy}/clear")
def dashboard_performer_target_clear(target_policy: str) -> dict:
    """Clear a performer target policy.
    
    Args:
        target_policy: Policy name to clear (all, voice, memory).
    
    Returns:
        dict: Confirmation response.
    """
    return get_dashboard_service().clear_performer_target(target_policy, reason="dashboard")


@app.post("/api/vision/analyze", response_model=VisionAnalysis)
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
    """Handle live voice WebSocket connection.
    
    Args:
        websocket: WebSocket connection for live voice.
    """
    if not websocket_is_authenticated(websocket):
        await websocket.close(code=4401, reason="authentication required")
        return
    try:
        await get_live_voice_session().handle(websocket)
    except WebSocketDisconnect:
        return
