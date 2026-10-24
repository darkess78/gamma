import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlmodel import Session

from gamma.api import routes
from gamma.delivery.authority import DeliveryDenied
from gamma.delivery.playback import PlaybackLedger
from gamma.delivery.protocol import validate_ack
from gamma.memory.state_authority import playback_receipts
from gamma.performer.bus import PerformerEventBus
from gamma.performer.models import PerformerOutputEvent
from gamma.presence import presence_state_for_mode
from gamma.schemas.response import AssistantResponse
from state_fixtures import commit_response


def ack(lease, event, status):
    return dict(type="playback_ack", schema_version=1, lease_id=lease, event_id=event, status=status)


def permit(ledger, lease, receiver, event_id, **kwargs):
    return ledger.prepare(lease, receiver,
        dict(type="playback_prepare", schema_version=1, lease_id=lease, event_id=event_id),
        eligible=kwargs.get("eligible", lambda target: True),
        target_epoch=kwargs.get("target_epoch", lambda target: 0), presence=kwargs.get("presence", ""))


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    ledger = PlaybackLedger(database_url=f"sqlite:///{tmp_path / 'playback.db'}")
    state = presence_state_for_mode("go_live")
    monkeypatch.setattr("gamma.presence.load_presence_state", lambda **kwargs: state)
    monkeypatch.setattr("gamma.delivery.execution.get_playback_ledger", lambda: ledger)
    return ledger


def event(ledger, target="stream_public"):
    response = AssistantResponse(spoken_text="Fictional speech")
    commit_response(ledger, response)
    audio = ledger.artifact_root.parent / "synthetic.wav"
    audio.write_bytes(b"synthetic private playback fixture")
    response.audio_path = str(audio)
    response.audio_content_type = "audio/wav"
    context = dict(target=target, audience="public", policy="fixture")
    grant = ledger.issue(response, **context)
    ledger.dispatch(grant, response, **context)
    return PerformerOutputEvent(type="speech_chunk_ready", turn_id=response.route_trace_id, target_policy=target,
        payload={"audio_url": response.tts_metadata["delivery_audio_url"], "text": response.display_text,
            "delivery_grant": grant}).model_dump()


def test_receipts_require_actual_start_and_correct_live_receiver(ledger):
    first = ledger.open(receiver="one", target="stream_public")
    second = ledger.open(receiver="two", target="stream_public")
    item = event(ledger)
    ledger.offer(first, "one", item)
    with pytest.raises(DeliveryDenied):
        ledger.acknowledge(second, "two", ack(first, item["event_id"], "started"))
    with pytest.raises(ValueError):
        ledger.acknowledge(first, "one", ack(first, item["event_id"], "completed"))
    with pytest.raises(DeliveryDenied):
        ledger.acknowledge(first, "one", ack(first, item["event_id"], "started"))
    permit(ledger, first, "one", item["event_id"])
    assert ledger.acknowledge(first, "one", ack(first, item["event_id"], "started"))["status"] == "started"
    assert ledger.acknowledge(first, "one", ack(first, item["event_id"], "completed"))["status"] == "completed"
    assert ledger.acknowledge(first, "one", ack(first, item["event_id"], "completed"))["status"] == "completed"
    with pytest.raises(DeliveryDenied):
        ledger.offer(first, "one", item)


def test_disconnect_expiry_restart_and_replay_never_complete_playback(ledger, monkeypatch):
    lease = ledger.open(receiver="one", target="stream_public")
    item = event(ledger)
    with pytest.raises(DeliveryDenied):
        ledger.offer(lease, "one", {**item, "replay": True})
    ledger.offer(lease, "one", item)
    restarted = PlaybackLedger(database_url=str(ledger.engine.url), generation="replacement")
    with pytest.raises(DeliveryDenied):
        restarted.acknowledge(lease, "one", ack(lease, item["event_id"], "started"))
    ledger.close(lease, "one")
    with Session(ledger.engine) as session:
        assert session.execute(select(playback_receipts.c.status)).scalar() == "interrupted"
    with pytest.raises(DeliveryDenied):
        ledger.acknowledge(lease, "one", ack(lease, item["event_id"], "completed"))
    fresh = ledger.open(receiver="two", target="stream_public")
    ledger.offer(fresh, "two", item)
    monkeypatch.setattr("gamma.delivery.playback.time.time", lambda: 10**12)
    with pytest.raises(DeliveryDenied):
        ledger.renew(fresh, "two")
    with pytest.raises(DeliveryDenied):
        ledger.acknowledge(fresh, "two", ack(fresh, item["event_id"], "started"))


def test_protocol_rejects_oversized_or_unknown_fields():
    for update in ({"lease_id": "a" * 129}, {"extra": 1}, {"schema_version": True}, {"status": []}):
        with pytest.raises(ValueError):
            validate_ack({**ack("lease", "event", "started"), **update})


