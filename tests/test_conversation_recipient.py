from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from threading import Barrier
from types import SimpleNamespace
from pathlib import Path

import pytest
from sqlmodel import Session, select

from gamma.config import settings
from gamma.conversation.service import ConversationService
from gamma.errors import ConversationError
from gamma.identity.profile import SpeakerProfile
from gamma.identity import resolver
from gamma.llm.base import LLMReply
from gamma.memory.continuity import ConversationJournalEntry, RollingSessionSummary, WorkingStateCheckpoint
from gamma.memory.models import EpisodicMemory, KnownPerson, PersonIdentity, ProfileFact
from gamma.persona import loader, emotion_service, assistant_state
from gamma.schemas.conversation import ConversationRequest, RecipientContext, SpeakerContext
from gamma.schemas.response import ConversationTurnDraft, ToolCall
from gamma.stream.brain import StreamBrain
from gamma.stream.models import StreamActor, StreamInputEvent
from gamma.stream.output import OutputDispatchResult
from gamma.safety.llm_reviewer import LLMReviewDecision


FACT = "SQL_ORDINARY_FACT_CANARY"
EPISODE = "SQL_EPISODIC_CANARY"
PRIVATE_WORK = "PRIVATE_WORK_CANARY"
LEGACY = "LEGACY_PUBLIC_LABEL_CANARY"
PRIVATE = RecipientContext(kind="owner_private", owner_authorized=True)
SHARED = RecipientContext(kind="shared", target_policy="stream_public")


class Capture:
    def __init__(self, *, malformed=False, tools=False, overlap=None, fail_repair=False):
        self.calls = []
        self.malformed, self.tools, self.overlap = malformed, tools, overlap
        self.fail_repair = fail_repair

    def generate_reply(self, system_prompt, user_text, **kwargs):
        context = kwargs.get("call_context")
        purpose = getattr(context, "purpose", None)
        if self.overlap and purpose == "conversation_draft":
            self.overlap.wait(timeout=10)
        rebuilt = [context.prompt_builder(n)[0] for n in (32768, 4096)] if context and context.prompt_builder else []
        self.calls.append({"system": system_prompt, "input": user_text, "purpose": purpose, "rebuilt": rebuilt})
        if self.malformed and purpose == "conversation_draft":
            return LLMReply(text="{")
        if self.fail_repair and purpose == "conversation_structured_repair":
            return LLMReply(text="{")
        if self.tools and purpose == "conversation_draft":
            result = ConversationTurnDraft(action="tool_first", requested_delivery="text_only", reason_code="fixture",
                tool_calls=[ToolCall(tool="search_memory", args={"query": "fixture topic", "recipient": "owner_private"}),
                            ToolCall(tool="memory_stats", args={})])
        else:
            result = ConversationTurnDraft(action="reply", requested_delivery="text_only",
                                           final_text="Safe fixture reply.", reason_code="fixture")
        return LLMReply(text=result.model_dump_json())


