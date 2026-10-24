import asyncio
import json
from uuid import uuid4

import pytest
import websockets

from gamma.config import settings
from gamma.delivery.authority import DeliveryDenied
from gamma.delivery.playback import PlaybackLedger
from gamma.performer.bus import PerformerEventBus
from gamma.performer.models import PerformerOutputEvent
from gamma.performer.vtube_studio import VTubeStudioAdapter, VTubeStudioAdapterConfig
from gamma.presence import presence_state_for_mode
from gamma.schemas.response import AssistantResponse
from state_fixtures import commit_response


@pytest.fixture
def vtube_delivery(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'delivery.db'}")
    ledger = PlaybackLedger(database_url=f"sqlite:///{tmp_path / 'delivery.db'}")
    state = presence_state_for_mode("go_live")
    bus = PerformerEventBus()
    monkeypatch.setattr("gamma.presence.load_presence_state", lambda **kwargs: state)
    monkeypatch.setattr("gamma.delivery.execution.get_playback_ledger", lambda: ledger)
    monkeypatch.setattr("gamma.performer.bus.get_performer_event_bus", lambda: bus)
    response = AssistantResponse(spoken_text="Fixture", emotion="happy", motions=["wave"])
    commit_response(ledger, response)
    context = dict(target="stream_public", audience="public", policy="fixture")
    grant = ledger.issue(response, **context)
    ledger.dispatch(grant, response, **context)
    event = PerformerOutputEvent(type="expression_set", turn_id=response.route_trace_id,
        target_policy="stream_public", payload={"expression": "happy", "delivery_grant": grant})
    return ledger, grant, event, state, bus


@pytest.mark.parametrize("change", ["cancel", "presence", "policy", "mute"])
def test_real_websocket_rechecks_after_delayed_authentication(vtube_delivery, monkeypatch, change):
    ledger, grant, event, state, bus = vtube_delivery
    async def run():
        auth_seen, release = asyncio.Event(), asyncio.Event()
        received = []
        async def fixture(socket):
            try:
                async for raw in socket:
                    request = json.loads(raw)
                    received.append(request["messageType"])
                    if request["messageType"] == "AuthenticationRequest":
                        auth_seen.set()
                        await release.wait()
                        await socket.send(json.dumps({"messageType": "AuthenticationResponse", "data": {"authenticated": True}}))
                    else:
                        await socket.send(json.dumps({"messageType": "HotkeyTriggerResponse", "data": {}}))
            except websockets.exceptions.ConnectionClosed:
                pass
        async with websockets.serve(fixture, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            adapter = VTubeStudioAdapter(VTubeStudioAdapterConfig(enabled=True,
                endpoint=f"ws://127.0.0.1:{port}", auth_token="fictional-fixture-token", expression_hotkeys={"happy": "fixture-happy"}))
            pending = asyncio.create_task(adapter.handle_event_async(event))
            try:
                await asyncio.wait_for(auth_seen.wait(), 3)
                assert received == ["AuthenticationRequest"]
                if change == "cancel":
                    ledger.cancel(grant)
                elif change == "presence":
                    state["mode"] = "sleep"
                elif change == "policy":
                    monkeypatch.setattr(settings, "speech_filter_llm_enabled", not settings.speech_filter_llm_enabled)
                else:
                    bus.set_target_muted("stream_public", True)
                release.set()
                result = await asyncio.wait_for(pending, 3)
                assert not result.ok
                assert received == ["AuthenticationRequest"]
            finally:
                release.set()
                await adapter.client.close()
    asyncio.run(run())


def test_real_websocket_sends_valid_once_rejects_new_event_id_and_chunk(vtube_delivery):
    ledger, grant, event, state, bus = vtube_delivery
    async def run():
        received = []
        async def fixture(socket):
            async for raw in socket:
                request = json.loads(raw)
                received.append(request)
                if request["messageType"] == "AuthenticationRequest":
                    reply = {"messageType": "AuthenticationResponse", "data": {"authenticated": True}}
                else:
                    reply = {"messageType": "HotkeyTriggerResponse", "data": {}}
                await socket.send(json.dumps(reply))
        async with websockets.serve(fixture, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            adapter = VTubeStudioAdapter(VTubeStudioAdapterConfig(enabled=True,
                endpoint=f"ws://127.0.0.1:{port}", auth_token="fictional-fixture-token", expression_hotkeys={"happy": "fixture-happy"}))
            try:
                assert (await adapter.handle_event_async(event)).ok
                for chunk in (None, 2):
                    duplicate = event.model_copy(deep=True, update={"event_id": uuid4().hex})
                    duplicate.payload["chunk_index"] = chunk
                    duplicate.payload["text"] = "Fixture" if chunk else ""
                    with pytest.raises(DeliveryDenied, match="already_claimed"):
                        await adapter.handle_event_async(duplicate)
                assert [r["messageType"] for r in received] == ["AuthenticationRequest", "HotkeyTriggerRequest"]
            finally:
                await adapter.client.close()
    asyncio.run(run())
