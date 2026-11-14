"""Pages and redirects served by the Shana API: root, dashboard redirects, performer, monitor and the subtitle overlay."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from . import deps
from ..config import settings


router = APIRouter()

@router.get("/")
def root() -> dict[str, str]:
    return {"message": "gamma backend scaffold"}


@router.get("/dashboard")
def dashboard() -> RedirectResponse:
    return RedirectResponse(url=f"{settings.dashboard_base_url}/dashboard", status_code=307)


@router.get("/dashboard/{page_name}")
def dashboard_page_redirect(page_name: str) -> RedirectResponse:
    if page_name == "twitch":
        page_name = "stream"
    if page_name == "talk":
        page_name = "monitor"
    allowed = {"live", "monitor", "status", "presence", "stream", "memory", "settings"}
    if page_name not in allowed:
        raise HTTPException(status_code=404, detail="dashboard page not found")
    return RedirectResponse(url=f"{settings.dashboard_base_url}/dashboard/{page_name}", status_code=307)


@router.get("/presence")
def presence_page_redirect() -> RedirectResponse:
    return RedirectResponse(url=f"{settings.dashboard_base_url}/dashboard/presence", status_code=307)


@router.get("/talk")
def talk_page_redirect() -> RedirectResponse:
    return RedirectResponse(url=f"{settings.dashboard_base_url}/dashboard/monitor", status_code=307)


@router.get("/performer")
def performer_page() -> HTMLResponse:
    return _performer_page(deps.PERFORMER_STATIC_DIR / "performer.html")


@router.get("/performer/assets/shana/default.png")
def performer_default_image() -> FileResponse:
    if not deps.SHANA_DEFAULT_IMAGE.exists():
        raise HTTPException(status_code=404, detail="default performer image not found")
    return FileResponse(deps.SHANA_DEFAULT_IMAGE, media_type="image/png")


@router.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(deps.DASHBOARD_STATIC_DIR / "favicon.svg", media_type="image/svg+xml")


@router.get("/monitor")
def monitor_page() -> HTMLResponse:
    return _dashboard_output_page(deps.DASHBOARD_STATIC_DIR / "monitor.html")


@router.get("/overlay/subtitles")
def subtitle_overlay_page() -> HTMLResponse:
    return _dashboard_output_page(deps.DASHBOARD_STATIC_DIR / "overlay.html")


def _dashboard_output_page(path: Path) -> HTMLResponse:
    html = path.read_text(encoding="utf-8")
    config = (
        f'<script>window.GAMMA_SHANA_BASE_URL = "{settings.shana_base_url}";'
        f' window.GAMMA_DASHBOARD_BASE_URL = "{settings.dashboard_base_url}";</script>'
    )
    html = html.replace("</head>", f"  {config}\n</head>", 1)
    return HTMLResponse(html)


def _performer_page(path: Path) -> HTMLResponse:
    html = path.read_text(encoding="utf-8")
    config = (
        f'<script>window.GAMMA_SHANA_BASE_URL = "{settings.shana_base_url}";'
        f' window.GAMMA_DASHBOARD_BASE_URL = "{settings.dashboard_base_url}";</script>'
    )
    html = html.replace("</head>", f"  {config}\n</head>", 1)
    return HTMLResponse(html)
