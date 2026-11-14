"""Gamma's game protocol: session rules, param validation, the route and the reference client."""
import json
import random
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from gamma.api import routes_games
from gamma.config import settings
from gamma.games import protocol as gp
from gamma.games.client import GameClient
from gamma.games.examples import tictactoe
from gamma.main import app

KEY = "test-game-key"
CELL = {"type": "object", "properties": {"cell": {"type": "integer", "minimum": 1, "maximum": 9}},
        "required": ["cell"]}


class Scripted:
    """Returns the queued choices in order (the last one repeats)."""

    def __init__(self, *choices, on_choose=None):
        self.choices = list(choices)
        self.calls = []
        self.on_choose = on_choose

    def choose(self, decision, actions, context):
        self.calls.append(decision.last_error)
        if self.on_choose:
            self.on_choose()
        return self.choices.pop(0) if len(self.choices) > 1 else self.choices[0]


class FirstFreeCell:
    """Plays tic-tac-toe by taking the first numbered cell in the state text."""

    def choose(self, decision, actions, context):
        free = [ch for ch in decision.state if ch.isdigit()]
        return gp.Choice("place", {"cell": int(free[0])}, say=f"Cell {free[0]}, obviously.")


def started(chooser, clock=lambda: 0.0):
    session = gp.GameSession(chooser, clock=clock)
    welcome = session.receive({"type": "hello", "protocol": 1, "game": "Test"})
    assert welcome[0]["type"] == "welcome"
    declared = session.receive({"type": "actions/declare", "actions": [
        {"name": "place", "description": "Place a mark", "params": CELL},
        {"name": "wait", "description": "Do nothing"}]})
    assert declared == [{"type": "declared", "actions": ["place", "wait"], "errors": []}]
    return session


def decide(session, request_id="r1", actions=("place",), **extra):
    return session.receive({"type": "decide", "id": request_id, "prompt": "Your move.",
                            "actions": list(actions), **extra})


# ---- route ------------------------------------------------------------------

@pytest.fixture
def games(monkeypatch):
    monkeypatch.setattr(settings, "games_enabled", True)
    monkeypatch.setattr(settings, "games_key", KEY)
    monkeypatch.setattr(settings, "games_chooser", "random")
    return TestClient(app)


def test_wrong_key_or_disabled_is_refused(games, monkeypatch):
    for url in ("/v1/games/play?key=nope", "/v1/games/play"):
        with pytest.raises(WebSocketDisconnect):
            with games.websocket_connect(url) as ws:
                ws.receive_text()
    monkeypatch.setattr(settings, "games_enabled", False)
    with pytest.raises(WebSocketDisconnect):
        with games.websocket_connect(f"/v1/games/play?key={KEY}") as ws:
            ws.receive_text()


def test_handshake_over_the_route(games):
    with games.websocket_connect(f"/v1/games/play?key={KEY}") as ws:
        ws.send_text(json.dumps({"type": "observe", "text": "too early"}))
        assert json.loads(ws.receive_text())["message"] == "send hello first"
        ws.send_text("not json")
        assert json.loads(ws.receive_text())["type"] == "error"
        client = GameClient.over(ws)
        welcome = client.hello("Test Game", capabilities=["say"])
        assert welcome["protocol"] == 1 and welcome["character"] == "shana" and welcome["session"]
        assert welcome["limits"]["max_attempts"] == gp.MAX_ATTEMPTS


def test_unsupported_version_gets_error_and_bye(games):
    with games.websocket_connect(f"/v1/games/play?key={KEY}") as ws:
        ws.send_text(json.dumps({"type": "hello", "protocol": 99, "game": "Future"}))
        assert json.loads(ws.receive_text())["type"] == "error"
        assert json.loads(ws.receive_text()) == {"type": "bye", "reason": "unsupported protocol version"}
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()


