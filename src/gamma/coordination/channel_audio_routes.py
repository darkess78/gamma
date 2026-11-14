"""Explicit private listening consent for checked observation-event speech."""
from typing import Literal

from fastapi import Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field


IDENTIFIER = r'^[A-Za-z0-9_-]{1,80}$'


class ChannelAudioConsumer(BaseModel):
    model_config = ConfigDict(extra='forbid')
    audience: Literal['owner_private']
    consumer_id: str = Field(pattern=IDENTIFIER)
    request_id: str | None = Field(default=None, pattern=IDENTIFIER)


class ChannelAudioConsent(ChannelAudioConsumer):
    enabled: bool = Field(strict=True)


class ChannelAudioRender(ChannelAudioConsumer):
    stream_output_id: str = Field(pattern=r'^stream-output-[a-f0-9]{32}$')


class MomentAudioRender(BaseModel):
    model_config = ConfigDict(extra='forbid')
    audience: Literal['owner_private']
    consumer_id: str = Field(pattern=IDENTIFIER)
    moment_id: str = Field(pattern=IDENTIFIER)


def _json(value):
    return JSONResponse(value, headers={'Cache-Control': 'no-store'})


def add_backend_routes(app):
    prefix = '/v1/coordination/channel-audio'

    @app.get(prefix + '/status')
    def status(audience: Literal['owner_private'], consumer_id: str = Query(pattern=IDENTIFIER)):
        return _json(app.state.channel_audio.status(consumer_id))

    @app.post(prefix + '/connect')
    def connect(payload: ChannelAudioConsumer):
        return _json(app.state.channel_audio.connect(payload.consumer_id))

    @app.post(prefix + '/enable')
    def enable(payload: ChannelAudioConsent):
        return _json(app.state.channel_audio.enable(payload.consumer_id, payload.enabled))

    @app.post(prefix + '/render')
    def render(payload: ChannelAudioRender):
        return _json(app.state.channel_audio.render(payload.stream_output_id, payload.consumer_id))

    @app.post(prefix + '/moment/render')
    def render_moment(payload: MomentAudioRender):
        return _json(app.state.channel_audio.render_moment(payload.moment_id, payload.consumer_id))

    @app.post(prefix + '/interrupt')
    def interrupt(payload: ChannelAudioConsumer):
        return _json(app.state.channel_audio.interrupt(payload.consumer_id))

    @app.post(prefix + '/disconnect')
    def disconnect(payload: ChannelAudioConsumer):
        return _json(app.state.channel_audio.disconnect(payload.consumer_id))


def add_dashboard_routes(app, client):
    prefix, backend = '/api/coordination/channel-audio', '/v1/coordination/channel-audio'

    @app.get(prefix + '/status')
    def status(audience: Literal['owner_private'], consumer_id: str = Query(pattern=IDENTIFIER)):
        return _json(client.request_json('GET', backend + '/status', params={
            'audience': audience, 'consumer_id': consumer_id}))

    @app.post(prefix + '/connect')
    def connect(payload: ChannelAudioConsumer):
        return _json(client.request_json('POST', backend + '/connect', json_payload=payload.model_dump()))

    @app.post(prefix + '/enable')
    def enable(payload: ChannelAudioConsent):
        return _json(client.request_json('POST', backend + '/enable', json_payload=payload.model_dump()))

    @app.post(prefix + '/render')
    def render(payload: ChannelAudioRender):
        return _json(client.request_json('POST', backend + '/render', json_payload=payload.model_dump(), timeout=180))

    @app.post(prefix + '/moment/render')
    def render_moment(payload: MomentAudioRender):
        return _json(client.request_json('POST', backend + '/moment/render', json_payload=payload.model_dump(), timeout=180))

    @app.post(prefix + '/interrupt')
    def interrupt(payload: ChannelAudioConsumer):
        return _json(client.request_json('POST', backend + '/interrupt', json_payload=payload.model_dump()))

    @app.post(prefix + '/disconnect')
    def disconnect(payload: ChannelAudioConsumer):
        return _json(client.request_json('POST', backend + '/disconnect', json_payload=payload.model_dump()))
