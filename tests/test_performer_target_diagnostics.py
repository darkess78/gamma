"""Target diagnostics omit connection rows while operator inspection stays global."""
from contextlib import ExitStack
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from gamma.config import settings
from gamma.main import app
from gamma.api import routes
from gamma.performer.bus import PerformerEventBus
from gamma.performer.models import DEFAULT_TARGET_POLICY, KNOWN_TARGET_POLICIES, PerformerOutputEvent

TOKEN = "synthetic-target-diagnostics-token"
AUTH = {"Authorization": "Bearer " + TOKEN}
SELECTORS = ["stream_public", "discord_call", "dashboard_monitor",
             "unknown_target", "", " \t ", " DiScOrD_CaLl ", " UnKnOwN_TaRgEt "]
STAT_KEYS = {"subscriber_count", "history_count", "last_sequence", "target_policies",
             "subscribers_by_target", "subscribers", "muted_targets"}

@pytest.fixture
def output_state(tmp_path, monkeypatch):
    bus = PerformerEventBus(history_limit=12, state_path=tmp_path/"bus-state.json")
    monkeypatch.setattr(settings, "api_auth_enabled", True)
    monkeypatch.setattr(settings, "api_bearer_token", TOKEN)
    monkeypatch.setattr(routes, "get_performer_bus", lambda: bus)
    for name in ("get_vtube_studio_adapter", "get_vtube_studio_runner", "get_discord_runtime"):
        monkeypatch.setattr(routes, name, lambda: SimpleNamespace(status=lambda: {"synthetic": True}))
    for target, count in (("stream_public", 2), ("discord_call", 2),
                          ("dashboard_monitor", 2), ("unknown_target", 1)):
        for index in range(count):
            bus.publish(PerformerOutputEvent(type="subtitle_update", turn_id=f"{target}-{index}",
                target_policy=target, payload={"text": f"{target} synthetic {index}"}))
    return bus

def selected(selector):
    return selector.strip().lower() or DEFAULT_TARGET_POLICY

def ws_path(target, **kwargs):
    return "/v1/performer/events?" + urlencode({"target_policy": target, **kwargs})

def recent_path(target):
    return "/v1/performer/events/recent?" + urlencode({"target_policy": target})

def aggregate(stats, target, connections, history, sequence, muted=False):
    assert set(stats) == STAT_KEYS
    assert stats["subscribers"] == []
    assert stats["subscriber_count"] == connections
    assert stats["subscribers_by_target"] == ({target: connections} if connections else {})
    assert stats["history_count"] == history
    assert stats["last_sequence"] == sequence
    assert stats["target_policies"] == list(KNOWN_TARGET_POLICIES)
    assert stats["muted_targets"] == ([target] if muted else [])

@pytest.mark.parametrize("selector", SELECTORS)
def test_actual_target_ready_and_http_omit_foreign_and_peer_connections(output_state, selector):
    bus = output_state
    target = selected(selector)
    visible = {"stream_public": 2, "discord_call": 2, "dashboard_monitor": 4, "unknown_target": 1}[target]
    connections = 5 if target == "dashboard_monitor" else 3
    # Each WebSocket records its own real ASGI client.host; no model response supplies metadata.
    with ExitStack() as stack:
        peers = []
        for index, policy in enumerate(("dashboard_monitor", "dashboard_monitor", target, target)):
            client = TestClient(app, client=(f"10.55.0.{index+10}", 42000+index))
            ws = stack.enter_context(client.websocket_connect(
                ws_path(policy, client_name=f"peer_private_name_{index}"), headers=AUTH))
            ready = ws.receive_json()
            assert ready["type"] == "ready"
            peers.append(ready)
        client = TestClient(app, client=("10.55.0.99", 42099))
        ws = stack.enter_context(client.websocket_connect(
            ws_path(selector, client_name="Selected_Self", replay_recent=100, token=TOKEN)))
        ready = ws.receive_json()
        assert ready["target_policy"] == target
        assert ready["subscriber_id"]
        assert ready["client_name"] == "selected_self"
        aggregate(ready["stats"], target, connections, visible, 7)
        assert ready["replay_window"] == {"first_sequence": 1, "last_sequence": 7}
        assert ready["replay_gap"] is False
        replay = [ws.receive_json() for _ in range(visible)]
        expected_targets = {target, "stream_public"} if target == "dashboard_monitor" else {target}
        assert {row["target_policy"] for row in replay} == expected_targets
        assert all("synthetic" in row["payload"]["text"] for row in replay)
        http = client.get(recent_path(selector), headers=AUTH)
        assert http.status_code == 200
        aggregate(http.json()["stats"], target, connections, visible, 7)
        assert [row["sequence"] for row in http.json()["items"]] == [row["sequence"] for row in replay]

        # Own acknowledgement remains available, without exposing peer rows.
        ws.send_json({"type": "subscriber_capabilities", "text_ready": False, "audio_ready": True})
        ack = ws.receive_json()
        assert ack == {"type": "subscriber_capabilities", "capabilities": {
            "subscriber_id": ready["subscriber_id"], "target_policy": target,
            "text_ready": False, "audio_ready": True}}
        status = client.get("/v1/performer/status", headers=AUTH)
        assert status.status_code == 200
        global_stats = status.json()["stats"]
        assert global_stats["subscriber_count"] == 5
        assert global_stats["history_count"] == 7
        assert len(global_stats["subscribers"]) == 5
        own = next(row for row in global_stats["subscribers"] if row["subscriber_id"] == ready["subscriber_id"])
        assert own["client_host"] == "10.55.0.99" and own["client_name"] == "selected_self"
        assert own["text_ready"] is False and own["audio_ready"] is True
        assert own["connected_at"]
        assert {row["client_name"] for row in global_stats["subscribers"]} >= {
            "peer_private_name_0", "peer_private_name_1", "peer_private_name_2", "peer_private_name_3"}
        assert {row["client_host"] for row in global_stats["subscribers"]} >= {
            "10.55.0.10", "10.55.0.11", "10.55.0.12", "10.55.0.13"}
        operator = client.get("/v1/performer/events/recent", headers=AUTH)
        assert operator.status_code == 200
        assert len(operator.json()["items"]) == 7
        assert operator.json()["stats"] == bus.stats()
        aggregate(bus.stats(target_policy=selector), target, connections, visible, 7)

        bus.publish(PerformerOutputEvent(type="subtitle_update", turn_id="live-selected",
            target_policy=target, payload={"text": "live selected presentation"}))
        live = ws.receive_json()
        assert live["sequence"] == 8 and live["payload"]["text"] == "live selected presentation"
        # A foreign event must not enter this target's live queue.
        if target != "dashboard_monitor":
            bus.publish(PerformerOutputEvent(type="subtitle_update", turn_id="live-foreign",
                target_policy="dashboard_monitor", payload={"text": "foreign private presentation"}))
        ws.send_json({"type": "subscriber_capabilities", "text_ready": True})
        assert ws.receive_json()["type"] == "subscriber_capabilities"
    assert bus.stats()["subscriber_count"] == 0
    assert bus.stats()["subscribers"] == []
    assert bus.stats(target_policy=selector)["subscribers_by_target"] == {}