@pytest.fixture
def fixture_state(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "memory").mkdir(parents=True)
    persona, config = tmp_path / "persona", tmp_path / "config"
    persona.mkdir(); config.mkdir()
    for name, text in {"core.md": "SAFE_CHARACTER_CANARY Shana is one continuous character.",
                       "boundaries.md": "Respect private context.", "style.json": "{}",
                       "relationship_state.json": "{}"}.items():
        (persona / name).write_text(text)
    (config / "persona.yaml").write_bytes((Path(__file__).resolve().parents[1] / "config/persona.yaml").read_bytes())
    (config / "users.toml").write_text('[owner]\nname="FixtureOwner"\n')
    (data / "core_memories.md").write_text("")
    monkeypatch.setattr(settings, "project_root", tmp_path)
    monkeypatch.setattr(settings, "data_dir", data)
    monkeypatch.setattr(settings, "database_url", "sqlite:///" + str(data / "memory/state.db"))
    monkeypatch.setattr(settings, "memory_enabled", True)
    monkeypatch.setattr(settings, "speech_filter_llm_enabled", False)
    monkeypatch.setattr(settings, "conversation_journal_retention_days", 30)
    monkeypatch.setattr(resolver, "_CONFIG_PATH", config / "users.toml")
    monkeypatch.setattr(loader, "PERSONA_DIR", persona)
    monkeypatch.setattr(loader, "CONFIG_DIR", config)
    monkeypatch.setattr(loader, "CORE_MEMORIES_PATH", data / "core_memories.md")
    monkeypatch.setattr(emotion_service, "_DEFAULT_PATH", data / "assistant_emotion_memory.json")
    monkeypatch.setattr(assistant_state, "_DEFAULT_STATE_PATH", data / "assistant_state.json")
    service = ConversationService()
    service._llm = Capture()
    with Session(service._memory._engine) as db:
        db.add(ProfileFact(category="project", fact_text=FACT))
        db.add(EpisodicMemory(summary="fixture topic " + EPISODE, session_id="mixed", importance=0.9))
        person = KnownPerson(name="FixtureOwner", trust="owner")
        db.add(person); db.commit(); db.refresh(person)
        for source in ("twitch", "discord"):
            db.add(PersonIdentity(person_id=person.id, platform=source, platform_user_id="fixture-owner"))
        db.commit()
    c = service._continuity
    exchange = c.begin_exchange(session_id="mixed", text=PRIVATE_WORK, speaker_name="FixtureOwner",
                                input_source="local", privacy_scope="local_private")
    c.complete_exchange(exchange_id=exchange, session_id="mixed", assistant_text="", trace_id=None,
                        output_target="dashboard_monitor", privacy_scope="local_private",
                        state_updates={"current_objective": PRIVATE_WORK, "deferred_intention": PRIVATE_WORK})
    with Session(c._engine) as db:
        db.add(RollingSessionSummary(session_id="legacy", summary_text=LEGACY, privacy_scope="public"))
        db.add(WorkingStateCheckpoint(session_id="legacy", current_objective=LEGACY, privacy_scope="public"))
        db.add(ConversationJournalEntry(exchange_id="legacy-public", session_id="legacy", role="user", text=LEGACY,
                                       privacy_scope="public", completion_status="completed"))
        db.commit()
    return SimpleNamespace(service=service, data=data, persona=persona, config=config)


def prompt_layers(adapter):
    return [text for call in adapter.calls for text in [call["system"], *call["rebuilt"]]]


@pytest.mark.parametrize("recipient", [None, RecipientContext(), SHARED])
@pytest.mark.parametrize("evaluation", [False, True])
def test_unbound_or_shared_owner_excludes_ordinary_sql_and_private_continuity(fixture_state, recipient, evaluation):
    service = fixture_state.service
    response = service.respond("fixture topic", session_id="mixed", fast_mode=True,
        speaker_ctx=SpeakerContext(source="twitch", platform_id="fixture-owner"),
        recipient=recipient, evaluation_mode=evaluation)
    assert response.display_text == "Safe fixture reply."
    assert all(FACT not in p and EPISODE not in p and PRIVATE_WORK not in p for p in prompt_layers(service._llm))
    assert "SAFE_CHARACTER_CANARY" in service._llm.calls[0]["system"]
    assert service._identity.resolve(SpeakerContext(source="twitch", platform_id="fixture-owner")).tools_allowed
    if not evaluation:
        with Session(service._continuity._engine) as db:
            row = db.exec(select(ConversationJournalEntry).where(
                ConversationJournalEntry.exchange_id == response.route_trace_id,
                ConversationJournalEntry.role == "user")).one()
        assert row.privacy_scope == (recipient.privacy_scope if recipient else "local_generic")


