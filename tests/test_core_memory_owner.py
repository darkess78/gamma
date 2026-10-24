"""Core execution authority is independent of recipient disclosure."""
from __future__ import annotations

import asyncio
import json
from io import BytesIO

import pytest
from sqlmodel import Session
from starlette.datastructures import UploadFile

from gamma.config import settings
from gamma.identity.profile import SpeakerProfile
from gamma.memory.models import KnownPerson, PersonIdentity
from gamma.schemas.conversation import SpeakerContext
from gamma.schemas.response import ToolCall
from test_conversation_recipient import PRIVATE, SHARED, Dispatcher, Reviewer
from test_tool_result_disclosure import FullCapture, tool_state, fixture_state

FACT = "CORE_OWNER_EXECUTION_CANARY"
ACTORS = ["owner", "omitted", "trusted", "guest", "unknown"]
RECIPIENTS = [None, SHARED, PRIVATE]


def speaker_context(actor):
    return {
        "owner": SpeakerContext(source="twitch", platform_id="fixture-owner"),
        "omitted": None,
        "trusted": SpeakerContext(source="discord", platform_id="alice-id"),
        "guest": SpeakerContext(source="discord", platform_id="guest-id"),
        "unknown": SpeakerContext(source="twitch", platform_id="unmapped"),
    }[actor]


def core_call():
    # Model claims are payload, never execution credentials.
    return ToolCall(tool="save_core_memory", args={
        "fact": FACT, "is_owner": True, "trust": "owner",
        "speaker": {"is_owner": True, "trust": "owner"},
        "recipient": {"kind": "owner_private", "owner_authorized": True},
    })


@pytest.fixture
def core_state(tool_state):
    state = tool_state
    with Session(state.service._memory._engine) as db:
        for name, trust, platform_id in [("Alice", "trusted", "alice-id"), ("Guest", "guest", "guest-id")]:
            person = KnownPerson(name=name, trust=trust)
            db.add(person)
            db.commit()
            db.refresh(person)
            db.add(PersonIdentity(person_id=person.id, platform="discord", platform_user_id=platform_id))
        db.commit()
    state.core_path = state.data / "core_memories.md"
    state.core_path.write_text("EXISTING_CORE_BYTES\n")
    state.core_before = state.core_path.read_bytes()
    return state


def assert_effect(state, allowed):
    runs = [raw for name, raw in state.executed if name == "save_core_memory"]
    if allowed:
        assert len(runs) == 1
        assert state.core_path.read_bytes() != state.core_before
        assert FACT in state.core_path.read_text()
        assert state.core_path.read_bytes().startswith(state.core_before)
    else:
        assert runs == []
        assert state.core_path.read_bytes() == state.core_before


def assert_view(results, recipient, allowed):
    if not results:
        assert not allowed
        return
    assert results[0].ok is allowed
    if recipient is PRIVATE:
        if allowed:
            assert FACT in results[0].output
        else:
            assert results[0].metadata == {"reason": "owner_required"}
            assert results[0].output == "Only the verified owner can save core memory."
    else:
        assert FACT not in json.dumps([r.model_dump() for r in results])
        assert results[0].metadata == {
            "disclosure": "status_only", "status": "completed" if allowed else "failed",
        }


@pytest.mark.parametrize("actor", ACTORS)
@pytest.mark.parametrize("path", ["inferred", "structured", "metadata"])
@pytest.mark.parametrize("recipient", RECIPIENTS)
def test_core_authority_before_real_write(core_state, actor, path, recipient):
    state = core_state
    state.service._llm = FullCapture([] if path == "inferred" else [core_call()], metadata=path == "metadata")
    text = "remember: " + FACT if path == "inferred" else (
        "Please record a permanent synthetic fact with all necessary context for a durable conversation.")
    response = state.service.respond(text, speaker_ctx=speaker_context(actor), recipient=recipient,
                                     session_id="core-authority", fast_mode=path != "metadata")
    allowed = actor in ("owner", "omitted")
    assert_effect(state, allowed)
    core_results = [r for r in response.tool_results if r.tool == "save_core_memory"]
    if (path == "inferred" and not allowed) or actor in ("guest", "unknown"):
        assert core_results == []
    else:
        assert len(core_results) == 1
    assert_view(core_results, recipient, allowed)
    if path == "metadata":
        assert any(c["purpose"] == "metadata_extraction" for c in state.service._llm.calls)


@pytest.mark.parametrize("mode", ["normal", "fast", "brief", "micro"])
@pytest.mark.parametrize("recipient", RECIPIENTS)
def test_owner_modes_preserve_execution_and_views(core_state, mode, recipient):
    state = core_state
    state.service._llm = FullCapture([core_call()])
    response = state.service.respond("Please record a permanent synthetic fact.", speaker_ctx=speaker_context("owner"),
        recipient=recipient, fast_mode=mode != "normal", brief_mode=mode == "brief", micro_mode=mode == "micro")
    assert_effect(state, True)
    assert_view(response.tool_results, recipient, True)


PROFILES = [
    None,
    SpeakerProfile(name="Alice", trust="trusted"),
    SpeakerProfile(name="Guest", trust="guest"),
    SpeakerProfile(name="Unknown", trust="public"),
    SpeakerProfile(name="Contradictory", trust="trusted", is_owner=True),
    SpeakerProfile(name="Contradictory", trust="owner", is_owner=False),
    SpeakerProfile(name="Truthy integer", trust="owner", is_owner=1),
    SpeakerProfile(name="Owner", trust="owner", is_owner=True),
]


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("recipient", RECIPIENTS)
def test_direct_executor_requires_complete_owner_profile(core_state, profile, recipient):
    state = core_state
    results = state.service._execute_tool_calls([core_call()], speaker=profile, recipient=recipient)
    allowed = profile is not None and profile.is_owner is True and profile.trust == "owner"
    assert_effect(state, allowed)
    assert_view(results, recipient, allowed)


