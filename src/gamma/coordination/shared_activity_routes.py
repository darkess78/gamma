"""Typed private dashboard proxies for selected-source shared activities."""
import base64
import binascii
import asyncio
from typing import Literal

from fastapi import Path as ApiPath
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt


class PrivateShared(BaseModel):
    model_config = ConfigDict(extra='forbid')
    audience: Literal['owner_private']
    request_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')


class SharedControl(PrivateShared):
    action: Literal['select', 'stop', 'goal', 'mode']
    values: dict = Field(default_factory=dict)


class SourceMetadata(BaseModel):
    model_config = ConfigDict(extra='forbid')
    observation_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    source_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    source_epoch: int = Field(ge=0, strict=True)
    sequence: int = Field(ge=0, strict=True)
    captured_at: StrictFloat | StrictInt = Field(ge=0, allow_inf_nan=False)
    kind: Literal['osrs_screen', 'minecraft_state', 'selected_application']


class SharedObservation(SourceMetadata):
    summary: str = Field(min_length=1, max_length=600)
    confidence: StrictFloat | StrictInt = Field(ge=0, le=1, allow_inf_nan=False)
    content_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


class SharedObserve(PrivateShared):
    observation: SharedObservation


class SharedImage(PrivateShared):
    metadata: SourceMetadata
    image_base64: str = Field(min_length=12, max_length=1_066_668)
    media_type: Literal['image/png', 'image/jpeg', 'image/webp']


class MomentReceived(PrivateShared):
    consumer_id: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_-]{1,80}$')
    moment_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    text_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


class WatchStart(PrivateShared):
    source_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    source_epoch: int = Field(ge=0, strict=True)
    minutes: int = Field(ge=5, le=30, strict=True)


class WatchLease(PrivateShared):
    watch_id: str = Field(pattern=r'^watch-[a-f0-9]{32}$')
    lease: str = Field(pattern=r'^[a-f0-9]{32}$')


class WatchImage(SharedImage):
    watch_id: str = Field(pattern=r'^watch-[a-f0-9]{32}$')
    lease: str = Field(pattern=r'^[a-f0-9]{32}$')


class GamePermit(PrivateShared):
    seconds: int = Field(default=60, ge=10, le=120, strict=True)
    maximum_commands: int = Field(default=6, ge=1, le=12, strict=True)


class GameAction(PrivateShared):
    name: Literal['join', 'leave', 'follow_owner', 'wait_here', 'come_here', 'look_at_owner',
                  'report_status', 'stop', 'emergency_stop']
    arguments: dict = Field(default_factory=dict)


class GameTrial(PrivateShared):
    spec: dict


class GameTrialRun(PrivateShared):
    trial_id: str = Field(pattern=r'^skill-[a-f0-9]{24}$')
    scenario_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')


def _game(app):
    game = getattr(app.state, 'shared_minecraft', None)
    if game is None:
        from fastapi import HTTPException
        raise HTTPException(409, 'A private disposable Minecraft world is not connected.')
    return game


def _json(value):
    return JSONResponse(value, headers={'Cache-Control': 'no-store'})