def test_explicit_trusted_private_context_and_subject_gate(fixture_state):
    service = fixture_state.service
    service.respond("fixture topic", session_id="mixed", fast_mode=True, recipient=PRIVATE)
    prompt = service._llm.calls[0]["system"]
    assert all(v in prompt for v in (FACT, EPISODE, PRIVATE_WORK, "SAFE_CHARACTER_CANARY"))
    guest = SpeakerProfile(name="FixtureGuest", trust="guest")
    guest_prompt = loader.build_system_prompt(service._memory, "fixture topic", "mixed", guest, recipient=PRIVATE)
    assert FACT not in guest_prompt and EPISODE not in guest_prompt


def test_direct_builder_and_client_json_cannot_grant_owner_view(fixture_state):
    request = ConversationRequest.model_validate({"user_text": "fixture topic",
        "recipient": {"kind": "owner_private", "owner_authorized": True},
        "owner_authorized": True, "speaker": {"source": "local", "is_owner": True}})
    assert not hasattr(request, "recipient")
    fixture_state.service.respond(request.user_text, speaker_ctx=request.speaker, fast_mode=True)
    assert FACT not in fixture_state.service._llm.calls[0]["system"]
    assert FACT not in loader.build_system_prompt(fixture_state.service._memory, "fixture topic", speaker=None)
    with pytest.raises((TypeError, ConversationError)):
        fixture_state.service.respond("fixture topic", recipient={"kind": "owner_private"})


@pytest.mark.parametrize("kwargs", [
    {"kind": "invalid"}, {"kind": 1}, {"kind": "owner_private"},
    {"kind": "owner_private", "owner_authorized": True, "target_policy": "stream_public"},
    {"kind": "owner_private", "owner_authorized": True, "target_policy": "discord_call"},
    {"kind": "shared", "owner_authorized": True}, {"owner_authorized": 1},
    {"target_policy": "other"}, {"target_policy": 1},
])
def test_recipient_constructor_rejects_invalid_values(kwargs):
    with pytest.raises((TypeError, ValueError)):
        RecipientContext(**kwargs)


def test_recipient_is_immutable_and_target_conflict_precedes_state_and_prompt(fixture_state):
    with pytest.raises(FrozenInstanceError):
        PRIVATE.kind = "shared"
    assert not hasattr(PRIVATE, "__dict__")
    before = fixture_state.service._continuity.snapshot("mixed")
    with Session(fixture_state.service._continuity._engine) as db:
        rows_before = [(r.id, r.completion_status) for r in db.exec(select(ConversationJournalEntry))]
    with pytest.raises(ConversationError, match="conflicts"):
        fixture_state.service.respond("fixture topic", session_id="mixed", recipient=PRIVATE,
                                      delivery_context="public_stream", fast_mode=True)
    assert fixture_state.service._llm.calls == []
    assert fixture_state.service._continuity.snapshot("mixed") == before
    with Session(fixture_state.service._continuity._engine) as db:
        assert [(r.id, r.completion_status) for r in db.exec(select(ConversationJournalEntry))] == rows_before


@pytest.mark.parametrize("facade", ["image", "wake"])
@pytest.mark.parametrize("recipient", [None, SHARED, PRIVATE])
def test_image_and_wake_use_same_explicit_recipient(fixture_state, monkeypatch, facade, recipient):
    service = fixture_state.service
    if facade == "image":
        monkeypatch.setattr(service._vision, "prepare_image", lambda **kwargs: None)
        monkeypatch.setattr(service._vision, "analyze_image", lambda **kwargs: None)
        service.respond_with_image(user_text="fixture topic", image_bytes=b"fixture", image_media_type="image/png",
                                   session_id="mixed", recipient=recipient)
    else:
        service.respond_presence_wake(session_id="mixed", speaker=service._identity.resolve(None),
                                     wake_context="Safe wake context.", synthesize_speech=False, recipient=recipient)
    first = service._llm.calls[0]["system"]
    assert (FACT in first) == (recipient is PRIVATE)
    assert "SAFE_CHARACTER_CANARY" in first
    if recipient is not PRIVATE:
        assert all(FACT not in p and PRIVATE_WORK not in p for p in prompt_layers(service._llm))


