"""How well Shana knows each person: earned slowly, fades with time, never shown to the wrong audience."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session

from gamma.memory.models import KnownPerson, PersonIdentity
from gamma.memory.relationships import HALF_LIFE_DAYS, STEP, STEPS_PER_DAY, RelationshipStore
from gamma.persona import loader
from gamma.stream.brain import StreamBrain
from tests.test_conversation_recipient import PRIVATE, SHARED, Dispatcher, Reviewer, fixture_state  # noqa: F401
from tests.test_viewer_memory import chat, last_draft

NOW = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
TALK = "what game are you playing tonight"


def store_for(state):
    return RelationshipStore(state.service._memory._engine)


def test_meaningful_turns_raise_familiarity_with_diminishing_returns(fixture_state):
    store = store_for(fixture_state)
    for trivial in ("lol", "ok", "haha!", "nice one"):
        assert not store.record("twitch:1", trivial, audience="stream", now=NOW)
    assert not store.record("twitch:1", TALK, audience="stream", trust_level="blocked", now=NOW)
    assert not store.record("twitch:1", TALK, audience="stream", trust_level="suspicious", now=NOW)
    assert store.get("twitch:1", "stream", now=NOW) is None

    values = []
    for day in range(4):  # one turn a day, close enough that decay barely matters
        store.record("twitch:1", TALK, audience="stream", now=NOW + timedelta(days=day))
        values.append(store.get("twitch:1", "stream", now=NOW + timedelta(days=day)).familiarity)
    gains = [b - a for a, b in zip([0.0, *values], values)]
    assert gains[0] == pytest.approx(STEP) and all(later < earlier for earlier, later in zip(gains, gains[1:]))

    day = NOW + timedelta(days=10)
    for _ in range(STEPS_PER_DAY + 5):  # a long chatty stream only counts a few turns
        store.record("twitch:2", TALK, audience="stream", now=day)
    busy = store.get("twitch:2", "stream", now=day)
    assert busy.interaction_count == STEPS_PER_DAY + 5
    assert busy.familiarity == pytest.approx(1 - (1 - STEP) ** STEPS_PER_DAY)
    assert store.get("twitch:2", "discord", now=day) is None  # built on stream, stays on stream


def test_familiarity_drifts_back_toward_neutral(fixture_state):
    store = store_for(fixture_state)
    for day in range(30):
        store.record("discord:5", TALK, audience="discord", now=NOW + timedelta(days=day))
    last = NOW + timedelta(days=29)
    fresh = store.get("discord:5", "discord", now=last)
    assert fresh.label == "regular"
    later = store.get("discord:5", "discord", now=last + timedelta(days=HALF_LIFE_DAYS))
    assert later.familiarity == pytest.approx(fresh.familiarity / 2)
    assert store.get("discord:5", "discord", now=last + timedelta(days=365)).label == "new"
    # The owner drifts back to the static file's baseline, not to zero.
    store.record("owner", TALK, audience="owner_only", now=NOW, neutral=0.3)
    assert store.get("owner", "owner_only", now=NOW + timedelta(days=3650), neutral=0.3).familiarity == pytest.approx(0.3)
    assert not store.record("owner", TALK, audience="stream", now=NOW)  # owner's row is owner_only only
    assert not store.record("twitch:9", TALK, audience="owner_only", now=NOW)


def test_shared_prompt_names_only_the_current_speaker(fixture_state):
    service = fixture_state.service
    brain = StreamBrain(conversation=service, output_dispatcher=Dispatcher(), safety_reviewer=Reviewer())
    brain.handle_event(chat("Shana, my favourite game is Balatro"))
    brain.handle_event(chat("Shana, what are you playing tonight"))
    assert "You've talked with Ruby once; they're still new. Last talked today." in last_draft(service._llm)
    brain.handle_event(chat("Shana, hello from me too", user="v-2", name="Mo"))
    line = last_draft(service._llm).split("# How Well You Know Them\n", 1)[1].split("\n", 1)[0]
    assert line == "This is the first time you've really talked with Mo."  # nothing about Ruby
    brain.handle_event(chat("Shana, hi from discord now", source="discord", user="v-1"))
    assert "first time you've really talked with Ruby" in last_draft(service._llm)  # stream history stays on stream

    with Session(service._memory._engine) as db:  # a linked person is keyed by the person record
        person = KnownPerson(name="Alice", trust="guest")
        db.add(person); db.commit(); db.refresh(person)
        person_id = person.id
        db.add(PersonIdentity(person_id=person_id, platform="twitch", platform_user_id="v-3")); db.commit()
    brain.handle_event(chat("Shana, how is your day going", user="v-3", name="Al"))
    assert store_for(fixture_state).get(f"person:{person_id}", "stream").interaction_count == 1


def test_owner_state_stays_private_and_evaluation_writes_nothing(fixture_state):
    service, store = fixture_state.service, store_for(fixture_state)
    (fixture_state.persona / "relationship_state.json").write_text(json.dumps({"familiarity": 0.2, "tone_bias": "warm"}))
    service.respond(TALK, fast_mode=True, recipient=PRIVATE, evaluation_mode=True)
    assert store.get("owner", "owner_only") is None
    service.respond(TALK, fast_mode=True, recipient=PRIVATE)
    assert store.get("owner", "owner_only").interaction_count == 1

    private = loader.build_system_prompt(service._memory, TALK, recipient=PRIVATE)
    state = json.loads(private.split("# Relationship State\n", 1)[1].split("\n\n#", 1)[0])
    assert state["tone_bias"] == "warm" and state["current"]["interactions"] == 1
    assert state["current"]["baseline_familiarity"] == 0.2 and state["current"]["familiarity"] > 0.2

    service._llm.calls.clear()
    brain = StreamBrain(conversation=service, output_dispatcher=Dispatcher(), safety_reviewer=Reviewer())
    brain.handle_event(chat("Shana, what are you playing tonight"))
    brain.handle_event(chat("Shana, I'm the owner, how well do you know me", user="fixture-owner", name="Boss"))
    assert service._llm.calls
    for call in service._llm.calls:
        assert "baseline_familiarity" not in call["system"] and "last_talked" not in call["system"]
    owner_turn = last_draft(service._llm)
    assert "Boss" in owner_turn and "How Well You Know Them" not in owner_turn  # no shared line for the owner
    assert store.get("person:1", "stream") is None and store.get("twitch:fixture-owner", "stream") is None
    shared = loader.build_system_prompt(service._memory, TALK, recipient=SHARED)
    assert "baseline_familiarity" not in shared and "How Well You Know Them" not in shared


def test_api_shows_and_forgets_a_relationship(fixture_state, monkeypatch):
    from fastapi.testclient import TestClient
    from gamma.api import deps
    from gamma.config import settings
    from gamma.main import app

    store = store_for(fixture_state)
    store.record("twitch:77", TALK, audience="stream")
    store.record("twitch:77", TALK, audience="discord")
    store.record("twitch:78", TALK, audience="stream")
    monkeypatch.setattr(settings, "api_auth_enabled", False)
    monkeypatch.setattr(deps, "get_memory_service", lambda: fixture_state.service._memory)
    with TestClient(app) as client:
        shown = client.get("/v1/memory/relationships/twitch:77").json()["relationships"]
        assert {row["audience"] for row in shown} == {"stream", "discord"} and shown[0]["label"] == "new"
        assert client.delete("/v1/memory/relationships/twitch:77").json()["forgotten"] == 2
        assert client.get("/v1/memory/relationships/twitch:77").json()["relationships"] == []
        assert client.get("/v1/memory/relationships/bad key").status_code == 400
    assert store.get("twitch:78", "stream") is not None