def _image_bytes(payload):
    try:
        return base64.b64decode(payload.image_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError('invalid_shared_image_encoding') from exc


def add_backend_routes(app):
    prefix = '/v1/coordination/shared-activity'

    @app.get(prefix + '/status')
    def status(audience: Literal['owner_private']):
        return _json(app.state.shared_activity.status())

    @app.post(prefix + '/control')
    async def control(payload: SharedControl):
        result = await asyncio.to_thread(app.state.shared_activity.control, payload.request_id, payload.action, payload.values)
        game = getattr(app.state, 'shared_minecraft', None)
        if game is not None and payload.action in {'select', 'stop'}:
            await game.ensure_authority()
        return _json(result)

    @app.post(prefix + '/observe')
    def observe(payload: SharedObserve):
        return _json(app.state.shared_activity.observe(payload.observation.model_dump()))

    @app.post(prefix + '/image')
    def image(payload: SharedImage):
        return _json(app.state.shared_activity.observe_image(payload.metadata.model_dump(), _image_bytes(payload), payload.media_type))

    @app.post(prefix + '/moment')
    def moment(payload: SharedImage):
        return _json(app.state.shared_activity.moments.share(payload.request_id, payload.metadata.model_dump(),
                                                           _image_bytes(payload), payload.media_type))

    @app.get(prefix + '/moments/{moment_id}')
    def inspect_moment(audience: Literal['owner_private'], moment_id: str = ApiPath(pattern=r'^[A-Za-z0-9_-]{1,80}$')):
        return _json(app.state.shared_activity.moments.inspect(moment_id))

    @app.post(prefix + '/moment/received')
    def moment_received(payload: MomentReceived):
        return _json(app.state.shared_activity.moments.received(payload.request_id, payload.moment_id, payload.text_sha256, payload.consumer_id))

    @app.post(prefix + '/watch/start')
    def watch_start(payload: WatchStart):
        return _json(app.state.shared_activity.moments.watch.start(payload.request_id, payload.source_id,
                                                                 payload.source_epoch, payload.minutes))

    @app.get(prefix + '/watch/{watch_id}')
    def inspect_watch(audience: Literal['owner_private'], watch_id: str = ApiPath(pattern=r'^watch-[a-f0-9]{32}$')):
        return _json(app.state.shared_activity.moments.watch.inspect(watch_id))

    @app.post(prefix + '/watch/heartbeat')
    def watch_heartbeat(payload: WatchLease):
        return _json(app.state.shared_activity.moments.watch.heartbeat(payload.watch_id, payload.lease))

    @app.post(prefix + '/watch/stop')
    def watch_stop(payload: WatchLease):
        return _json(app.state.shared_activity.moments.watch.stop(payload.watch_id, payload.lease))

    @app.post(prefix + '/watch/moment')
    def watch_moment(payload: WatchImage):
        return _json(app.state.shared_activity.moments.watch.share(payload.watch_id, payload.lease, payload.request_id,
                      payload.metadata.model_dump(), _image_bytes(payload), payload.media_type))

    @app.post(prefix + '/review')
    def review(payload: PrivateShared):
        return _json(app.state.shared_activity.review(payload.request_id))

    @app.get(prefix + '/minecraft/status')
    def game_status(audience: Literal['owner_private']):
        return _json(_game(app).status())

    @app.post(prefix + '/minecraft/permit')
    def permit(payload: GamePermit):
        return _json(_game(app).grant(payload.request_id, seconds=payload.seconds,
                                     maximum_commands=payload.maximum_commands))

    @app.post(prefix + '/minecraft/action')
    async def action(payload: GameAction):
        return _json(await _game(app).execute(payload.request_id, payload.name, payload.arguments))

    @app.post(prefix + '/minecraft/revoke')
    async def revoke(payload: PrivateShared):
        return _json(await _game(app).revoke())

    @app.post(prefix + '/minecraft/trials')
    def prepare_trial(payload: GameTrial):
        return _json(_game(app).skills.prepare(payload.request_id, payload.spec))

    @app.get(prefix + '/minecraft/trials/{trial_id}')
    def inspect_trial(audience: Literal['owner_private'], trial_id: str = ApiPath(pattern=r'^skill-[a-f0-9]{24}$')):
        return _json(_game(app).skills.inspect(trial_id))

    @app.post(prefix + '/minecraft/trials/run')
    async def run_trial(payload: GameTrialRun):
        return _json(await _game(app).skills.run(payload.trial_id, payload.scenario_id, payload.request_id))


def add_dashboard_routes(app, client):
    prefix, backend = '/api/coordination/shared-activity', '/v1/coordination/shared-activity'

    @app.get(prefix + '/status')
    def status(audience: Literal['owner_private']):
        return _json(client.request_json('GET', backend + '/status', params={'audience': audience}))

    @app.post(prefix + '/control')
    def control(payload: SharedControl):
        return _json(client.request_json('POST', backend + '/control', json_payload=payload.model_dump()))

    @app.post(prefix + '/observe')
    def observe(payload: SharedObserve):
        return _json(client.request_json('POST', backend + '/observe', json_payload=payload.model_dump()))

    @app.post(prefix + '/image')
    def image(payload: SharedImage):
        return _json(client.request_json('POST', backend + '/image', json_payload=payload.model_dump(), timeout=180))

    @app.post(prefix + '/moment')
    def moment(payload: SharedImage):
        return _json(client.request_json('POST', backend + '/moment', json_payload=payload.model_dump(), timeout=60))

    @app.get(prefix + '/moments/{moment_id}')
    def inspect_moment(audience: Literal['owner_private'], moment_id: str = ApiPath(pattern=r'^[A-Za-z0-9_-]{1,80}$')):
        return _json(client.request_json('GET', backend + '/moments/' + moment_id, params={'audience': audience}))

    @app.post(prefix + '/moment/received')
    def moment_received(payload: MomentReceived):
        return _json(client.request_json('POST', backend + '/moment/received', json_payload=payload.model_dump()))

    @app.post(prefix + '/watch/start')
    def watch_start(payload: WatchStart):
        return _json(client.request_json('POST', backend + '/watch/start', json_payload=payload.model_dump()))

    @app.get(prefix + '/watch/{watch_id}')
    def inspect_watch(audience: Literal['owner_private'], watch_id: str = ApiPath(pattern=r'^watch-[a-f0-9]{32}$')):
        return _json(client.request_json('GET', backend + '/watch/' + watch_id, params={'audience': audience}))

    @app.post(prefix + '/watch/heartbeat')
    def watch_heartbeat(payload: WatchLease):
        return _json(client.request_json('POST', backend + '/watch/heartbeat', json_payload=payload.model_dump()))

    @app.post(prefix + '/watch/stop')
    def watch_stop(payload: WatchLease):
        return _json(client.request_json('POST', backend + '/watch/stop', json_payload=payload.model_dump()))

    @app.post(prefix + '/watch/moment')
    def watch_moment(payload: WatchImage):
        return _json(client.request_json('POST', backend + '/watch/moment', json_payload=payload.model_dump(), timeout=60))

    @app.post(prefix + '/review')
    def review(payload: PrivateShared):
        return _json(client.request_json('POST', backend + '/review', json_payload=payload.model_dump(), timeout=180))

    @app.get(prefix + '/minecraft/status')
    def game_status(audience: Literal['owner_private']):
        return _json(client.request_json('GET', backend + '/minecraft/status', params={'audience': audience}))

    @app.post(prefix + '/minecraft/permit')
    def permit(payload: GamePermit):
        return _json(client.request_json('POST', backend + '/minecraft/permit', json_payload=payload.model_dump()))

    @app.post(prefix + '/minecraft/action')
    def action(payload: GameAction):
        return _json(client.request_json('POST', backend + '/minecraft/action', json_payload=payload.model_dump()))

    @app.post(prefix + '/minecraft/revoke')
    def revoke(payload: PrivateShared):
        return _json(client.request_json('POST', backend + '/minecraft/revoke', json_payload=payload.model_dump()))

    @app.post(prefix + '/minecraft/trials')
    def prepare_trial(payload: GameTrial):
        return _json(client.request_json('POST', backend + '/minecraft/trials', json_payload=payload.model_dump()))

    @app.get(prefix + '/minecraft/trials/{trial_id}')
    def inspect_trial(audience: Literal['owner_private'], trial_id: str = ApiPath(pattern=r'^skill-[a-f0-9]{24}$')):
        return _json(client.request_json('GET', backend + '/minecraft/trials/' + trial_id, params={'audience': audience}))

    @app.post(prefix + '/minecraft/trials/run')
    def run_trial(payload: GameTrialRun):
        return _json(client.request_json('POST', backend + '/minecraft/trials/run', json_payload=payload.model_dump()))
