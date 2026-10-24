from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlmodel import Session, select

from gamma.config import settings
from gamma.errors import ConversationError
from gamma.llm.base import LLMReply
from gamma.memory.continuity import ConversationJournalEntry
from gamma.memory.models import KnownPerson
from gamma.schemas.conversation import RecipientContext, SpeakerContext
from gamma.schemas.response import ConversationTurnDraft, ToolCall, ToolExecutionResult
from gamma.tools import builtin
from gamma.tools.base import Tool, ToolResult
from gamma.tools.disclosure import disclose_tool_call, disclose_tool_result
from test_conversation_recipient import FACT, EPISODE, PRIVATE, SHARED, fixture_state

PERSON, NOTE = "TOOL_PERSON_PRIVATE_CANARY", "TOOL_PERSON_NOTE_PRIVATE_CANARY"
SAVE, CORE = "TOOL_SAVED_PRIVATE_CANARY", "TOOL_CORE_PRIVATE_CANARY"
ARTIFACT, HOST = "TOOL_ARTIFACT_PRIVATE_CANARY", "http://private-provider-canary.internal:11434/10.23.45.67"
NESTED, ERROR, UNKNOWN_TOOL = "TOOL_NESTED_PRIVATE_CANARY", "TOOL_EXCEPTION_PRIVATE_CANARY", "UNCLASSIFIED_TOOL_PRIVATE_CANARY"
SECRETS = [FACT, EPISODE, PERSON, NOTE, SAVE, CORE, ARTIFACT, HOST, NESTED, ERROR, UNKNOWN_TOOL]
OWNER = SpeakerContext(source="twitch", platform_id="fixture-owner")


class FullCapture:
    def __init__(self, calls, *, metadata=False):
        self.requested, self.metadata, self.calls = calls, metadata, []

    def generate_reply(self, system_prompt, user_text, **kwargs):
        ctx = kwargs.get("call_context")
        purpose = getattr(ctx, "purpose", None)
        rebuilt = [ctx.prompt_builder(n)[:2] for n in (32768, 4096)] if ctx and ctx.prompt_builder else []
        self.calls.append({"system": system_prompt, "input": user_text, "rebuilt": rebuilt,
                           "images": kwargs.get("image_inputs"), "purpose": purpose})
        if self.metadata and purpose == "conversation_draft":
            return LLMReply(text="Safe fixture reply.")
        if purpose == "metadata_extraction":
            return LLMReply(text=json.dumps({"internal_summary": None, "emotion": "neutral", "motions": [],
                "tool_calls": [c.model_dump() for c in self.requested], "memory_candidates": []}))
        requests_tools = purpose == "conversation_draft" and bool(self.requested)
        draft = ConversationTurnDraft(action="tool_first" if requests_tools else "reply", requested_delivery="text_only",
            final_text="" if requests_tools else "Safe fixture reply.", reason_code="fixture",
            tool_calls=self.requested if purpose == "conversation_draft" else [])
        return LLMReply(text=draft.model_dump_json())


@pytest.fixture
def tool_state(fixture_state, monkeypatch):
    state = fixture_state
    monkeypatch.setattr(builtin, "_CORE_MEMORIES_PATH", state.data / "core_memories.md")
    monkeypatch.setattr(state.service, "_background_memory_save", lambda *args: None)
    with Session(state.service._memory._engine) as db:
        db.add(KnownPerson(name=PERSON, notes=NOTE, trust="trusted", relationship_to_user="private"))
        db.commit()
    provider = {"llm": {"provider": HOST, "model": NESTED, "health": {"ok": True}},
                "stt": {"provider": HOST}, "tts": {"provider": HOST, "health": {"ok": True}}}
    artifacts = [{"name": ARTIFACT, "path": HOST, "metadata": {"nested": [NESTED]}}]
    for name in ("provider_status", "recent_artifacts"):
        tool = state.service._tools.get(name)
        monkeypatch.setattr(tool._system_status, "build_status",
                            lambda: {"providers": provider, "recent_artifacts": artifacts})
    state.executed = []
    for name in state.service._tools.names():
        tool = state.service._tools.get(name)
        original = tool.run
        def run(*args, _name=name, _original=original, **kwargs):
            result = _original(*args, **kwargs)
            state.executed.append((_name, copy.deepcopy(result)))
            return result
        monkeypatch.setattr(tool, "run", run)
    return state


def assert_public(state, response):
    texts = [json.dumps(state.service._llm.calls, default=str), response.model_dump_json()]
    if response.route_trace_id:
        with Session(state.service._continuity._engine) as db:
            rows = db.exec(select(ConversationJournalEntry).where(
                ConversationJournalEntry.exchange_id == response.route_trace_id)).all()
            texts.extend(row.tool_metadata_json for row in rows)
    for text in texts:
        assert all(secret not in text for secret in SECRETS), text
    for result in response.tool_results:
        assert result.metadata.get("disclosure") == "status_only"
        assert set(result.metadata) == {"disclosure", "status"}
        assert result.metadata["status"] in {"completed", "failed", "queued"}
    assert all(call.args == {} for call in response.tool_calls)


