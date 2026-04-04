from __future__ import annotations

from fastapi import FastAPI

from .config import settings
from .api.routes import router

app = FastAPI(title=settings.app_name)
app.include_router(router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
