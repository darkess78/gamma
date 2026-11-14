"""Not everything said becomes a memory (gamma.memory.triage)."""
import pytest

from gamma.config import settings
from gamma.evals.memory_triage import load
from gamma.memory.attribution import MemoryOrigin
from gamma.memory.service import MemoryService
from gamma.memory.triage import ModelJudge, obvious_skip, triage
from gamma.schemas.response import MemoryCandidate


def test_the_cheap_filter_never_drops_a_labelled_keep():
    keeps = [row["candidate_text"] for row in load() if row["label"] == "keep"]
    assert len(keeps) == 200
    assert [text for text in keeps if obvious_skip(text)] == []


@pytest.mark.parametrize("text", ["lol", "haha", "that sounds great!", "Shana said she would look into it",
                                  "use bullet points for this response only please"])
def test_obvious_non_memories_are_skipped(text):
    assert triage(text).keep is False


class FakeAdapter:
    def __init__(self, answer):
        self.answer = answer

    def generate_reply(self, *, system_prompt, user_text):
        assert "SEPARATE future conversation" in system_prompt
        return type("Reply", (), {"text": self.answer})()


def test_model_judge_reads_keep_or_skip():
    assert triage("The user prefers concise answers with the conclusion first.", ModelJudge(FakeAdapter("keep"))).keep
    assert not triage("The user is tired tonight after a long day.", ModelJudge(FakeAdapter("Skip."))).keep


@pytest.fixture
def memory(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_url", "sqlite:///" + str(tmp_path / "m.db"))
    monkeypatch.setattr(settings, "memory_enabled", True)
    monkeypatch.setattr(settings, "memory_write_mode", "selective")
    return MemoryService()


def test_automatic_candidates_are_triaged_but_explicit_saves_are_not(memory):
    automatic = MemoryOrigin(reporter_name="Owner", is_owner=True, source="local", privacy_scope="local_private")
    assert memory.persist_candidates([MemoryCandidate(type="episodic", text="lol")], origin=automatic) == 0
    explicit = MemoryOrigin(source="tool", privacy_scope="local_private")
    assert memory.persist_candidates([MemoryCandidate(type="episodic", text="lol")], origin=explicit) == 1
