from __future__ import annotations

import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import settings
from .api.deps import get_proactive_scheduler
from .api.routes import router
from .observability import configure_logging, install_request_logging
from .memory.journal_lifecycle import JournalRecovery

@asynccontextmanager
async def lifespan(_app: FastAPI):
    scheduler = get_proactive_scheduler()
    recovery = JournalRecovery()
    try:
        await recovery.start()
        await scheduler.start()
        if settings.avatar3d_enabled:
            from .api.routes_performer import start_avatar3d_runner

            await start_avatar3d_runner()
        yield
    finally:
        if settings.avatar3d_enabled:
            from .api.deps import get_avatar3d_runner

            get_avatar3d_runner().stop()
        try:
            await scheduler.stop()
        finally:
            await recovery.stop()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(router)


@app.middleware("http")
async def require_api_auth(request: Request, call_next) -> Response:
    """Require API auth.
    
    Args:
        request: HTTP request.
        call_next: Next handler.
    
    Returns:
        Response: Response.
    """
    path = request.url.path
    if not settings.api_auth_enabled or not path.startswith("/"):
        return await call_next(request)
    auth_header = request.headers.get("authorization", "")
    expected = f"Bearer {settings.api_bearer_token}"
    if settings.api_bearer_token and secrets.compare_digest(auth_header, expected):
        return await call_next(request)
    return JSONResponse({"detail": "api authentication required"}, status_code=401)


install_request_logging(app, service="shana", logger=configure_logging("shana"))


@app.get("/health")
def health() -> dict[str, str]:
    """Health check.
    
    Returns:
        dict: Health status.
    """
    return {"status": "ok"}