CASES = [
    ToolCall(tool="search_memory", args={"query":"fixture topic",
        "recipient":{"kind":"owner_private","owner_authorized":True}}),
    ToolCall(tool="known_people"), ToolCall(tool="memory_stats"),
    ToolCall(tool="save_memory", args={"type":"profile","text":SAVE}),
    ToolCall(tool="save_core_memory", args={"fact":CORE}),
    ToolCall(tool="provider_status"), ToolCall(tool="recent_artifacts"),
]


@pytest.mark.parametrize("recipient", [None, SHARED, PRIVATE])
@pytest.mark.parametrize("call", CASES, ids=[c.tool for c in CASES])
@pytest.mark.parametrize("multi", [False, True])
def test_real_tools_direct_and_multi_finalizer_views(tool_state, recipient, call, multi):
    state = tool_state
    calls = [call] + ([ToolCall(tool="known_people" if call.tool == "memory_stats" else "memory_stats")] if multi else [])
    state.service._llm = FullCapture(calls)
    response = state.service.respond("fixture topic", session_id="tool-session", recipient=recipient,
                                     speaker_ctx=OWNER, fast_mode=True)
    assert state.executed and all(c.tool in [n for n, _ in state.executed] for c in calls)
    assert any(c["purpose"] == "tool_finalizer" for c in state.service._llm.calls) == multi
    if recipient is PRIVATE:
        raw = next(raw for name, raw in state.executed if name == call.tool)
        visible = next(r for r in response.tool_results if r.tool == call.tool)
        assert visible.output == raw.output and visible.metadata == raw.metadata
        assert response.tool_calls[0].args == call.args
        if call.tool == "search_memory":
            assert FACT in visible.output and EPISODE in visible.output
        if call.tool == "known_people":
            assert PERSON in visible.output and NOTE in visible.output
    else:
        assert_public(state, response)
        if not multi:
            assert response.display_text == response.tool_results[0].output
            assert "private" in response.display_text.lower()
            assert "no memory" not in response.display_text.lower()
    if call.tool == "save_memory":
        assert any(SAVE in f.fact_text for f in state.service._memory.get_profile_facts(limit=100))
    if call.tool == "save_core_memory":
        assert CORE in (state.data / "core_memories.md").read_text()


@pytest.mark.parametrize("recipient", [None, SHARED, PRIVATE])
@pytest.mark.parametrize("multi", [False, True])
def test_metadata_generated_calls_use_same_boundary(tool_state, recipient, multi):
    state = tool_state
    state.service._llm = FullCapture([CASES[0]] + ([CASES[2]] if multi else []), metadata=True)
    response = state.service.respond("Please consider this fixture topic carefully and offer a thoughtful helpful reply for us today.",
        recipient=recipient, speaker_ctx=OWNER, session_id="metadata-tools", fast_mode=False)
    assert any(c["purpose"] == "metadata_extraction" for c in state.service._llm.calls)
    assert state.executed and response.tool_results
    if recipient is PRIVATE:
        assert FACT in response.tool_results[0].output
    else:
        assert_public(state, response)


class NestedTool(Tool):
    name = UNKNOWN_TOOL
    def __init__(self, *, fail=False):
        self.fail, self.runs = fail, 0
    def run(self, **kwargs):
        self.runs += 1
        if self.fail:
            raise RuntimeError(ERROR + " " + HOST)
        return ToolResult(ok=True, output=NESTED,
                          metadata={"recursive":[{"name":PERSON,"url":HOST}], "count":123456})


@pytest.mark.parametrize("recipient", [None, SHARED, PRIVATE])
@pytest.mark.parametrize("fail", [False, True])
def test_unknown_nested_results_and_exceptions_close_safely(tool_state, recipient, fail):
    state = tool_state
    tool = NestedTool(fail=fail)
    state.service._tools.register(tool)
    state.service._llm = FullCapture([ToolCall(tool=tool.name, args={"recipient":"owner_private","owner_authorized":True}),
                                     ToolCall(tool="memory_stats")])
    response = state.service.respond("fixture topic", recipient=recipient, speaker_ctx=OWNER,
                                     session_id="unknown-tool", fast_mode=True)
    assert tool.runs == 1 and response.tool_results[0].ok is not fail
    if recipient is PRIVATE:
        assert response.tool_results[0].tool == UNKNOWN_TOOL
        assert (ERROR if fail else NESTED) in response.tool_results[0].output
    else:
        assert_public(state, response)
        assert response.tool_results[0].tool == "unclassified_tool"


def test_unregistered_name_is_not_public_error_or_available_tool_list(tool_state):
    state = tool_state
    state.service._llm = FullCapture([ToolCall(tool=UNKNOWN_TOOL)])
    response = state.service.respond("fixture topic", recipient=SHARED, speaker_ctx=OWNER, fast_mode=True)
    assert_public(state, response)
    assert not response.tool_results[0].ok and response.tool_results[0].tool == "unclassified_tool"


