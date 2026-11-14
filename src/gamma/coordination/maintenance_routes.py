"""Authenticated owner-only recovery controls for the shared dashboard."""
from typing import Literal

from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field


class MaintenanceRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    audience: Literal['owner_private']
    action: Literal['check','backup','rehearse']
    request_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    backup_id: str | None = Field(default=None, pattern=r'^backup-[a-f0-9]{32}$')


def add_backend_routes(app):
    @app.get('/v1/coordination/maintenance')
    def status(audience: Literal['owner_private']):
        return JSONResponse(app.state.maintenance.status(), headers={'Cache-Control': 'no-store'})

    @app.post('/v1/coordination/maintenance')
    def control(payload: MaintenanceRequest):
        service = app.state.maintenance
        if payload.action == 'check':
            result = service.check()
        elif payload.action == 'backup':
            result = service.backup(payload.request_id)
        else:
            result = service.rehearse(payload.backup_id)
        return JSONResponse(result, headers={'Cache-Control': 'no-store'})


def add_dashboard_routes(app, client):
    @app.get('/api/coordination/maintenance')
    def status(audience: Literal['owner_private']):
        return JSONResponse(client.request_json('GET', '/v1/coordination/maintenance', params={'audience': audience}), headers={'Cache-Control': 'no-store'})

    @app.post('/api/coordination/maintenance')
    def control(payload: MaintenanceRequest):
        return JSONResponse(client.request_json('POST', '/v1/coordination/maintenance', json_payload=payload.model_dump(), timeout=180), headers={'Cache-Control': 'no-store'})
