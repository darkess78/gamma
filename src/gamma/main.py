from __future__ import annotations

import secrets

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import settings
from .api.routes import router

app = FastAPI(title=settings.app_name)
app.include_router(router)


@app.middleware("http")
async def require_api_auth(request: Request, call_next):
    path = request.url.path
    if not settings.api_auth_enabled or not path.startswith("/v1/"):
        return await call_next(request)
    auth_header = request.headers.get("authorization", "")
    expected = f"Bearer {settings.api_bearer_token}"
    if settings.api_bearer_token and secrets.compare_digest(auth_header, expected):
        return await call_next(request)
    return JSONResponse({"detail": "api authentication required"}, status_code=401)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
