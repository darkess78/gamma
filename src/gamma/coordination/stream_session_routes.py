"""Typed owner-private rehearsal API. No route publishes to Twitch or OBS."""
from typing import Literal

from fastapi import Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .stream_session_models import Identifier, RehearsalAck, RehearsalEvent, StreamPlan


class StartStream(StreamPlan):
    request_id: str = Field(pattern=Identifier)


class StreamConsumer(BaseModel):
    model_config = ConfigDict(extra='forbid')
    audience: Literal['owner_private']
    session_id: str = Field(pattern=r'^stream-[a-f0-9]{32}$')
    consumer_id: str = Field(pattern=Identifier)


class StreamControl(BaseModel):
    model_config = ConfigDict(extra='forbid')
    audience: Literal['owner_private']
    request_id: str = Field(pattern=Identifier)
    session_id: str = Field(pattern=r'^stream-[a-f0-9]{32}$')
    action: Literal['stop', 'owner_away', 'owner_return', 'focus', 'relax']


def _json(value):
    return JSONResponse(value, headers={'Cache-Control': 'no-store'})


def add_backend_routes(app):
    prefix = '/v1/coordination/stream'

    @app.get(prefix + '/status')
    def status(audience: Literal['owner_private']):
        return _json(app.state.stream_session.status())

    @app.post(prefix + '/start')
    def start(payload: StartStream):
        return _json(app.state.stream_session.start(payload.request_id,
            payload.model_dump(exclude={'request_id'})))

    @app.post(prefix + '/control')
    def control(payload: StreamControl):
        return _json(app.state.stream_session.control(payload.request_id, payload.session_id, payload.action))

    @app.post(prefix + '/connect')
    def connect(payload: StreamConsumer):
        return _json(app.state.stream_session.connect(payload.session_id, payload.consumer_id))

    @app.post(prefix + '/heartbeat')
    def heartbeat(payload: StreamConsumer):
        return _json(app.state.stream_session.heartbeat(payload.session_id, payload.consumer_id))

    @app.post(prefix + '/disconnect')
    def disconnect(payload: StreamConsumer):
        return _json(app.state.stream_session.disconnect(payload.session_id, payload.consumer_id))

    @app.post(prefix + '/event')
    def event(payload: RehearsalEvent):
        return _json(app.state.stream_session.submit(payload))

    @app.get(prefix + '/next')
    def next_output(audience: Literal['owner_private'],
                    session_id: str = Query(pattern=r'^stream-[a-f0-9]{32}$'),
                    consumer_id: str = Query(pattern=Identifier)):
        return _json(app.state.stream_session.next_output(session_id, consumer_id))

    @app.post(prefix + '/ack')
    def ack(payload: RehearsalAck):
        return _json(app.state.stream_session.ack(payload))


def add_dashboard_routes(app, client):
    prefix, backend = '/api/coordination/stream', '/v1/coordination/stream'

    @app.get(prefix + '/status')
    def status(audience: Literal['owner_private']):
        return _json(client.request_json('GET', backend + '/status', params={'audience': audience}))

    @app.post(prefix + '/start')
    def start(payload: StartStream):
        return _json(client.request_json('POST', backend + '/start', json_payload=payload.model_dump()))

    @app.post(prefix + '/control')
    def control(payload: StreamControl):
        return _json(client.request_json('POST', backend + '/control', json_payload=payload.model_dump()))

    @app.post(prefix + '/connect')
    def connect(payload: StreamConsumer):
        return _json(client.request_json('POST', backend + '/connect', json_payload=payload.model_dump()))

    @app.post(prefix + '/heartbeat')
    def heartbeat(payload: StreamConsumer):
        return _json(client.request_json('POST', backend + '/heartbeat', json_payload=payload.model_dump()))

    @app.post(prefix + '/disconnect')
    def disconnect(payload: StreamConsumer):
        return _json(client.request_json('POST', backend + '/disconnect', json_payload=payload.model_dump()))

    @app.post(prefix + '/event')
    def event(payload: RehearsalEvent):
        return _json(client.request_json('POST', backend + '/event', json_payload=payload.model_dump(), timeout=180))

    @app.get(prefix + '/next')
    def next_output(audience: Literal['owner_private'],
                    session_id: str = Query(pattern=r'^stream-[a-f0-9]{32}$'),
                    consumer_id: str = Query(pattern=Identifier)):
        return _json(client.request_json('GET', backend + '/next', params={
            'audience': audience, 'session_id': session_id, 'consumer_id': consumer_id}))

    @app.post(prefix + '/ack')
    def ack(payload: RehearsalAck):
        return _json(client.request_json('POST', backend + '/ack', json_payload=payload.model_dump()))