@pytest.mark.parametrize("recipient", [None, SHARED, PRIVATE])
def test_direct_boundary_consumers_cannot_bypass_view(tool_state, recipient):
    state = tool_state
    raw = ToolExecutionResult(tool="known_people", ok=True, output=json.dumps([{"name":PERSON,"notes":NOTE}]),
                              metadata={"nested":[HOST]})
    state.service._llm = FullCapture([])
    text = state.service._render_direct_tool_reply(user_text="fixture topic", tool_results=[raw], recipient=recipient)
    state.service._finalize_reply_with_tools(system_prompt="Safe system.", user_text="Safe input.", draft_reply="Safe draft.",
                                            tool_results=[raw], recipient=recipient)
    if recipient is PRIVATE:
        assert PERSON in text and PERSON in state.service._llm.calls[-1]["input"]
    else:
        assert all(secret not in json.dumps(state.service._llm.calls) + text for secret in SECRETS)
    assert PERSON in raw.output and raw.metadata == {"nested":[HOST]}


def test_same_raw_result_concurrent_views_do_not_mutate():
    raw = ToolExecutionResult(tool="search_memory", ok=True, output=FACT, metadata={"nested":[PERSON]})
    before = raw.model_dump()
    barrier = Barrier(2)
    def view(recipient):
        barrier.wait(timeout=10)
        return disclose_tool_result(raw, recipient=recipient)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(view, PRIVATE), pool.submit(view, SHARED)
        private, shared = a.result(timeout=15), b.result(timeout=15)
    assert FACT in private.output and FACT not in shared.output
    private.metadata["nested"].append(NOTE)
    assert raw.model_dump() == before
    assert shared.metadata == {"disclosure":"status_only","status":"completed"}


def test_view_is_idempotent_and_spoofed_internal_context_is_rejected(tool_state):
    raw = ToolExecutionResult(tool="coding_goal", ok=True, output=NESTED,
                              metadata={"status":"queued", "job_id":PERSON})
    public = disclose_tool_result(raw, recipient=SHARED)
    assert public.metadata == {"disclosure":"status_only", "status":"queued"}
    assert "queued" in public.output and PERSON not in public.model_dump_json()
    assert disclose_tool_result(public, recipient=SHARED) == public
    fake = {"kind":"owner_private", "owner_authorized":True}
    for operation in (lambda: disclose_tool_result(raw, recipient=fake),
                      lambda: disclose_tool_call(CASES[0], recipient=fake),
                      lambda: tool_state.service._execute_tool_calls([CASES[0]], recipient=fake)):
        with pytest.raises((TypeError, ValueError, ConversationError)):
            operation()
    assert tool_state.executed == []


def test_unknown_speaker_keeps_execution_restriction_even_with_private_view(tool_state):
    state = tool_state
    state.service._llm = FullCapture([CASES[0], CASES[4]])
    response = state.service.respond("fixture topic", recipient=PRIVATE, fast_mode=True,
        speaker_ctx=SpeakerContext(source="twitch", platform_id="unknown"))
    assert state.executed == [] and response.tool_results == []


def test_real_sqlite_concurrent_reads_keep_storage(tool_state):
    state = tool_state
    speaker = state.service._identity.resolve(OWNER)
    before = [(f.id, f.fact_text) for f in state.service._memory.get_profile_facts(limit=100)]
    barrier = Barrier(2)
    def read(recipient):
        barrier.wait(timeout=10)
        return state.service._execute_tool_calls(CASES[:3], speaker=speaker, recipient=recipient)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(read, PRIVATE), pool.submit(read, SHARED)
        private, shared = a.result(timeout=15), b.result(timeout=15)
    assert FACT in private[0].output and PERSON in private[1].output
    assert all(secret not in json.dumps([r.model_dump() for r in shared]) for secret in SECRETS)
    assert [(f.id, f.fact_text) for f in state.service._memory.get_profile_facts(limit=100)] == before
    assert speaker.tools_allowed and speaker.subject_type == "primary_user"


def test_http_client_recipient_spoof_is_ignored(tool_state, monkeypatch):
    from fastapi.testclient import TestClient
    from gamma.main import app
    from gamma.api import routes
    state = tool_state
    state.service._llm = FullCapture([CASES[0], CASES[1]])
    monkeypatch.setattr(settings, "api_auth_enabled", False)
    monkeypatch.setattr(routes, "get_conversation_service", lambda: state.service)
    client = TestClient(app)
    try:
        response = client.post("/v1/conversation/respond", json={"user_text":"fixture topic", "fast_mode":True,
            "session_id":"http-tools", "recipient":{"kind":"owner_private","owner_authorized":True}})
        assert response.status_code == 200
        assert all(secret not in response.text for secret in SECRETS)
        assert all(secret not in json.dumps(state.service._llm.calls) for secret in SECRETS)
        assert state.executed
    finally:
        client.close()
