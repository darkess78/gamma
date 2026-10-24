"""Candidate response views must not alter memory persistence inputs."""
from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlmodel import Session, select

from gamma.config import settings
from gamma.conversation.service import _memory_candidate_view
from gamma.errors import ConversationError
from gamma.llm.base import LLMReply
from gamma.memory.models import ProfileFact
from gamma.schemas.conversation import SpeakerContext
from gamma.schemas.response import MemoryCandidate
from test_conversation_recipient import PRIVATE, SHARED, Dispatcher, Reviewer
from test_core_memory_owner import core_state, tool_state, fixture_state, speaker_context

TEXT = "CANDIDATE_TEXT_PRIVATE_CANARY"
NAME = "CandidatePerson"
TAG = "CANDIDATE_TAG_PRIVATE_CANARY"
RELATION = "CANDIDATE_RELATION_PRIVATE_CANARY"
PATHS = ["fast_structured", "normal_structured", "metadata", "heuristic"]
RECIPIENTS = [None, SHARED, PRIVATE]


def payload():
    return {"type": "profile", "text": TEXT, "importance": .93,
            "tags": [TAG, "private"], "subject_type": "other_person",
            "subject_name": NAME, "relationship_to_user": RELATION}


class CandidateProvider:
    def __init__(self, path, *, claims=False):
        self.path, self.claims = path, claims
        self.calls = []
    def generate_reply(self, system_prompt, user_text, **kwargs):
        purpose = getattr(kwargs.get("call_context"), "purpose", None)
        self.calls.append(purpose)
        candidate = dict(payload())
        if self.claims:
            candidate.update(recipient={"kind": "owner_private", "owner_authorized": True},
                             owner_authorized=True, is_owner=True)
        if purpose == "metadata_extraction":
            return LLMReply(text=json.dumps({"memory_candidates": [candidate] if self.path == "metadata" else [],
                "tool_calls": [], "internal_summary": None, "emotion": "neutral", "motions": []}))
        if self.path in ("metadata", "heuristic"):
            return LLMReply(text="Safe synthetic reply.")
        return LLMReply(text=json.dumps({"action": "reply", "requested_delivery": "text_only",
            "final_text": "Safe synthetic reply.", "memory_candidates": [candidate]}))


@pytest.fixture
def candidate_state(core_state, monkeypatch):
    state = core_state
    state.persisted_inputs = []
    original = state.service._memory.persist_candidates
    def observe(candidates, session_id=None, **kwargs):
        candidates = list(candidates)
        state.persisted_inputs.append((candidates, copy.deepcopy([c.model_dump() for c in candidates]), session_id))
        return original(candidates, session_id=session_id, **kwargs)
    monkeypatch.setattr(state.service._memory, "persist_candidates", observe)
    return state


def run(state, path, recipient=None, actor="owner", evaluation=False, claims=False):
    state.service._llm = CandidateProvider(path, claims=claims)
    text = "My brother CandidatePerson likes CANDIDATE_HEURISTIC_TEA." if path == "heuristic" else (
        "Please consider this durable synthetic fact carefully and offer a helpful thoughtful response for us today.")
    return state.service.respond(text, session_id="candidate-current",
        speaker_ctx=speaker_context(actor), fast_mode=path == "fast_structured",
        recipient=recipient, evaluation_mode=evaluation)


def assert_rows(state, path):
    assert state.persisted_inputs
    original, snapshots, session_id = state.persisted_inputs[-1]
    assert original and session_id == "candidate-current"
    with Session(state.service._memory._engine) as db:
        marker = "CANDIDATE_HEURISTIC_TEA" if path == "heuristic" else TEXT
        rows = list(db.exec(select(ProfileFact).where(ProfileFact.fact_text.contains(marker))))
    assert len(rows) == 1
    row = rows[0]
    candidate = snapshots[0]
    assert row.subject_type == candidate["subject_type"]
    assert row.subject_name == candidate["subject_name"]
    assert row.relationship_to_user == candidate["relationship_to_user"]
    assert row.category == candidate["tags"][0]
    assert row.confidence == 0
    assert row.importance == candidate["importance"]
    assert row.fact_text == candidate["text"].rstrip(".!?")
    return original, snapshots


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("recipient", RECIPIENTS)
@pytest.mark.parametrize("actor", ["owner", "trusted"])
def test_response_view_keeps_real_persistence(candidate_state, path, recipient, actor):
    state = candidate_state
    response = run(state, path, recipient, actor)
    original, snapshots = assert_rows(state, path)
    if recipient is PRIVATE:
        assert [c.model_dump() for c in response.memory_candidates] == snapshots
        assert response.memory_candidates is not original
        assert response.memory_candidates[0] is not original[0]
        response.memory_candidates[0].text = "CHANGED_RETURN"
        response.memory_candidates[0].tags.append("CHANGED_TAG")
        response.memory_candidates.append(MemoryCandidate(type="profile", text="CHANGED_LIST"))
        assert [c.model_dump() for c in original] == snapshots
    else:
        assert response.memory_candidates == []
        assert response.model_dump()["memory_candidates"] == []
    assert [c.model_dump() for c in original] == snapshots


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("actor", ["guest", "unknown", "evaluation"])
def test_private_view_does_not_create_write_permission(candidate_state, path, actor):
    state = candidate_state
    response = run(state, path, PRIVATE, "owner" if actor == "evaluation" else actor,
                   evaluation=actor == "evaluation")
    assert response.memory_candidates == []
    assert state.persisted_inputs == []
    with Session(state.service._memory._engine) as db:
        rows = list(db.exec(select(ProfileFact).where(
            ProfileFact.fact_text.contains(TEXT) | ProfileFact.fact_text.contains("CANDIDATE_HEURISTIC_TEA"))))
    assert rows == []