@pytest.mark.parametrize("selector", ["stream_public", "discord_call", "dashboard_monitor", "unknown_target", " \t "])
def test_actual_mute_replay_sequence_gap_and_controls(output_state, selector):
    bus = output_state
    target = selected(selector)
    bus.set_target_muted(target, True, reason="synthetic-target-test")
    bus.publish(PerformerOutputEvent(type="subtitle_update", turn_id="hidden-own",
        target_policy=target, payload={"text": "hidden selected presentation"}))
    expected = 3 if target == "dashboard_monitor" else 1  # Monitor also retains the two public events.
    with TestClient(app).websocket_connect(ws_path(selector, replay_recent=100, after_sequence=0),
                                           headers=AUTH) as ws:
        ready = ws.receive_json()
        aggregate(ready["stats"], target, 1, expected, 9, muted=True)
        rows = [ws.receive_json() for _ in range(expected)]
        assert all(row["turn_id"] != "hidden-own" for row in rows)
        assert rows[-1]["type"] == "output_cleared"
        client = TestClient(app)
        result = client.get(recent_path(selector), headers=AUTH).json()
        aggregate(result["stats"], target, 1, expected, 9, muted=True)
        assert [r["sequence"] for r in result["items"]] == [r["sequence"] for r in rows]
        control = bus.set_target_muted(target, False, reason="synthetic-target-test")
        assert control["stats"] == bus.stats()  # operator control remains full/global
        assert ws.receive_json()["type"] == "target_mute_changed"
        bus.publish(PerformerOutputEvent(type="subtitle_update", turn_id="after-unmute",
            target_policy=target, payload={"text": "restored selected presentation"}))
        assert ws.receive_json()["payload"]["text"] == "restored selected presentation"
        # Rotated history retains global replay-window/gap behavior.
        for index in range(13):
            bus.publish(PerformerOutputEvent(type="expression_set", turn_id=f"rotate-{index}",
                target_policy="unrelated_target", payload={"emotion":"neutral"}))
        assert bus.replay_window() == {"first_sequence": 13, "last_sequence": 24}
        assert bus.replay_gap_after(0) is True
        assert bus.replay_gap_after(12) is False
        result = client.get(recent_path(selector)+"&after_sequence=0", headers=AUTH).json()
        assert result["items"] == []
        aggregate(result["stats"], target, 1, 0, 24)
        assert result["replay"]["gap"] is True
        # Other-target mute state is not included in this target view.
        foreign = "discord_call" if target != "discord_call" else "stream_public"
        bus.set_target_muted(foreign, True, reason="synthetic-foreign-mute")
        assert bus.stats(target_policy=selector)["muted_targets"] == []
        assert foreign in bus.stats()["muted_targets"]
    assert bus.stats()["subscriber_count"] == 0

def test_monitor_history_keeps_muted_public_events_for_operator_delivery(output_state):
    bus = output_state
    bus.set_target_muted("stream_public", True, reason="synthetic")
    # Existing mute behavior hides muted public speech from public, but Monitor still receives it.
    with TestClient(app).websocket_connect(ws_path("dashboard_monitor", replay_recent=100), headers=AUTH) as ws:
        ready = ws.receive_json()
        aggregate(ready["stats"], "dashboard_monitor", 1, 5, 8)
        replay = [ws.receive_json() for _ in range(5)]
        assert len([r for r in replay if r["target_policy"] == "stream_public"]) == 3
        bus.publish(PerformerOutputEvent(type="subtitle_update", turn_id="muted-public-live",
            target_policy="stream_public", payload={"text":"monitor still sees public"}))
        assert ws.receive_json()["payload"]["text"] == "monitor still sees public"
        aggregate(bus.stats(target_policy="stream_public"), "stream_public", 0, 1, 9, muted=True)

@pytest.mark.parametrize("headers", [{}, {"Authorization":"Bearer wrong"}])
def test_anonymous_and_wrong_credentials_still_denied(output_state, headers):
    client = TestClient(app)
    for url in ("/v1/performer/status", "/v1/performer/events/recent", recent_path("dashboard_monitor")):
        assert client.get(url, headers=headers).status_code == 401
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(ws_path("stream_public"), headers=headers):
            pass
    assert exc.value.code == 1008
    assert output_state.stats()["subscriber_count"] == 0
