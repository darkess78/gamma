from __future__ import annotations

import secrets
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import settings
from .api.routes import get_proactive_scheduler, get_stream_brain, reset_minecraft_coordinator, router, start_minecraft_coordinator
from .observability import configure_logging, install_request_logging
from .memory.lifecycle import StateLifecycle
from .brain.runtime import start_brain_queue
from .api.routes import stop_core_live_runtime, stop_voice_transcription
from .api.stt_routes import router as stt_router
from .api.tts_routes import router as tts_router
from .voice.tts_runtime import close_tts_worker
from .brain.model_runtime import close_model_worker
from .api.model_routes import router as model_router
from .api.ingress_routes import router as ingress_router

@asynccontextmanager
async def lifespan(_app: FastAPI):
    state_lifecycle = StateLifecycle()
    await state_lifecycle.start()
    try:
        scheduler = get_proactive_scheduler()
        coordinator = start_minecraft_coordinator()
        brain_queue = None
        stream_delivery = get_stream_brain().delivery_jobs
        try:
            brain_queue = start_brain_queue()
            from .brain.process import get_brain_process, BrainUnavailable
            try:
                await asyncio.to_thread(get_brain_process().control, "start")
            except BrainUnavailable:
                logging.getLogger("gamma.brain").error("Brain unavailable; API remains online.")
            stream_delivery.start()
            await scheduler.start()
            yield
        finally:
            try:
                reset_minecraft_coordinator(coordinator)
            finally:
                try:
                    await scheduler.stop()
                finally:
                    try:
                        stop_core_live_runtime()
                    finally:
                        try:
                            await asyncio.to_thread(stop_voice_transcription)
                        finally:
                            from .brain.process import get_brain_process
                            await asyncio.to_thread(get_brain_process().control, "stop")
                            if brain_queue is not None:
                                stopped = await asyncio.to_thread(brain_queue.close, timeout=5)
                                if not stopped:
                                    logging.getLogger("gamma.brain").warning("Brain queue is draining an active provider call.")
                            stopped_delivery = await asyncio.to_thread(stream_delivery.close, timeout=5)
                            if not stopped_delivery:
                                logging.getLogger("gamma.delivery").warning("Stream delivery is draining an active provider call.")
    finally:
        try:
            await asyncio.to_thread(close_tts_worker)
        finally:
            try:
                await asyncio.to_thread(close_model_worker)
            finally:
                await state_lifecycle.stop()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(router)
app.include_router(stt_router)
app.include_router(tts_router)
app.include_router(model_router)
app.include_router(ingress_router)


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