def test_public_and_default_ignore_mislabeled_legacy_history(fixture_state):
    for recipient in (None, SHARED):
        service = fixture_state.service
        service._llm = Capture()
        service.respond("fixture topic", session_id="legacy", fast_mode=True, recipient=recipient)
        assert all(LEGACY not in p for p in prompt_layers(service._llm))
    service._llm = Capture()
    service.respond("fixture topic", session_id="legacy", fast_mode=True, recipient=PRIVATE)
    assert LEGACY in service._llm.calls[0]["system"]


def test_overlap_keeps_each_recipient_and_private_checkpoint(fixture_state):
    private, public = fixture_state.service, ConversationService()
    before = private._continuity.snapshot("mixed")
    barrier = Barrier(2)
    private._llm = Capture(overlap=barrier); public._llm = Capture(overlap=barrier)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(private.respond, "fixture topic", session_id="mixed", recipient=PRIVATE,
                               fast_mode=True, evaluation_mode=True),
                   pool.submit(public.respond, "fixture topic", session_id="mixed", recipient=SHARED, fast_mode=True)]
        for future in futures:
            future.result(timeout=20)
    assert FACT in private._llm.calls[0]["system"]
    assert all(FACT not in p and PRIVATE_WORK not in p for p in prompt_layers(public._llm))
    assert private._continuity.snapshot("mixed") == before


def test_repair_fallback_and_finalizer_keep_same_frozen_recipient(fixture_state, monkeypatch):
    service = fixture_state.service
    seen = []
    for method in ("_parse_turn_result", "_finalize_reply_with_tools", "_guard_finalized_text"):
        original = getattr(service, method)
        def observe(*args, _original=original, **kwargs):
            seen.append(kwargs.get("recipient"))
            return _original(*args, **kwargs)
        monkeypatch.setattr(service, method, observe)
    service._llm = Capture(malformed=True)
    service.respond("fixture topic", session_id="mixed", recipient=SHARED, fast_mode=True)
    assert any(c["purpose"] == "conversation_structured_repair" for c in service._llm.calls)
    assert all(FACT not in p and PRIVATE_WORK not in p for p in prompt_layers(service._llm))
    service._llm = Capture(tools=True)
    service.respond("fixture topic", recipient=SHARED, fast_mode=True)
    assert any(c["purpose"] == "tool_finalizer" for c in service._llm.calls)
    assert all(FACT not in p and PRIVATE_WORK not in p for p in prompt_layers(service._llm))
    assert all(value is SHARED for value in seen)
    assert all(FACT not in c["input"] for c in service._llm.calls if c["purpose"] == "tool_finalizer")


def test_failed_repair_terminal_fallback_keeps_shared_recipient(fixture_state, monkeypatch):
    service = fixture_state.service
    service._llm = Capture(malformed=True, fail_repair=True)
    original = service._fallback_turn_draft
    seen = []
    def fallback(**kwargs):
        seen.append(kwargs["recipient"])
        return original(**kwargs)
    monkeypatch.setattr(service, "_fallback_turn_draft", fallback)
    response = service.respond("fixture topic", session_id="mixed", recipient=SHARED,
                               delivery_context="public_stream", fast_mode=True)
    assert response.response_action == "stay_silent"
    assert seen and all(value is SHARED for value in seen)
    assert all(FACT not in p and PRIVATE_WORK not in p for p in prompt_layers(service._llm))


class Dispatcher:
    def dispatch(self, events):
        return OutputDispatchResult()


class Reviewer:
    def review(self, text):
        return LLMReviewDecision(action="allow", reason="fixture", confidence=1)


@pytest.mark.parametrize("source,target,kind", [("twitch", "stream_public", "chat_message"),
    ("discord", "discord_call", "mic_transcript"), ("twitch", "dashboard_monitor", "chat_message")])
