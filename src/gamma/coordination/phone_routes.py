"""Typed phone proxies. Existing API and dashboard authentication stay mandatory."""
from pathlib import Path
from typing import Literal

from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field


class PhoneRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    audience: Literal['owner_private']
    request_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    device_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    device_secret: str = Field(pattern=r'^[A-Za-z0-9_-]{43,128}$')


class RegisterPhone(PhoneRequest):
    label: str = Field(default='My phone', min_length=1, max_length=60)


class RevokePhoneDevice(BaseModel):
    model_config = ConfigDict(extra='forbid')
    audience: Literal['owner_private']
    request_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    device_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')


class ConnectedPhone(PhoneRequest):
    epoch: int = Field(ge=1, strict=True)


class PhoneInput(ConnectedPhone):
    text: str = Field(min_length=1, max_length=8000)
    session_id: str = Field(default='monitor-private', pattern=r'^[A-Za-z0-9_-]{1,80}$')


class PhoneResult(ConnectedPhone):
    input_request_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')


class PhoneConversation(ConnectedPhone):
    session_id: str = Field(default='monitor-private', pattern=r'^[A-Za-z0-9_-]{1,80}$')


class PhoneReceipt(ConnectedPhone):
    message_id: str = Field(pattern=r'^reminder-[a-f0-9]{32}$')
    status: Literal['received', 'acknowledged', 'uncertain']


class PhoneControl(ConnectedPhone):
    action: Literal['availability', 'disconnect', 'revoke', 'contact_policy']
    value: dict | str | None = None


class PhoneSchedule(ConnectedPhone):
    commitment_id: str = Field(pattern=r'^mem-[a-f0-9]{24}$')
    due_at: float = Field(ge=0, allow_inf_nan=False)
    expires_at: float = Field(ge=0, allow_inf_nan=False)


def _json(value):
    return JSONResponse(value, headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})


def _credentials(payload):
    return payload.device_id, payload.device_secret, payload.epoch


def add_backend_routes(app):
    prefix = '/v1/coordination/phone'

    @app.get(prefix + '/summary')
    def summary(audience: Literal['owner_private']):
        return _json(app.state.phone.snapshot())

    @app.get(prefix + '/devices')
    def devices(audience: Literal['owner_private']):
        return _json(app.state.phone.owner_devices())

    @app.post(prefix + '/revoke-device')
    def revoke_device(payload: RevokePhoneDevice):
        return _json(app.state.phone.owner_revoke(payload.request_id, payload.device_id))

    @app.post(prefix + '/register')
    def register(payload: RegisterPhone):
        return _json(app.state.phone.register(payload.request_id, payload.device_id, payload.device_secret, payload.label))

    @app.post(prefix + '/connect')
    def connect(payload: PhoneRequest):
        return _json(app.state.phone.connect(payload.device_id, payload.device_secret, payload.request_id))

    @app.post(prefix + '/state')
    def state(payload: ConnectedPhone):
        return _json(app.state.phone.state(*_credentials(payload)))

    @app.post(prefix + '/control')
    def control(payload: PhoneControl):
        return _json(app.state.phone.control(*_credentials(payload), payload.action, payload.value))

    @app.post(prefix + '/input')
    def input(payload: PhoneInput):
        return _json(app.state.phone.send(*_credentials(payload), payload.request_id, payload.text, payload.session_id))

    @app.post(prefix + '/result')
    def result(payload: PhoneResult):
        return _json(app.state.phone.result(*_credentials(payload), payload.input_request_id))

    @app.post(prefix + '/conversation')
    def conversation(payload: PhoneConversation):
        return _json(app.state.phone.conversation(*_credentials(payload), payload.session_id))

    @app.post(prefix + '/poll')
    def poll(payload: ConnectedPhone):
        return _json(app.state.phone.poll(*_credentials(payload)))

    @app.post(prefix + '/receipt')
    def receipt(payload: PhoneReceipt):
        return _json(app.state.phone.receipt(*_credentials(payload), payload.message_id, payload.status))

    @app.post(prefix + '/schedule')
    def schedule(payload: PhoneSchedule):
        return _json(app.state.phone.schedule(*_credentials(payload), payload.commitment_id, payload.due_at, payload.expires_at))


def add_dashboard_routes(app, client):
    prefix, backend = '/api/coordination/phone', '/v1/coordination/phone'

    @app.get('/dashboard/phone')
    def page():
        from .dashboard import _shell
        return HTMLResponse(_shell((Path(__file__).with_name('phone.html')).read_text(encoding='utf-8')),
                            headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})

    @app.get('/dashboard/phone-worker.js')
    def notification_worker():
        path = Path(__file__).parents[1] / 'dashboard' / 'static' / 'phone_worker.js'
        return Response(path.read_text(encoding='utf-8'), media_type='application/javascript',
                        headers={'Cache-Control': 'no-store', 'Service-Worker-Allowed': '/dashboard/phone',
                                 'Referrer-Policy': 'no-referrer', 'X-Content-Type-Options': 'nosniff'})

    @app.get(prefix + '/summary')
    def summary(audience: Literal['owner_private']):
        return _json(client.request_json('GET', backend + '/summary', params={'audience': audience}))

    @app.get(prefix + '/devices')
    def devices(audience: Literal['owner_private']):
        return _json(client.request_json('GET', backend + '/devices', params={'audience': audience}))

    @app.post(prefix + '/revoke-device')
    def revoke_device(payload: RevokePhoneDevice):
        return _json(client.request_json('POST', backend + '/revoke-device', json_payload=payload.model_dump()))

    @app.post(prefix + '/register')
    def register(payload: RegisterPhone):
        return _json(client.request_json('POST', backend + '/register', json_payload=payload.model_dump()))

    @app.post(prefix + '/connect')
    def connect(payload: PhoneRequest):
        return _json(client.request_json('POST', backend + '/connect', json_payload=payload.model_dump()))

    @app.post(prefix + '/state')
    def state(payload: ConnectedPhone):
        return _json(client.request_json('POST', backend + '/state', json_payload=payload.model_dump()))

    @app.post(prefix + '/control')
    def control(payload: PhoneControl):
        return _json(client.request_json('POST', backend + '/control', json_payload=payload.model_dump()))

    @app.post(prefix + '/input')
    def input(payload: PhoneInput):
        return _json(client.request_json('POST', backend + '/input', json_payload=payload.model_dump(), timeout=180))

    @app.post(prefix + '/result')
    def result(payload: PhoneResult):
        return _json(client.request_json('POST', backend + '/result', json_payload=payload.model_dump()))

    @app.post(prefix + '/conversation')
    def conversation(payload: PhoneConversation):
        return _json(client.request_json('POST', backend + '/conversation', json_payload=payload.model_dump()))

    @app.post(prefix + '/poll')
    def poll(payload: ConnectedPhone):
        return _json(client.request_json('POST', backend + '/poll', json_payload=payload.model_dump()))

    @app.post(prefix + '/receipt')
    def receipt(payload: PhoneReceipt):
        return _json(client.request_json('POST', backend + '/receipt', json_payload=payload.model_dump()))

    @app.post(prefix + '/schedule')
    def schedule(payload: PhoneSchedule):
        return _json(client.request_json('POST', backend + '/schedule', json_payload=payload.model_dump()))
