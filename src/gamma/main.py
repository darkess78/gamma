from __future__ import annotations

import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import settings
from .api.routes import get_proactive_scheduler, router
from .observability import configure_logging, install_request_logging

@asynccontextmanager
async def lifespan(_app: FastAPI):
    scheduler = get_proactive_scheduler()
    await scheduler.start()
    try:
        yield
    finally:
        await scheduler.stop()


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
