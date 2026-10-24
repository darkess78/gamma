"""Session-authenticated fixed-target proxy for approved browser playback."""
import inspect
from urllib.parse import urlsplit, urlunsplit, urlencode

import httpx
import websockets
from fastapi import APIRouter, HTTPException, Path, WebSocket
from fastapi.responses import Response

from ..config import settings
from .auth import websocket_is_authenticated
from .playback_relay import relay

router = APIRouter()
MAX_AUDIO_BYTES = 33554432


def _headers():
    if settings.api_auth_enabled:
        if not settings.api_bearer_token:
            raise RuntimeError("internal API authentication unavailable")
        return {"Authorization": f"Bearer {settings.api_bearer_token}"}
    return {}


def _connect():
    base = urlsplit(settings.shana_internal_base_url)
    if base.scheme not in {"http", "https"} or base.username or base.password:
        raise RuntimeError("invalid internal API URL")
    url = urlunsplit(("wss" if base.scheme == "https" else "ws", base.netloc,
        base.path.rstrip("/") + "/v1/performer/events", urlencode({
            "target_policy": "dashboard_monitor", "client_name": "dashboard_live", "replay_recent": 0}), ""))
    parameters = inspect.signature(websockets.connect).parameters
    options = {"max_size": 2097152, "max_queue": 16, "open_timeout": 10, "close_timeout": 2}
    options["additional_headers" if "additional_headers" in parameters else "extra_headers"] = _headers()
    if "proxy" in parameters:
        options["proxy"] = None
    return websockets.connect(url, **options)


@router.websocket("/api/playback/events")
async def playback_events(websocket: WebSocket):
    origin = urlsplit(websocket.headers.get("origin", ""))
    if (origin.scheme not in {"http", "https"}
            or origin.netloc.lower() != websocket.headers.get("host", "").lower()):
        await websocket.close(code=1008)
        return
    if not websocket_is_authenticated(websocket):
        await websocket.close(code=4401)
        return
    try:
        async with _connect() as upstream:
            await websocket.accept()
            await relay(websocket, upstream)
    except Exception:
        # Never relay upstream exception text containing URLs or credentials.
        try:
            await websocket.close(code=1011)
        except Exception:
            pass


@router.get("/api/playback/audio/{grant_id}")
async def playback_audio(grant_id: str = Path(pattern=r"^[a-f0-9]{32}$")):
    # Dashboard HTTP middleware authenticates this route; it is not a generic proxy.
    try:
        async with httpx.AsyncClient(base_url=settings.shana_internal_base_url,
                headers=_headers(), timeout=20, follow_redirects=False, trust_env=False) as client:
            async with client.stream("GET", f"/v1/delivery/{grant_id}/audio") as upstream:
                if upstream.status_code != 200:
                    code = upstream.status_code if upstream.status_code in {401, 403, 404, 410} else 502
                    raise HTTPException(status_code=code, detail="approved audio unavailable")
                chunks, size = [], 0
                async for chunk in upstream.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_AUDIO_BYTES:
                        raise HTTPException(status_code=502, detail="approved audio exceeds limit")
                    chunks.append(chunk)
                headers = {"Cache-Control": "no-store"}
                if digest := upstream.headers.get("x-content-sha256"):
                    headers["X-Content-SHA256"] = digest
                return Response(b"".join(chunks), media_type=upstream.headers.get("content-type", "application/octet-stream"), headers=headers)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=502, detail="approved audio unavailable") from None
