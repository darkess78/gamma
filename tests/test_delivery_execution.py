"""Execution acceptance against isolated state and real loopback sockets."""
import json
import socket
import threading
import time
from pathlib import Path
from urllib.request import urlopen
from urllib.error import HTTPError

import pytest
import uvicorn
from fastapi import FastAPI
from sqlalchemy import select
from websockets.sync.client import connect

from gamma.api import routes
from gamma.delivery.authority import DeliveryDenied
from gamma.delivery.execution import validate_event
from gamma.delivery.playback import PlaybackLedger
from gamma.memory.state_authority import dispatch_attempts, playback_receipts
from gamma.performer.bus import PerformerEventBus
from gamma.performer.models import PerformerOutputEvent
from gamma.presence import presence_state_for_mode
from gamma.schemas.response import AssistantResponse
from state_fixtures import commit_response

REAL_WEBSOCKET_AUTH = routes._websocket_api_auth_ok


@pytest.fixture
def approved(tmp_path, monkeypatch):
    presence = presence_state_for_mode("go_live")
    monkeypatch.setattr("gamma.presence.load_presence_state", lambda **kwargs: presence)
    monkeypatch.setattr(routes, "load_presence_state", lambda **kwargs: presence)
    ledger = PlaybackLedger(database_url=f"sqlite:///{tmp_path / 'execution.db'}")
    bus = PerformerEventBus()
    monkeypatch.setattr("gamma.delivery.execution.get_playback_ledger", lambda: ledger)
    monkeypatch.setattr(routes, "get_playback_ledger", lambda: ledger)
    monkeypatch.setattr(routes, "get_performer_bus", lambda: bus)
    monkeypatch.setattr(routes, "_websocket_api_auth_ok", lambda _: True)
    response = AssistantResponse(spoken_text="Approved socket fixture")
    commit_response(ledger, response)
    source = tmp_path / "fixture.wav"
    source.write_bytes(b"approved isolated audio fixture")
    response.audio_path = str(source)
    response.audio_content_type = "audio/wav"
    context = dict(target="stream_public", audience="public", policy="fixture")
    grant = ledger.issue(response, **context)
    ledger.dispatch(grant, response, **context)
    event = PerformerOutputEvent(type="speech_chunk_ready", turn_id=response.route_trace_id,
        target_policy="stream_public", payload={"delivery_grant": grant,
        "text": response.display_text, "audio_url": response.tts_metadata["delivery_audio_url"]})
    return ledger, bus, event, presence


def test_reconstructed_event_cannot_duplicate_dispatch(approved):
    ledger, bus, event, _ = approved
    bus.publish(event)
    reconstructed = PerformerOutputEvent(**event.model_dump(exclude={"event_id", "sequence"}))
    assert reconstructed.event_id != event.event_id
    with pytest.raises(DeliveryDenied, match="already_claimed"):
        bus.publish(reconstructed)
    assert len(bus.recent()) == 1
    with ledger._session() as session:
        assert session.execute(select(dispatch_attempts.c.status)).scalars().all() == ["dispatched"]


@pytest.mark.parametrize("change", ["text", "target", "url", "cancel", "clear", "mute_unmute", "presence", "policy"])
def test_delayed_event_rechecks_current_approval_before_bus_output(approved, monkeypatch, change):
    ledger, bus, event, presence = approved
    if change == "text":
        event.payload["text"] = "Unapproved replacement"
    elif change == "target":
        event.target_policy = "dashboard_monitor"
    elif change == "url":
        event.payload["audio_url"] = "/legacy.wav"
    elif change == "cancel":
        ledger.cancel(event.payload["delivery_grant"])
    elif change == "clear":
        bus.clear_target(event.target_policy)
    elif change == "mute_unmute":
        bus.set_target_muted(event.target_policy, True)
        bus.set_target_muted(event.target_policy, False)
    elif change == "presence":
        presence.update(presence_state_for_mode("sleep"))
    else:
        from gamma.config import settings
        monkeypatch.setattr(settings, "speech_filter_llm_enabled", not settings.speech_filter_llm_enabled)
    with pytest.raises(DeliveryDenied):
        bus.publish(event)
    assert not any(item.type == "speech_chunk_ready" for item in bus.recent())
    with ledger._session() as session:
        assert session.execute(select(dispatch_attempts)).first() is None


@pytest.fixture
def socket_server(approved):
    app = FastAPI()
    app.include_router(routes.router)
    from gamma.main import require_api_auth
    app.middleware("http")(require_api_auth)
    bound = socket.socket()
    bound.bind(("127.0.0.1", 0))
    port = bound.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [bound]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    try:
        yield port
    finally:
        server.should_exit = True
        thread.join(5)
        bound.close()
        assert not thread.is_alive()


def receive(connection):
    return json.loads(connection.recv(timeout=3))


def send(connection, payload):
    connection.send(json.dumps(payload))