def test_real_stream_owner_and_discord_use_shared_view(fixture_state, source, target, kind):
    service = fixture_state.service
    before = service._continuity.snapshot("mixed")
    brain = StreamBrain(conversation=service, output_dispatcher=Dispatcher(), safety_reviewer=Reviewer())
    event = StreamInputEvent(kind=kind, text="Shana, fixture topic", session_id="mixed",
        actor=StreamActor(source=source, platform_id="fixture-owner"),
        metadata={"output_target_policy": target, "recipient": {"kind": "owner_private", "owner_authorized": True}})
    result = brain.handle_event(event, synthesize_speech=False)
    assert result.assistant_response is not None
    assert all(FACT not in p and PRIVATE_WORK not in p for p in prompt_layers(service._llm))
    assert "kind: shared" in service._llm.calls[0]["system"]
    assert service._continuity.snapshot("mixed") == before
    with Session(service._continuity._engine) as db:
        row = db.exec(select(ConversationJournalEntry).where(
            ConversationJournalEntry.exchange_id == result.assistant_response.route_trace_id,
            ConversationJournalEntry.role == "user")).one()
    assert row.privacy_scope == "public" and row.output_target is None


def test_stream_unknown_target_rejected_before_conversation(fixture_state):
    brain = StreamBrain(conversation=fixture_state.service)
    event = StreamInputEvent(kind="chat_message", text="Shana, fixture topic",
                            metadata={"output_target_policy": "invalid"})
    with pytest.raises(ConversationError, match="unknown_recipient_output_target"):
        brain.handle_event(event)
    assert fixture_state.service._llm.calls == []


def test_private_recipient_keeps_existing_other_person_subject_filter(fixture_state):
    service = fixture_state.service
    with Session(service._memory._engine) as db:
        db.add(ProfileFact(category="project", fact_text="FRIEND_SUBJECT_CANARY",
                           subject_type="other_person", subject_name="FixtureFriend"))
        db.commit()
    friend = SpeakerProfile(name="FixtureFriend", trust="trusted")
    prompt = loader.build_system_prompt(service._memory, "fixture topic", speaker=friend, recipient=PRIVATE)
    assert "FRIEND_SUBJECT_CANARY" in prompt and FACT not in prompt
    assert friend.tools_allowed


@pytest.mark.parametrize("auth_enabled", [True, False])
def test_real_http_bearer_and_json_do_not_grant_private_recipient(fixture_state, monkeypatch, auth_enabled):
    from fastapi.testclient import TestClient
    from gamma.main import app
    from gamma.api import routes
    monkeypatch.setattr(settings, "api_auth_enabled", auth_enabled)
    monkeypatch.setattr(settings, "api_bearer_token", "fixture-token-only")
    monkeypatch.setattr(routes, "get_conversation_service", lambda: fixture_state.service)
    # No lifespan start: this exercises the real route/auth middleware without starting workers.
    client = TestClient(app)
    try:
        response = client.post("/v1/conversation/respond", headers={"Authorization": "Bearer fixture-token-only"},
            json={"user_text": "fixture topic", "session_id": "mixed", "fast_mode": True,
                  "recipient": {"kind": "owner_private", "owner_authorized": True}})
        assert response.status_code == 200
        assert response.json()["display_text"] == "Safe fixture reply."
        assert all(FACT not in p and PRIVATE_WORK not in p for p in prompt_layers(fixture_state.service._llm))
    finally:
        client.close()


def test_user_invoked_local_cli_uses_explicit_private_path(fixture_state, monkeypatch, capsys):
    from gamma import run_llm_test
    monkeypatch.setattr(run_llm_test, "ConversationService", lambda: fixture_state.service)
    monkeypatch.setattr(run_llm_test.sys, "argv", ["gamma.run_llm_test", "fixture topic"])
    run_llm_test.main()
    assert FACT in fixture_state.service._llm.calls[0]["system"]
    assert json.loads(capsys.readouterr().out)["reply"] == "Safe fixture reply."