def test_random_chooser_declare_decide_act_outcome(games):
    with games.websocket_connect(f"/v1/games/play?key={KEY}") as ws:
        client = GameClient.over(ws)
        client.hello("Test Game")
        client.declare(tictactoe.PLACE)
        client.observe("A new board.", importance="high", comment=True)
        act = client.decide("r1", "Pick a cell.", ["place"], state="1 2 3\n4 5 6\n7 8 9")
        assert act["type"] == "act" and act["id"] == "r1" and act["action"] == "place"
        assert act["attempt"] == 1 and 1 <= act["params"]["cell"] <= 9
        assert client.outcome("r1", True, "Placed.") is None
        retry = client.decide("r2", "Again.", ["place"])
        again = client.outcome("r2", False, "cell taken")
        assert retry["attempt"] == 1 and again["type"] == "act" and again["attempt"] == 2
        client.outcome("r2", True)
        client.bye()


def test_a_whole_tictactoe_game_through_the_reference_client(games, monkeypatch):
    monkeypatch.setattr(routes_games, "build_chooser", FirstFreeCell)
    with games.websocket_connect(f"/v1/games/play?key={KEY}") as ws:
        client = GameClient.over(ws)
        result = tictactoe.play(client, rng=random.Random(4), allow_resign=False)
    assert result in ("X", "O", "draw")
    assert client.said and client.said[0].startswith("Cell 1")


def test_bad_declarations_are_reported():
    session = gp.GameSession(Scripted(gp.Choice("x")))
    session.receive({"type": "hello", "protocol": 1, "game": "Test"})
    reply = session.receive({"type": "actions/declare", "actions": [
        {"name": "Bad Name", "description": "x"},
        {"name": "nodesc"},
        {"name": "regex", "description": "x", "params": {"type": "object", "properties": {
            "s": {"type": "string", "pattern": "^a"}}}},
        {"name": "notobject", "description": "x", "params": {"type": "integer"}},
        {"name": "fine", "description": "ok", "params": {"type": "object", "properties": {
            "tags": {"type": "array", "items": {"enum": ["a", "b"]}, "maxItems": 2}}}}]})
    assert reply[0]["actions"] == ["fine"]
    assert len(reply[0]["errors"]) == 4 and "pattern" in reply[0]["errors"][2]


# ---- validation and retries -------------------------------------------------

def test_validate_covers_the_supported_subset():
    assert gp.validate({"cell": 5}, CELL) == []
    assert gp.validate({"cell": True}, CELL)            # bool is not an integer
    assert gp.validate({"cell": 10}, CELL)              # above maximum
    assert gp.validate({"cell": 2.5}, CELL)
    assert gp.validate({}, CELL)                        # required
    assert gp.validate({"cell": 1, "extra": 1}, CELL)   # undeclared param
    tags = {"type": "array", "items": {"enum": ["a", "b"]}, "minItems": 1, "maxItems": 2}
    assert gp.validate(["a"], tags) == []
    assert gp.validate([], tags) and gp.validate(["a", "b", "a"], tags) and gp.validate(["c"], tags)
    name = {"type": "string", "minLength": 2, "maxLength": 3}
    assert gp.validate("ab", name) == [] and gp.validate("a", name) and gp.validate("abcd", name)
    assert gp.validate(0.5, {"type": "number", "minimum": 0, "maximum": 1}) == []


def test_invalid_params_are_retried_internally_and_never_sent():
    chooser = Scripted(gp.Choice("place", {"cell": 12}), gp.Choice("nope", {}), gp.Choice("place", {"cell": 3}))
    session = started(chooser)
    out = decide(session)
    assert out == [{"type": "act", "id": "r1", "attempt": 3, "action": "place", "params": {"cell": 3}}]
    assert "params.cell: must be <= 9" in chooser.calls[1] and "allowed actions" in chooser.calls[2]


def test_params_on_an_action_without_params_are_invalid():
    session = started(Scripted(gp.Choice("wait", {"x": 1}), gp.Choice("wait", {})))
    out = decide(session, actions=("wait",))
    assert out[0]["params"] == {} and out[0]["attempt"] == 2


def test_only_invalid_answers_end_in_a_pass():
    chooser = Scripted(gp.Choice("place", {"cell": "five"}))
    session = started(chooser)
    out = decide(session)
    assert [m["type"] for m in out] == ["pass"] and out[0]["reason"] == "attempts"
    assert len(chooser.calls) == gp.MAX_ATTEMPTS and session.decision is None


