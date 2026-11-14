"""Games written for the Neuro SDK can play with Shana through /v1/games/neuro."""
import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from gamma.config import settings
from gamma.games import neuro_protocol as neuro
from gamma.main import app

CARD_SCHEMA = {"type": "object", "properties": {"card": {"type": "integer", "minimum": 0, "maximum": 4}},
               "required": ["card"]}


@pytest.fixture
def games(monkeypatch):
    monkeypatch.setattr(settings, "games_neuro_enabled", True)
    monkeypatch.setattr(settings, "games_neuro_key", "test-game-key")
    monkeypatch.setattr(settings, "games_neuro_chooser", "random")
    return TestClient(app)


def send(ws, command, data=None):
    message = {"command": command, "game": "Card Test"}
    if data is not None:
        message["data"] = data
    ws.send_text(json.dumps(message))


def test_wrong_key_or_disabled_is_refused(games, monkeypatch):
    with pytest.raises(WebSocketDisconnect):
        with games.websocket_connect("/v1/games/neuro?key=nope") as ws:
            ws.receive_text()
    monkeypatch.setattr(settings, "games_neuro_enabled", False)
    with pytest.raises(WebSocketDisconnect):
        with games.websocket_connect("/v1/games/neuro?key=test-game-key") as ws:
            ws.receive_text()


def test_a_game_can_register_force_and_get_a_valid_action(games):
    with games.websocket_connect("/v1/games/neuro?key=test-game-key") as ws:
        send(ws, "startup")
        ack = json.loads(ws.receive_text())
        assert ack["command"] == "startup" and ack["data"]["session"]["characterId"] == "shana"
        send(ws, "actions/register", {"actions": [
            {"name": "play_card", "description": "Play a card from your hand", "schema": CARD_SCHEMA},
            {"name": "pass", "description": "Skip your turn"}]})
        send(ws, "context", {"message": "You drew a card.", "silent": True})
        send(ws, "actions/force", {"query": "Play a card.", "state": "Hand: 5 cards", "action_names": ["play_card"]})
        action = json.loads(ws.receive_text())
        assert action["command"] == "action" and action["data"]["name"] == "play_card"
        card = json.loads(action["data"]["data"])["card"]
        assert 0 <= card <= 4
        send(ws, "action/result", {"id": action["data"]["id"], "success": True})
        send(ws, "actions/force", {"query": "Your turn again.", "action_names": ["pass"]})
        again = json.loads(ws.receive_text())
        assert again["data"]["name"] == "pass" and "data" not in again["data"]


class Scripted:
    def __init__(self):
        self.calls = 0

    def choose(self, *, game, force, actions, context):
        self.calls += 1
        return neuro.Choice(actions[0].name, {"card": 1})


def started_session(chooser, clock=lambda: 0.0):
    session = neuro.GameSession(chooser, clock=clock)
    session.receive({"command": "startup", "game": "Card Test"})
    session.receive({"command": "actions/register", "game": "Card Test", "data": {"actions": [
        {"name": "play_card", "description": "Play", "schema": CARD_SCHEMA}]}})
    return session


def test_failed_results_are_retried_then_given_up():
    chooser = Scripted()
    session = started_session(chooser)
    out = session.receive({"command": "actions/force", "game": "Card Test",
                           "data": {"query": "Play.", "action_names": ["play_card"]}})
    for _ in range(neuro.MAX_FORCE_ATTEMPTS - 1):
        out = session.receive({"command": "action/result", "game": "Card Test",
                               "data": {"id": out[0]["data"]["id"], "success": False, "message": "not your turn"}})
        assert out and out[0]["command"] == "action"
    assert session.receive({"command": "action/result", "game": "Card Test",
                            "data": {"id": out[0]["data"]["id"], "success": False}}) == []
    assert session.force is None and chooser.calls == neuro.MAX_FORCE_ATTEMPTS
    assert any("gave up" in line for line in session.context)


def test_one_force_at_a_time_and_late_results_ignored():
    now = [0.0]
    session = started_session(Scripted(), clock=lambda: now[0])
    first = session.receive({"command": "actions/force", "game": "Card Test",
                             "data": {"query": "Play.", "action_names": ["play_card"]}})
    assert session.receive({"command": "actions/force", "game": "Card Test",
                            "data": {"query": "Again?", "action_names": ["play_card"]}}) == []
    assert session.receive({"command": "action/result", "game": "Card Test",
                            "data": {"id": "someone-else", "success": True}}) == []
    now[0] = neuro.RESULT_TIMEOUT_SECONDS + 1
    retried = session.tick()
    assert retried and retried[0]["data"]["id"] != first[0]["data"]["id"]


def test_model_chooser_reads_json_and_falls_back_on_garbage():
    actions = [neuro.Action("pass", "Skip"), neuro.Action("play_card", "Play", CARD_SCHEMA)]
    good = neuro.parse_choice('Sure! {"action": "play_card", "data": {"card": 3}, "say": "Watch this."}', actions)
    assert good == neuro.Choice("play_card", {"card": 3}, "Watch this.")
    assert neuro.parse_choice("I don't know", actions).action == "pass"