def test_real_http_and_two_websocket_receivers_enforce_grants(approved, socket_server):
    ledger, bus, event, _ = approved
    grant = event.payload["delivery_grant"]
    base = f"http://127.0.0.1:{socket_server}"
    with urlopen(f"{base}/v1/delivery/{grant}/audio", timeout=3) as result:
        assert result.read() == b"approved isolated audio fixture"
        assert result.headers["Cache-Control"] == "no-store"
    with pytest.raises(HTTPError) as legacy:
        urlopen(f"{base}/v1/audio/artifacts/fixture.wav", timeout=3)
    assert legacy.value.code == 410
    endpoint = f"ws://127.0.0.1:{socket_server}/v1/performer/events?target_policy=stream_public"
    with connect(endpoint) as one, connect(endpoint) as two:
        first, second = receive(one), receive(two)
        for connection in (one, two):
            send(connection, dict(type="subscriber_capabilities", audio_ready=True, text_ready=True))
            assert receive(connection)["type"] == "subscriber_capabilities"
        bus.publish(event)
        offer_one, offer_two = receive(one), receive(two)
        first_lease = first["playback_lease"]["lease_id"]
        second_lease = second["playback_lease"]["lease_id"]
        assert offer_one["playback"]["lease_id"] == first_lease
        assert offer_two["playback"]["lease_id"] == second_lease
        assert first_lease != second_lease
        send(two, dict(type="playback_ack", schema_version=1, lease_id=first_lease,
            event_id=event.event_id, status="started"))
        assert receive(two)["type"] == "playback_rejected"
        send(two, dict(type="playback_prepare", schema_version=1, lease_id=second_lease, event_id=event.event_id))
        assert receive(two)["type"] == "playback_permit"
        for status in ("started", "completed"):
            send(two, dict(type="playback_ack", schema_version=1, lease_id=second_lease,
                event_id=event.event_id, status=status))
            assert receive(two)["status"] == status
        ledger.cancel(grant)
        assert receive(one)["type"] == "playback_revoked"
        with pytest.raises(HTTPError) as cancelled:
            urlopen(f"{base}/v1/delivery/{grant}/audio", timeout=3)
        assert cancelled.value.code == 410
    with ledger._session() as session:
        assert sorted(session.execute(select(playback_receipts.c.status)).scalars()) == ["completed", "interrupted"]


@pytest.mark.parametrize("cancel", [False, True])
def test_cli_receiver_real_protocol_and_cancellable_player(approved, socket_server, tmp_path, monkeypatch, cancel):
    from concurrent.futures import ThreadPoolExecutor
    import hashlib
    import shlex
    from gamma.config import settings
    from gamma.voice.approved_playback import ApprovedAudioPlayback
    ledger, bus, _, _ = approved
    monkeypatch.setattr(settings, "shana_bind_host", "127.0.0.1")
    monkeypatch.setattr(settings, "shana_port", socket_server)
    monkeypatch.setattr(settings, "api_auth_enabled", True)
    monkeypatch.setattr(settings, "api_bearer_token", "isolated-synthetic-fixture-token")
    monkeypatch.setattr(routes, "_websocket_api_auth_ok", REAL_WEBSOCKET_AUTH)
    response = AssistantResponse(spoken_text="CLI approved fixture")
    commit_response(ledger, response)
    source = tmp_path / "cli.wav"
    audio = b"exact CLI approved synthetic audio"
    source.write_bytes(audio)
    response.audio_path = str(source)
    response.audio_content_type = "audio/wav"
    context = dict(target="dashboard_monitor", audience="private", policy="fixture")
    grant = ledger.issue(response, **context)
    ledger.dispatch(grant, response, **context)
    with pytest.raises(HTTPError) as unauthenticated:
        urlopen(f"http://127.0.0.1:{socket_server}/v1/delivery/{grant}/audio", timeout=3)
    assert unauthenticated.value.code == 401
    marker = tmp_path / "player-started"
    digest = tmp_path / "player-digest"
    player = tmp_path / "fixture-player"
    duration = "10" if cancel else "0.2"
    # A real child process consumes the exact approved temporary file. No audio
    # device is used; exec makes cancellation target the sleeping player itself.
    player.write_text("#!/bin/sh\nsha256sum \"$1\" > " + shlex.quote(str(digest)) +
        "\nprintf '%s' \"$$\" > " + shlex.quote(str(marker)) + "\nexec sleep " + duration + "\n")
    player.chmod(0o700)
    receiver = ApprovedAudioPlayback(binary=str(player))
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(receiver.play_response, response)
        deadline = time.monotonic() + 5
        while not marker.exists() and not result.done() and time.monotonic() < deadline:
            time.sleep(.01)
        assert marker.exists(), result.result(timeout=1) if result.done() else "player did not start"
        assert digest.read_text().split()[0] == hashlib.sha256(audio).hexdigest()
        if cancel:
            while time.monotonic() < deadline:
                with ledger._session() as session:
                    started = session.execute(select(playback_receipts.c.status).where(
                        playback_receipts.c.status == "started")).first()
                if started:
                    break
                time.sleep(.01)
            assert started
            ledger.cancel(grant)
        assert result.result(timeout=5) is (not cancel)
    with ledger._session() as session:
        assert session.execute(select(playback_receipts.c.status)).scalar_one() == ("interrupted" if cancel else "completed")
    import psutil
    assert not psutil.pid_exists(int(marker.read_text()))
    temporary_audio = Path(digest.read_text().split(maxsplit=1)[1].strip())
    assert not temporary_audio.exists()
    if not cancel:
        with pytest.raises(DeliveryDenied):
            receiver.play_response(response)
