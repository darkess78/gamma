"""Dashboard HTML pages and redirects, with public URLs injected for the browser."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter
from fastapi import Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from ..config import settings as _app_settings


router = APIRouter()

STATIC_DIR = Path(__file__).resolve().parent / "static"


INDEX_PAGE = STATIC_DIR / "index.html"


MONITOR_PAGE = STATIC_DIR / "monitor.html"


SUBTITLE_OVERLAY_PAGE = STATIC_DIR / "overlay.html"


LIVE_VOICE_PANEL = STATIC_DIR / "live_voice_panel.html"


@router.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    """Return dashboard favicon.

    Returns:
        FileResponse: SVG favicon from static directory.
    """
    return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")


@router.get("/")
@router.get("/dashboard")
def dashboard(request: Request = None) -> HTMLResponse:
    """Return main dashboard page.

    Returns:
        HTMLResponse: Dashboard index HTML with GAMMA_SHANA_BASE_URL injected.
    """
    return _dashboard_page(INDEX_PAGE, dashboard_page="dashboard", request=request)


@router.get("/dashboard/live")
def dashboard_live_page(request: Request = None) -> HTMLResponse:
    """Return live dashboard page.

    Returns:
        HTMLResponse: Live view HTML with base URLs injected.
    """
    return _dashboard_page(INDEX_PAGE, dashboard_page="live", request=request)


@router.get("/dashboard/talk")
def dashboard_talk_page() -> RedirectResponse:
    """Redirect the retired Talk client to the persistent Monitor room."""
    return RedirectResponse(url="/dashboard/monitor", status_code=307)


@router.get("/talk")
def talk_page_redirect() -> RedirectResponse:
    """Redirect the retired short Talk URL to Monitor."""
    return RedirectResponse(url="/dashboard/monitor", status_code=307)


@router.get("/dashboard/status")
def dashboard_status_page(request: Request = None) -> HTMLResponse:
    """Return dashboard status page.

    Returns:
        HTMLResponse: Status view HTML with base URLs injected.
    """
    return _dashboard_page(INDEX_PAGE, dashboard_page="status", request=request)


@router.get("/dashboard/presence")
def dashboard_presence_page(request: Request = None) -> HTMLResponse:
    """Return dashboard presence page."""
    return _dashboard_page(INDEX_PAGE, dashboard_page="presence", request=request)


@router.get("/presence")
def presence_page_redirect() -> RedirectResponse:
    """Redirect stale short Presence links to the dashboard route."""
    return RedirectResponse(url="/dashboard/presence", status_code=307)


@router.get("/dashboard/stream")
def dashboard_stream_page(request: Request = None) -> HTMLResponse:
    """Return stream dashboard page.

    Returns:
        HTMLResponse: Stream view HTML with base URLs injected.
    """
    return _dashboard_page(INDEX_PAGE, dashboard_page="stream", request=request)


@router.get("/dashboard/twitch")
def dashboard_twitch_page() -> RedirectResponse:
    """Redirect Twitch dashboard URL to stream page.

    Returns:
        RedirectResponse: 307 redirect to /dashboard/stream.
    """
    return RedirectResponse(url="/dashboard/stream", status_code=307)


@router.get("/dashboard/memory")
def dashboard_memory_page(request: Request = None) -> HTMLResponse:
    """Return memory dashboard page.

    Returns:
        HTMLResponse: Memory view HTML with base URLs injected.
    """
    return _dashboard_page(INDEX_PAGE, dashboard_page="memory", request=request)


@router.get("/dashboard/improvement")
def dashboard_improvement_page(request: Request = None) -> HTMLResponse:
    """Return the owner-reviewed coding page."""
    return _dashboard_page(INDEX_PAGE, dashboard_page="improvement", request=request)


@router.get("/dashboard/settings")
def dashboard_settings_page(request: Request = None) -> HTMLResponse:
    """Return settings dashboard page.

    Returns:
        HTMLResponse: Settings view HTML with base URLs injected.
    """
    return _dashboard_page(INDEX_PAGE, dashboard_page="settings", request=request)


@router.get("/monitor")
def monitor_page() -> RedirectResponse:
    """Redirect monitor URL to dashboard monitor page.
    
    Returns:
        RedirectResponse: 307 redirect to /dashboard/monitor.
    """
    return RedirectResponse(url="/dashboard/monitor", status_code=307)


@router.get("/dashboard/monitor")
def dashboard_monitor_page(request: Request = None) -> HTMLResponse:
    """Return monitor dashboard page.
    
    Returns:
        HTMLResponse: Monitor view HTML with base URLs injected.
    """
    return _dashboard_output_page(MONITOR_PAGE, dashboard_page="monitor", request=request)


@router.get("/performer")
def performer_redirect() -> RedirectResponse:
    """Redirect performer URL to Shana performer endpoint.
    
    Returns:
        RedirectResponse: 307 redirect to Shana base URL + /performer.
    """
    return RedirectResponse(url=f"{_app_settings.shana_base_url}/performer", status_code=307)


@router.get("/overlay/subtitles")
def subtitle_overlay_page(request: Request = None) -> HTMLResponse:
    """Return subtitle overlay dashboard page.
    
    Returns:
        HTMLResponse: Subtitle overlay HTML with base URLs injected.
    """
    return _dashboard_output_page(SUBTITLE_OVERLAY_PAGE, request=request)


def _dashboard_output_page(path: Path, *, dashboard_page: str = "", request: Request = None) -> HTMLResponse:
    return _dashboard_page(path, dashboard_page=dashboard_page, request=request)


def _dashboard_page(path: Path, *, dashboard_page: str = "", request: Request = None) -> HTMLResponse:
    """Return dashboard page with injected configuration.
    
    Args:
        path: HTML file path.
        dashboard_page: Dashboard page name for URL injection.
    
    Returns:
        HTMLResponse: Page with configuration injected.
    """
    html = path.read_text(encoding="utf-8")
    html = _with_dashboard_public_links(html)
    html = _with_dashboard_partials(html)
    shana_base_url = _request_public_base(_app_settings.shana_base_url, request=request)
    dashboard_base_url = _request_public_base(_app_settings.dashboard_base_url, request=request)
    config = (
        f'<script>window.GAMMA_SHANA_BASE_URL = "{shana_base_url}";'
        f' window.GAMMA_DASHBOARD_BASE_URL = "{dashboard_base_url}";'
        f' window.GAMMA_DASHBOARD_PAGE = "{dashboard_page}";</script>'
    )
    html = html.replace("</head>", f"  {config}\n</head>", 1)
    return HTMLResponse(html)


def _with_dashboard_partials(html: str) -> str:
    """Replace shared dashboard HTML partial markers."""
    if "<!-- GAMMA_LIVE_VOICE_PANEL -->" in html:
        html = html.replace("<!-- GAMMA_LIVE_VOICE_PANEL -->", LIVE_VOICE_PANEL.read_text(encoding="utf-8"))
    return html


def _with_dashboard_public_links(html: str) -> str:
    """Replace overlay/subtitles URLs with public base URLs.

    The navbar and dashboard navigation links use same-origin relative paths
    to ensure they work correctly in all deployment configurations.

    Args:
        html: HTML string to modify.

    Returns:
        str: HTML with public URLs injected for overlay/subtitles only.
    """
    dashboard_base = _app_settings.dashboard_base_url.rstrip("/")
    # Only replace overlay/subtitles (external output view), not navbar links
    html = html.replace('href="/overlay/subtitles', f'href="{dashboard_base}/overlay/subtitles')
    return html


def _request_public_base(configured_base: str, *, request: Request = None) -> str:
    configured = str(configured_base or "").rstrip("/")
    if request is None:
        return configured
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").strip()
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "").split(",", 1)[0].strip().lower()
    if not host or not proto:
        return configured
    try:
        parsed = urlparse(configured)
    except ValueError:
        return configured
    internal_host = (parsed.hostname or "").lower() in {"127.0.0.1", "localhost", "0.0.0.0", "::1"}
    internal_port = parsed.port in {8000, 8001, 8080}
    if proto == "https" and (parsed.scheme != "https" or internal_host or internal_port):
        return f"https://{host}".rstrip("/")
    if internal_host and host:
        return f"{proto}://{host}".rstrip("/")
    return configured