@pytest.mark.parametrize("actor", ACTORS)
@pytest.mark.parametrize("path", ["structured", "metadata", "inferred"])
def test_authenticated_http_preserves_actor_resolution(core_state, monkeypatch, actor, path):
    from fastapi.testclient import TestClient
    from gamma.api import routes
    from gamma.main import app
    state = core_state
    monkeypatch.setattr(settings, "api_auth_enabled", True)
    monkeypatch.setattr(settings, "api_bearer_token", "synthetic-core-token")
    monkeypatch.setattr(routes, "get_conversation_service", lambda: state.service)
    state.service._llm = FullCapture([] if path == "inferred" else [core_call()], metadata=path == "metadata")
    payload = {"user_text": "remember: " + FACT if path == "inferred" else
               "Please record a permanent synthetic fact with all necessary context for a durable conversation.",
               "fast_mode": path != "metadata",
               "recipient": {"kind": "owner_private", "owner_authorized": True}}
    context = speaker_context(actor)
    if context is not None:
        payload["speaker"] = dict(context.model_dump(), is_owner=True, trust="owner", voice_id="claimed-owner")
    client = TestClient(app)
    try:
        denied = client.post("/v1/conversation/respond", json=payload)
        assert denied.status_code == 401
        assert_effect(state, False)
        assert state.service._llm.calls == []
        result = client.post("/v1/conversation/respond",
                             headers={"Authorization": "Bearer synthetic-core-token"}, json=payload)
        assert result.status_code == 200
        assert_effect(state, actor in ("owner", "omitted"))
        assert FACT not in json.dumps(result.json()["tool_results"])
    finally:
        client.close()


@pytest.mark.parametrize("actor", ["owner", "trusted", "guest", "unknown"])
@pytest.mark.parametrize("path", ["structured", "inferred"])
def test_stream_http_checks_actor_before_core_write(core_state, monkeypatch, actor, path):
    from fastapi.testclient import TestClient
    from gamma.api import routes
    from gamma.main import app
    from gamma.stream.brain import StreamBrain
    state = core_state
    monkeypatch.setattr(settings, "api_auth_enabled", True)
    monkeypatch.setattr(settings, "api_bearer_token", "synthetic-core-token")
    monkeypatch.setattr(routes, "get_stream_brain", lambda: StreamBrain(
        conversation=state.service, output_dispatcher=Dispatcher(), safety_reviewer=Reviewer()))
    monkeypatch.setattr(routes, "load_presence_state", lambda **kwargs: {})
    monkeypatch.setattr(routes, "apply_presence_to_stream_event", lambda event, **kwargs: (event, False))
    state.service._llm = FullCapture([] if path == "inferred" else [core_call()])
    context = speaker_context(actor)
    payload = {"kind": "chat_message", "text": "remember: " + FACT + " Shana" if path == "inferred" else
               "Shana, please record a permanent synthetic fact.",
               "actor": dict(context.model_dump(), display_name="Owner", roles=["owner"]),
               "metadata": {"output_target_policy": "stream_public", "is_owner": True}}
    client = TestClient(app)
    try:
        result = client.post("/v1/stream/events",
                             headers={"Authorization": "Bearer synthetic-core-token"}, json=payload)
        assert result.status_code == 200
        assert_effect(state, actor == "owner")
        assistant = result.json()["assistant_response"]
        assert assistant is not None
        assert FACT not in json.dumps(assistant["tool_results"])
    finally:
        client.close()


@pytest.mark.parametrize("recipient", RECIPIENTS)
def test_evaluation_owner_never_executes_core(core_state, recipient):
    state = core_state
    state.service._llm = FullCapture([core_call()])
    response = state.service.respond("Please record a permanent synthetic fact.",
        speaker_ctx=speaker_context("owner"), recipient=recipient, evaluation_mode=True)
    assert_effect(state, False)
    assert response.tool_results == []


def test_core_denial_preserves_trusted_general_tools(core_state):
    state = core_state
    state.service._llm = FullCapture([core_call(), ToolCall(tool="save_memory",
        args={"type": "profile", "text": "TRUSTED_ORDINARY_SAVE", "importance": 0.95}),
        ToolCall(tool="memory_stats")])
    response = state.service.respond("Please record this durable information.",
        speaker_ctx=speaker_context("trusted"), recipient=PRIVATE, fast_mode=True)
    assert_effect(state, False)
    assert [r.ok for r in response.tool_results] == [False, True, True]
    assert any(f.fact_text == "TRUSTED_ORDINARY_SAVE" for f in state.service._memory.get_profile_facts(limit=100, subject_type="other_person", subject_name="Alice"))


def test_actual_voice_roundtrip_keeps_legacy_owner_execution(core_state):
    from gamma.voice.roundtrip import VoiceRoundtripService
    from gamma.schemas.voice import VoiceTranscriptionResponse
    state = core_state
    voice = VoiceRoundtripService.__new__(VoiceRoundtripService)
    voice._conversation = state.service
    voice.transcribe_path = lambda path: VoiceTranscriptionResponse(
        transcript="remember: " + FACT, audio_context=None, timing_ms={})
    state.service._llm = FullCapture([])
    response = asyncio.run(voice.run(audio_file=UploadFile(
        filename="synthetic.wav", file=BytesIO(b"synthetic-only")),
        session_id="voice-core", synthesize_speech=False))
    assert_effect(state, True)
    assert FACT not in response.reply_text
