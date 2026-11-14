"""Dashboard HTML pages and redirects, with public URLs injected for the browser."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Request
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


# Dashboard pages served from the shared shell (index.html). The page name decides which
# sections are visible and which /api/status/* endpoint the browser polls.
DASHBOARD_PAGES = ("shana", "people", "integrations", "coding", "system")

@router.get("/")
@router.get("/dashboard")
def dashboard(request: Request = None) -> HTMLResponse:
    """Return the Home page: presence, what Shana is doing, quick health."""
    return _dashboard_page(INDEX_PAGE, dashboard_page="dashboard", request=request)


@router.get("/dashboard/shana")
def dashboard_shana_page(request: Request = None) -> HTMLResponse:
    """Return the Shana page: voice, providers and behaviour settings."""
    return _dashboard_page(INDEX_PAGE, dashboard_page="shana", request=request)


@router.get("/dashboard/people")
def dashboard_people_page(request: Request = None) -> HTMLResponse:
    """Return the People & Memory page."""
    return _dashboard_page(INDEX_PAGE, dashboard_page="people", request=request)


@router.get("/dashboard/integrations")
def dashboard_integrations_page(request: Request = None) -> HTMLResponse:
    """Return the Integrations page: Twitch, Discord and stream outputs."""
    return _dashboard_page(INDEX_PAGE, dashboard_page="integrations", request=request)


@router.get("/dashboard/coding")
def dashboard_coding_page(request: Request = None) -> HTMLResponse:
    """Return the owner-reviewed coding page."""
    return _dashboard_page(INDEX_PAGE, dashboard_page="coding", request=request)


@router.get("/dashboard/system")
def dashboard_system_page(request: Request = None) -> HTMLResponse:
    """Return the System page: service controls, machine, diagnostics and logs."""
    return _dashboard_page(INDEX_PAGE, dashboard_page="system", request=request)


def _redirect(target: str, request: Request = None) -> RedirectResponse:
    """Redirect a retired page URL, keeping any query string (monitor links carry api_base)."""
    query = request.url.query if request is not None else ""
    return RedirectResponse(url=f"{target}?{query}" if query else target, status_code=307)


# Retired page URLs. Each one redirects to the page that now holds its content.
@router.get("/dashboard/live", include_in_schema=False)
def dashboard_live_page(request: Request = None) -> RedirectResponse:
    """Live voice moved into the Talk (monitor) page, which already embeds the panel."""
    return _redirect("/dashboard/monitor", request)


@router.get("/dashboard/talk", include_in_schema=False)
def dashboard_talk_page(request: Request = None) -> RedirectResponse:
    """Redirect the retired Talk client to the persistent Monitor room."""
    return _redirect("/dashboard/monitor", request)


@router.get("/talk", include_in_schema=False)
def talk_page_redirect(request: Request = None) -> RedirectResponse:
    """Redirect the retired short Talk URL to Monitor."""
    return _redirect("/dashboard/monitor", request)


@router.get("/dashboard/presence", include_in_schema=False)
def dashboard_presence_page(request: Request = None) -> RedirectResponse:
    """Presence controls now live on Home."""
    return _redirect("/dashboard", request)


@router.get("/presence", include_in_schema=False)
def presence_page_redirect(request: Request = None) -> RedirectResponse:
    """Redirect stale short Presence links to Home."""
    return _redirect("/dashboard", request)


@router.get("/dashboard/status", include_in_schema=False)
def dashboard_status_page(request: Request = None) -> RedirectResponse:
    """Status diagnostics now live on System."""
    return _redirect("/dashboard/system", request)


@router.get("/dashboard/stream", include_in_schema=False)
def dashboard_stream_page(request: Request = None) -> RedirectResponse:
    """Stream and Twitch controls now live on Integrations."""
    return _redirect("/dashboard/integrations", request)


@router.get("/dashboard/twitch", include_in_schema=False)
def dashboard_twitch_page(request: Request = None) -> RedirectResponse:
    """Redirect the old Twitch URL to Integrations."""
    return _redirect("/dashboard/integrations", request)


@router.get("/dashboard/memory", include_in_schema=False)
def dashboard_memory_page(request: Request = None) -> RedirectResponse:
    """Memory now lives on People."""
    return _redirect("/dashboard/people", request)


@router.get("/dashboard/improvement", include_in_schema=False)
def dashboard_improvement_page(request: Request = None) -> RedirectResponse:
    """The coding review queue now lives on Coding."""
    return _redirect("/dashboard/coding", request)


@router.get("/dashboard/settings", include_in_schema=False)
def dashboard_settings_page(request: Request = None) -> RedirectResponse:
    """Provider and assistant settings now live on Shana."""
    return _redirect("/dashboard/shana", request)


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
