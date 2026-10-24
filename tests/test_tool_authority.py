import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from sqlalchemy import select
from sqlmodel import Session

from gamma.config import settings
from gamma.conversation.service import ConversationService
from gamma.identity.profile import SpeakerProfile
from gamma.memory.continuity import ContinuityService
from gamma.memory.continuity import ConversationJournalEntry
from gamma.memory.core import CoreMemoryStore
from gamma.memory.models import ProfileFact
from gamma.memory.state_authority import StateConflict, documents
from gamma.schemas.response import ToolCall
from gamma.tools.policy import ToolGrants, allowed


@pytest.fixture
def conversation(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'tools.db'}")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "memory_enabled", True)
    monkeypatch.setattr(settings, "memory_write_mode", "on")
    monkeypatch.setattr(settings, "speech_filter_llm_enabled", False)
    monkeypatch.setattr(settings, "assistant_state_enabled", True)
    instance = ConversationService()
    monkeypatch.setattr(instance, "_append_timing_log", lambda **kwargs: None)
    monkeypatch.setattr(instance, "_record_presence_interaction", lambda *args: None)
    return instance


@pytest.mark.parametrize("tool", ["save_memory", "save_core_memory"])
@pytest.mark.parametrize("fast_mode", [False, True])
@pytest.mark.parametrize("stale", [False, True])
def test_model_requested_write_requires_successful_decision_commit(conversation, tool, fast_mode, stale):
    other = ContinuityService()
    args = {"type": "profile", "text": "Fictional tool fact", "session_id": "model-supplied-wrong-session"} if tool == "save_memory" else {"fact": "Fictional core fact"}
    def reply(*args_, **kwargs):
        if stale:
            exchange = other.begin_exchange(session_id="fixture", text="Newer decision", speaker_name="Fixture", input_source="test", privacy_scope="local_private")
            other.complete_exchange(exchange_id=exchange, session_id="fixture", assistant_text="Accepted", trace_id=None,
                output_target="dashboard_monitor", privacy_scope="local_private")
        return SimpleNamespace(text=json.dumps({"action": "tool_first", "requested_delivery": "text_only", "final_text": "Noted.",
            "tool_calls": [{"tool": tool, "args": args}]}))
    conversation._llm = SimpleNamespace(generate_reply=reply)
    conversation._identity.resolve = lambda ctx: SpeakerProfile("Fixture", "owner", is_owner=True)
    # A direct writer would be a regression even if the final response looked correct.
    conversation._tools.get(tool).run = Mock(side_effect=AssertionError("direct write bypass"))
    with patch("gamma.conversation.service.build_system_prompt", return_value="Fixture"):
        if stale:
            with pytest.raises(StateConflict):
                conversation.respond("Hello there", session_id="fixture", fast_mode=fast_mode)
        else:
            response = conversation.respond("Hello there", session_id="fixture", fast_mode=fast_mode)
            result = next(r for r in response.tool_results if r.tool == tool)
            assert result.ok and result.metadata["status"] == "committed"
    conversation._tools.get(tool).run.assert_not_called()
    with Session(other._engine) as session:
        facts = list(session.execute(select(ProfileFact)).scalars())
        core = session.execute(select(documents.c.payload).where(documents.c.key == "core_memories")).scalar()
    assert len(facts) == (1 if tool == "save_memory" and not stale else 0)
    assert bool(core) == (tool == "save_core_memory" and not stale)
    assert other.snapshot("fixture")["state_version"] == 1


def test_trusted_model_cannot_promote_itself_to_owner_or_execute_unknown_capability(conversation):
    speaker = SpeakerProfile("Trusted fixture", "trusted", is_owner=False)
    core_tool = conversation._tools.get("save_core_memory")
    core_tool.run = Mock(side_effect=AssertionError("permission bypass"))
    results = conversation._execute_tool_calls([
        ToolCall(tool="save_core_memory", args={"fact": "Unapproved", "is_owner": True, "trust": "owner"}),
        ToolCall(tool="grant_capability", args={"permission": "owner"}),
    ], speaker=speaker)
    assert all(not result.ok for result in results)
    assert results[0].metadata["reason"] == "permission_denied"
    core_tool.run.assert_not_called()
    assert not allowed("save_core_memory", ToolGrants(True, True, True, True, False))
    assert not allowed("save_memory", ToolGrants(False, True, True, True, True))


def test_disabled_memory_writes_deny_even_owner_tool_requests(conversation, monkeypatch):
    monkeypatch.setattr(settings, "memory_write_mode", "off")
    owner = SpeakerProfile("Fixture", "owner", is_owner=True)
    results = conversation._execute_tool_calls([
        ToolCall(tool="save_memory", args={"type": "profile", "text": "Forbidden"}),
        ToolCall(tool="save_core_memory", args={"fact": "Forbidden"}),
    ], speaker=owner)
    assert all(not result.ok and result.metadata["reason"] == "permission_denied" for result in results)


def test_core_memory_preserves_markdown_and_rolls_back_with_transaction(conversation, tmp_path):
    legacy = tmp_path / "core_memories.md"
    legacy.write_text("# Core Memories\n\n- Prior fictional fact\n")
    original = legacy.read_bytes()
    store = CoreMemoryStore()
    with conversation._continuity._write_session() as session:
        store.append("Never committed", session=session)
        # Closing the uncommitted session rolls back migration and insertion.
    assert "Never committed" not in store.load()
    assert store.append("New fictional fact")
    assert not store.append("New fictional fact")
    assert "Prior fictional fact" in store.load()
    assert "New fictional fact" in store.load()
    assert legacy.read_bytes() == original


@pytest.mark.parametrize("image", [False, True])
def test_sessionless_and_image_requests_use_owned_decisions(conversation, monkeypatch, image):
    from gamma.schemas.response import VisionAnalysis
    from gamma.vision.service import VisionImage
    conversation._identity.resolve = lambda ctx: SpeakerProfile("Fixture", "owner", is_owner=True)
    conversation._llm = SimpleNamespace(generate_reply=lambda *args, **kw: SimpleNamespace(text=json.dumps({
        "action": "tool_first", "requested_delivery": "text_only", "final_text": "Noted.",
        "tool_calls": [{"tool": "save_core_memory", "args": {"fact": "Owned image or text fact"}}]})))
    conversation._tools.get("save_core_memory").run = Mock(side_effect=AssertionError("direct write"))
    monkeypatch.setattr(conversation._vision, "prepare_image", lambda **kw: VisionImage(b"fictional", "image/png"))
    monkeypatch.setattr(conversation._vision, "analyze_image", lambda **kw: VisionAnalysis(summary="Fictional image"))
    with patch("gamma.conversation.service.build_system_prompt", return_value="Fixture"):
        for _ in range(2):
            if image:
                response = conversation.respond_with_image(user_text="Hello", image_bytes=b"fixture", image_media_type="image/png")
                assert response.vision.summary == "Fictional image"
            else:
                response = conversation.respond("Hello", fast_mode=True)
            assert response.route_trace_id
            assert response.tool_results[0].metadata["status"] == "committed"
    conversation._tools.get("save_core_memory").run.assert_not_called()
    with Session(conversation._continuity._engine) as session:
        entries = list(session.execute(select(ConversationJournalEntry)).scalars())
        ids = {entry.session_id for entry in entries}
    assert len(ids) == 2 and all(value.startswith("transient:") for value in ids)
    assert len(entries) == 4 and all(entry.completion_status == "completed" for entry in entries)