@pytest.mark.parametrize("change", ["mute", "epoch", "presence"])
def test_prepare_rechecks_context_after_offer(ledger, change):
    lease = ledger.open(receiver="one", target="stream_public")
    item = event(ledger)
    ledger.offer(lease, "one", item)
    options = {"eligible": lambda target: False} if change == "mute" else (
        {"target_epoch": lambda target: 1} if change == "epoch" else {"presence": "changed audience"})
    with pytest.raises(DeliveryDenied):
        permit(ledger, lease, "one", item["event_id"], **options)


def test_start_permit_expires_and_cannot_repeat(ledger, monkeypatch):
    lease = ledger.open(receiver="one", target="stream_public")
    item = event(ledger)
    ledger.offer(lease, "one", item)
    permission = permit(ledger, lease, "one", item["event_id"])
    with pytest.raises(DeliveryDenied):
        permit(ledger, lease, "one", item["event_id"])
    monkeypatch.setattr("gamma.delivery.playback.time.time", lambda: permission["expires_at"] + 1)
    with pytest.raises(DeliveryDenied):
        ledger.acknowledge(lease, "one", ack(lease, item["event_id"], "started"))


def test_websocket_receipts_and_reconnect_audio_suppression(ledger, monkeypatch):
    bus = PerformerEventBus()
    monkeypatch.setattr(routes, "get_playback_ledger", lambda: ledger)
    monkeypatch.setattr(routes, "get_performer_bus", lambda: bus)
    monkeypatch.setattr(routes, "_websocket_api_auth_ok", lambda _: True)
    presence = presence_state_for_mode("go_live")
    monkeypatch.setattr(routes, "load_presence_state", lambda: presence)
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    endpoint = "/v1/performer/events?target_policy=stream_public"
    with client.websocket_connect(endpoint) as one, client.websocket_connect(endpoint) as two:
        first, second = one.receive_json(), two.receive_json()
        for connection in (one, two):
            connection.send_json(dict(type="subscriber_capabilities", audio_ready=True, text_ready=True))
            assert connection.receive_json()["type"] == "subscriber_capabilities"
        item = event(ledger)
        bus.publish(PerformerOutputEvent(**item))
        sent_one, sent_two = one.receive_json(), two.receive_json()
        assert sent_one["playback"]["lease_id"] != sent_two["playback"]["lease_id"]
        two.send_json(ack(first["playback_lease"]["lease_id"], item["event_id"], "started"))
        assert two.receive_json()["type"] == "playback_rejected"
        two.send_json(dict(type="playback_prepare", schema_version=1,
            lease_id=second["playback_lease"]["lease_id"], event_id=item["event_id"]))
        assert two.receive_json()["type"] == "playback_permit"
        for status in ("started", "completed"):
            two.send_json(ack(second["playback_lease"]["lease_id"], item["event_id"], status))
            assert two.receive_json()["status"] == status
    with client.websocket_connect(endpoint + "&replay_recent=10") as reconnect:
        assert reconnect.receive_json()["type"] == "ready"
        replay = reconnect.receive_json()
        assert replay["replay"] and "audio_url" not in replay["payload"] and "playback" not in replay
    with Session(ledger.engine) as session:
        statuses = list(session.execute(select(playback_receipts.c.status)).scalars())
        assert sorted(statuses) == ["completed", "interrupted"]


def test_muting_after_queueing_revokes_send_permission(ledger):
    import asyncio

    async def scenario():
        bus = PerformerEventBus()
        receiver, queue = await bus.subscribe(target_policy="stream_public")
        bus.update_subscriber_capabilities(receiver, audio_ready=True)
        bus.publish(PerformerOutputEvent(**event(ledger)))
        assert bus.audio_delivery_allowed(receiver, "stream_public")
        bus.set_target_muted("stream_public", True)
        queued = await queue.get()
        assert queued["type"] == "speech_chunk_ready"
        assert not bus.audio_delivery_allowed(receiver, queued["target_policy"])
        bus.unsubscribe(receiver)
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["sleep", "audience", "mute_then_unmute", "clear"])
def test_websocket_start_denied_when_queued_context_changes(ledger, monkeypatch, change):
    bus = PerformerEventBus()
    presence = presence_state_for_mode("go_live")
    monkeypatch.setattr(routes, "get_playback_ledger", lambda: ledger)
    monkeypatch.setattr(routes, "get_performer_bus", lambda: bus)
    monkeypatch.setattr(routes, "load_presence_state", lambda: presence)
    monkeypatch.setattr(routes, "_websocket_api_auth_ok", lambda _: True)
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app).websocket_connect("/v1/performer/events?target_policy=dashboard_monitor") as connection:
        ready = connection.receive_json()
        lease = ready["playback_lease"]["lease_id"]
        connection.send_json(dict(type="subscriber_capabilities", audio_ready=True, text_ready=True))
        assert connection.receive_json()["type"] == "subscriber_capabilities"
        item = event(ledger, target="dashboard_monitor")
        bus.publish(PerformerOutputEvent(**item))
        assert connection.receive_json()["playback"]["lease_id"] == lease
        if change == "sleep":
            presence.update(presence_state_for_mode("sleep"))
        elif change == "audience":
            presence["audience"] = {"kind": "another audience"}
        elif change == "clear":
            bus.clear_target("dashboard_monitor")
            assert connection.receive_json()["type"] == "output_cleared"
        else:
            bus.set_target_muted("dashboard_monitor", True)
            assert connection.receive_json()["type"] == "output_cleared"
            bus.set_target_muted("dashboard_monitor", False)
            assert connection.receive_json()["type"] == "target_mute_changed"
        connection.send_json(dict(type="playback_prepare", schema_version=1, lease_id=lease, event_id=item["event_id"]))
        assert connection.receive_json()["type"] == "playback_rejected"
        connection.send_json(ack(lease, item["event_id"], "failed"))
        assert connection.receive_json()["type"] == "playback_rejected"
        with Session(ledger.engine) as session:
            assert session.execute(select(playback_receipts.c.status).where(
                playback_receipts.c.lease_id == lease)).scalar_one() == "interrupted"