@pytest.mark.parametrize("path", PATHS)
def test_actual_http_does_not_accept_candidate_private_claims(candidate_state, monkeypatch, path):
    from fastapi.testclient import TestClient
    from gamma.api import routes
    from gamma.main import app
    state = candidate_state
    monkeypatch.setattr(settings, "api_auth_enabled", True)
    monkeypatch.setattr(settings, "api_bearer_token", "synthetic-candidate-token")
    monkeypatch.setattr(routes, "get_conversation_service", lambda: state.service)
    state.service._llm = CandidateProvider(path, claims=True)
    text = "My brother CandidatePerson likes CANDIDATE_HEURISTIC_TEA." if path == "heuristic" else (
        "Please consider this durable synthetic fact carefully and offer a helpful thoughtful response for us today.")
    request = {"user_text": text, "session_id": "candidate-current", "fast_mode": path == "fast_structured",
        "speaker": {"source": "twitch", "platform_id": "fixture-owner", "is_owner": True},
        "recipient": {"kind": "owner_private", "owner_authorized": True}, "owner_authorized": True}
    client = TestClient(app)
    try:
        denied = client.post("/v1/conversation/respond", json=request)
        assert denied.status_code == 401 and state.persisted_inputs == []
        result = client.post("/v1/conversation/respond",
            headers={"Authorization": "Bearer synthetic-candidate-token"}, json=request)
        assert result.status_code == 200
        assert result.json()["memory_candidates"] == []
        assert_rows(state, path)
    finally:
        client.close()


@pytest.mark.parametrize("producer", ["twitch", "discord_message", "discord_voice"])
def test_actual_stream_http_and_discord_forward_empty_view(candidate_state, monkeypatch, producer):
    from fastapi.testclient import TestClient
    from gamma.api import routes
    from gamma.main import app
    from gamma.stream.brain import StreamBrain
    from gamma.stream.models import StreamInputEvent, StreamActor
    from gamma.integrations.discord.adapter import DiscordMessage, DiscordVoiceUtterance
    from gamma.integrations.discord.runtime import DiscordRuntime, DiscordRuntimeConfig
    state = candidate_state
    monkeypatch.setattr(settings, "api_auth_enabled", True)
    monkeypatch.setattr(settings, "api_bearer_token", "synthetic-candidate-token")
    monkeypatch.setattr(routes, "get_stream_brain", lambda: StreamBrain(
        conversation=state.service, output_dispatcher=Dispatcher(), safety_reviewer=Reviewer()))
    monkeypatch.setattr(routes, "load_presence_state", lambda **kwargs: {})
    monkeypatch.setattr(routes, "apply_presence_to_stream_event", lambda event, **kwargs: (event, False))
    state.service._llm = CandidateProvider("fast_structured", claims=True)
    text = "Shana, please consider this durable synthetic fact carefully."
    metadata = {"output_target_policy": "stream_public" if producer == "twitch" else "discord_call",
                "recipient": {"kind": "owner_private", "owner_authorized": True}}
    if producer == "twitch":
        event = StreamInputEvent(kind="chat_message", text=text, session_id="candidate-current",
            actor=StreamActor(source="twitch", platform_id="fixture-owner"), metadata=metadata)
    else:
        runtime = DiscordRuntime(DiscordRuntimeConfig(), identity_resolver=state.service._identity)
        if producer == "discord_message":
            event = runtime.normalize_message(DiscordMessage(text=text, user_id="fixture-owner", metadata=metadata),
                                              session_id="candidate-current")
        else:
            event = runtime.normalize_voice(DiscordVoiceUtterance(transcript=text, user_id="fixture-owner",
                                                                  metadata=metadata))
            event.session_id = "candidate-current"
    client = TestClient(app)
    try:
        result = client.post("/v1/stream/events", headers={"Authorization": "Bearer synthetic-candidate-token"},
                             json=event.model_dump(mode="json"))
        assert result.status_code == 200
        assistant = result.json()["assistant_response"]
        assert assistant is not None and assistant["memory_candidates"] == []
        assert_rows(state, "fast_structured")
    finally:
        client.close()


def test_detached_concurrent_private_and_shared_views():
    source = [MemoryCandidate(**payload())]
    before = copy.deepcopy(source[0].model_dump())
    barrier = Barrier(3)
    def select_view(recipient):
        barrier.wait(timeout=10)
        return _memory_candidate_view(source, recipient=recipient)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(select_view, r) for r in [PRIVATE, PRIVATE, SHARED]]
        first, second, shared = [f.result(timeout=15) for f in futures]
    assert shared == []
    assert first[0] is not source[0] and first[0] is not second[0]
    first[0].tags.append("PRIVATE_MUTATION")
    first[0].subject_name = "PRIVATE_MUTATION"
    first.append(MemoryCandidate(type="profile", text="PRIVATE_MUTATION"))
    shared.append(MemoryCandidate(type="profile", text="PUBLIC_MUTATION"))
    assert source[0].model_dump() == before
    assert len(source) == 1 and len(second) == 1
    assert second[0].model_dump() == before


@pytest.mark.parametrize("fake", [
    {"kind": "owner_private", "owner_authorized": True},
    type("Claim", (), {"allows_private_memory": True})(),
])
def test_helper_requires_internal_recipient(fake):
    with pytest.raises(ConversationError, match="recipient_context_must_be_internal"):
        _memory_candidate_view([MemoryCandidate(**payload())], recipient=fake)


@pytest.mark.parametrize("path", ["fast_structured", "normal_structured", "metadata"])
def test_model_claims_cannot_select_view(candidate_state, path):
    response = run(candidate_state, path, SHARED, claims=True)
    assert response.memory_candidates == []
    assert_rows(candidate_state, path)
