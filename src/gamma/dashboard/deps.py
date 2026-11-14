"""Shared dashboard singletons. Route modules call these through the module so
tests can replace them in one place (``patch.object(deps, ...)``)."""
from __future__ import annotations

from fastapi import HTTPException, Request

from ..system.lazy_singleton import LazySingleton
from ..voice.live import LiveVoiceSession
from .service import DashboardService

service = LazySingleton[DashboardService]()
live_voice_session = LazySingleton[LiveVoiceSession]()


def get_dashboard_service() -> DashboardService:
    return service.get(DashboardService)


def get_live_voice_session() -> LiveVoiceSession:
    def build() -> LiveVoiceSession:
        dashboard_service = get_dashboard_service()
        return LiveVoiceSession(
            job_starter=dashboard_service.start_remote_live_job,
            job_fetcher=dashboard_service.get_remote_live_job,
            job_canceler=dashboard_service.cancel_remote_live_job,
            partial_transcriber=dashboard_service.transcribe_remote_live_audio,
        )

    return live_voice_session.get(build)


async def json_object_payload(request: Request) -> dict:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="object payload is required")
    return payload