def test_receiver_admission_bounds_pending_revocation_work(ledger):
    lease = ledger.open(receiver="one", target="stream_public")
    items = [event(ledger) for _ in range(129)]
    for item in items[:128]:
        ledger.offer(lease, "one", item)
    with pytest.raises(DeliveryDenied, match="queue_full"):
        ledger.offer(lease, "one", items[-1])
    ledger.acknowledge(lease, "one", ack(lease, items[0]["event_id"], "failed"))
    ledger.offer(lease, "one", items[-1])


@pytest.mark.parametrize("change", ["audience", "mute", "clear"])
def test_active_websocket_playback_is_revoked_without_client_request(ledger, monkeypatch, change):
    bus = PerformerEventBus()
    presence = presence_state_for_mode("go_live")
    monkeypatch.setattr(routes, "get_playback_ledger", lambda: ledger)
    monkeypatch.setattr(routes, "get_performer_bus", lambda: bus)
    monkeypatch.setattr(routes, "load_presence_state", lambda: presence)
    monkeypatch.setattr(routes, "_websocket_api_auth_ok", lambda _: True)
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app).websocket_connect("/v1/performer/events?target_policy=stream_public") as connection:
        lease = connection.receive_json()["playback_lease"]["lease_id"]
        connection.send_json(dict(type="subscriber_capabilities", audio_ready=True, text_ready=True))
        assert connection.receive_json()["type"] == "subscriber_capabilities"
        item = event(ledger)
        bus.publish(PerformerOutputEvent(**item))
        assert connection.receive_json()["playback"]["event_id"] == item["event_id"]
        connection.send_json(dict(type="playback_prepare", schema_version=1, lease_id=lease, event_id=item["event_id"]))
        assert connection.receive_json()["type"] == "playback_permit"
        connection.send_json(ack(lease, item["event_id"], "started"))
        assert connection.receive_json()["status"] == "started"
        if change == "audience":
            presence["audience"] = {"kind": "changed"}
        elif change == "mute":
            bus.set_target_muted("stream_public", True)
            assert connection.receive_json()["type"] == "output_cleared"
        else:
            bus.clear_target("stream_public")
            assert connection.receive_json()["type"] == "output_cleared"
        revoked = connection.receive_json()
        assert revoked == dict(type="playback_revoked", lease_id=lease, event_id=item["event_id"])
        connection.send_json(ack(lease, item["event_id"], "completed"))
        assert connection.receive_json()["type"] == "playback_rejected"
    with Session(ledger.engine) as session:
        assert session.execute(select(playback_receipts.c.status)).scalar_one() == "interrupted"


@pytest.mark.parametrize("closed_transport", [True, False])
def test_transport_send_race_closes_receiver_lease(ledger, monkeypatch, closed_transport):
    import asyncio
    from unittest.mock import Mock, AsyncMock
    from gamma.memory.state_authority import playback_leases
    async def exercise():
        queue = asyncio.Queue()
        queue.put_nowait({"type": "subtitle_update", "replay": True, "payload": {"text": "fixture"}})
        bus = Mock()
        bus.subscribe = AsyncMock(return_value=("receiver-fixture", queue))
        bus.stats.return_value = {}
        bus.replay_window.return_value = {}
        bus.replay_gap_after.return_value = None
        socket = Mock()
        socket.client = None
        socket.accept = AsyncMock()
        failure = ("Unexpected ASGI message 'websocket.send', after sending 'websocket.close' or response already completed."
                   if closed_transport else "unrelated programming failure")
        socket.send_json = AsyncMock(side_effect=[None, RuntimeError(failure)])
        async def receive(): await asyncio.Event().wait()
        socket.receive_json = receive
        monkeypatch.setattr(routes, "get_playback_ledger", lambda: ledger)
        monkeypatch.setattr(routes, "get_performer_bus", lambda: bus)
        monkeypatch.setattr(routes, "_websocket_api_auth_ok", lambda _: True)
        if closed_transport:
            await routes.performer_events(socket)
        else:
            with pytest.raises(RuntimeError, match="unrelated programming"):
                await routes.performer_events(socket)
        bus.unsubscribe.assert_called_once_with("receiver-fixture")
        with Session(ledger.engine) as session:
            assert session.execute(select(playback_leases.c.status)).scalar_one() == "closed"
    asyncio.run(exercise())