def test_rejected_outcomes_retry_up_to_the_cap():
    chooser = Scripted(gp.Choice("place", {"cell": 1}))
    session = started(chooser)
    out = decide(session)
    for attempt in range(2, gp.MAX_ATTEMPTS + 1):
        out = session.receive({"type": "outcome", "id": "r1", "ok": False, "message": "taken"})
        assert out[0]["type"] == "act" and out[0]["attempt"] == attempt
    out = session.receive({"type": "outcome", "id": "r1", "ok": False, "message": "taken"})
    assert out == [{"type": "pass", "id": "r1", "reason": "attempts", "message": "taken"}]
    assert chooser.calls[1] == "taken"
    assert any("passed on" in o.text for o in session.context)


def test_deadline_passing_while_choosing_sends_pass_not_act():
    now = [0.0]
    session = started(Scripted(gp.Choice("place", {"cell": 1}), on_choose=lambda: now.__setitem__(0, 5.0)),
                      clock=lambda: now[0])
    out = decide(session, deadline_ms=2000)
    assert out == [{"type": "pass", "id": "r1", "reason": "deadline",
                    "message": "the deadline passed while choosing"}]


def test_missing_outcome_times_out_and_retries():
    now = [0.0]
    session = started(Scripted(gp.Choice("place", {"cell": 1})), clock=lambda: now[0])
    first = decide(session, deadline_ms=60_000)
    assert session.tick() == []
    now[0] = gp.OUTCOME_TIMEOUT_SECONDS + 1
    retried = session.tick()
    assert retried[0]["type"] == "act" and retried[0]["attempt"] == first[0]["attempt"] + 1
    # an outcome for the wrong request is ignored
    assert session.receive({"type": "outcome", "id": "other", "ok": True}) == []
    assert session.decision is not None


def test_one_decision_at_a_time_and_cancel():
    session = started(Scripted(gp.Choice("place", {"cell": 1})))
    decide(session, "r1")
    busy = decide(session, "r2")
    assert busy[0]["type"] == "pass" and busy[0]["reason"] == "busy"
    session.receive({"type": "cancel", "id": "r1"})
    assert session.decision is None
    assert decide(session, "r3", actions=("missing",))[0]["reason"] == "unknown_action"


def test_say_is_sent_only_when_speaking_is_allowed():
    session = started(Scripted(gp.Choice("place", {"cell": 2}, say="Middle top!")))
    out = decide(session)
    assert out[0] == {"type": "say", "id": "r1", "text": "Middle top!"} and out[1]["type"] == "act"
    session.receive({"type": "outcome", "id": "r1", "ok": True})
    quiet = decide(session, "r2", speak=False)
    assert [m["type"] for m in quiet] == ["act"] and session.said == ["Middle top!"]


def test_random_chooser_always_fits_the_schema():
    schema = {"type": "object", "properties": {
        "n": {"type": "integer", "minimum": -3, "maximum": 3},
        "x": {"type": "number", "minimum": 0.5, "maximum": 0.75},
        "s": {"type": "string", "minLength": 2, "maxLength": 4},
        "b": {"type": "boolean"},
        "e": {"enum": ["rock", "paper"]},
        "a": {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 1}, "minItems": 1, "maxItems": 3}},
        "required": ["n", "a"]}
    assert gp.check_schema(schema) == []
    rng = random.Random(1)
    for _ in range(200):
        assert gp.validate(gp.example_for(schema, rng), schema) == []


def test_model_chooser_parses_json_and_feeds_back_errors():
    replies = ["I think... no JSON here",
               'Sure! {"action": "place", "params": {"cell": 5}, "say": "Center."} done']
    prompts = []

    class FakeAdapter:
        def generate_reply(self, *, system_prompt, user_text):
            prompts.append((system_prompt, user_text))
            return SimpleNamespace(text=replies.pop(0))

    session = started(gp.ModelChooser(FakeAdapter()))
    session.receive({"type": "observe", "text": "The board is empty.", "importance": "high"})
    out = decide(session, state="1 2 3\n4 5 6\n7 8 9")
    assert out[-1] == {"type": "act", "id": "r1", "attempt": 2, "action": "place", "params": {"cell": 5}}
    assert out[0] == {"type": "say", "id": "r1", "text": "Center."}
    system, user = prompts[0]
    assert '"minimum": 1' in system and "The board is empty." in user and "4 5 6" in user
    assert "didn't work" in prompts[1][1]
    assert gp.parse_choice("nothing") == gp.Choice("")
