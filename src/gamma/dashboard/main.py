from __future__ import annotations

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from ..observability import configure_logging, install_request_logging
from . import auth, deps
from .auth import auth_config, dashboard_auth_ready, is_authenticated, session_cookie_value, verify_login
from .coding_routes import router as coding_router
from .pages import STATIC_DIR, router as pages_router
from .routes_control import router as control_router
from .routes_integrations import router as integrations_router
from .routes_memory import router as memory_router
from .routes_stream import router as stream_router
from .routes_voice import router as voice_router
from .shana_client import ShanaClientError

LOGIN_PAGE = STATIC_DIR / "login.html"

app = FastAPI(title="Gamma Dashboard")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="dashboard-static")


@app.exception_handler(ShanaClientError)
async def shana_client_error_handler(_request: Request, exc: ShanaClientError) -> JSONResponse:
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


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


install_request_logging(app, service="dashboard", logger=configure_logging("dashboard"))


@app.get("/health")
def health() -> dict[str, str]:
    """Health check endpoint.

    Returns:
        dict: {"status": "ok"}
    """
    return {"status": "ok"}


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
    return LOGIN_PAGE.read_text(encoding="utf-8").replace("<!--STATUS-->", status)


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
    response = RedirectResponse(url="/dashboard", status_code=303)
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


@app.websocket("/api/voice/live")
async def dashboard_live_voice(websocket: WebSocket) -> None:
    """Handle live voice WebSocket connection.
    
    Args:
        websocket: WebSocket connection for live voice.
    """
    if not auth.websocket_is_authenticated(websocket):
        await websocket.close(code=4401, reason="authentication required")
        return
    try:
        await deps.get_live_voice_session().handle(websocket)
    except WebSocketDisconnect:
        return


for _router in (pages_router, control_router, integrations_router, memory_router,
                voice_router, stream_router, coding_router):
    app.include_router(_router)
