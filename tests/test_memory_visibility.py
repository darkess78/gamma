"""Every memory records which audience it came from, and searches can filter by it."""
import pytest
from sqlmodel import Session

from gamma.config import settings
from gamma.memory.attribution import MemoryOrigin
from gamma.memory.models import EpisodicMemory
from gamma.memory.service import MemoryService
from gamma.schemas.response import MemoryCandidate


@pytest.fixture
def memory(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_url", "sqlite:///" + str(tmp_path / "memory.db"))
    monkeypatch.setattr(settings, "memory_enabled", True)
    monkeypatch.setattr(settings, "memory_write_mode", "selective")
    return MemoryService()


def write(memory, text, scope):
    origin = MemoryOrigin(reporter_name="Owner", is_owner=True, source="test", privacy_scope=scope)
    memory.persist_candidates([MemoryCandidate(type="episodic", text=text, importance=0.6)], origin=origin)


def test_visibility_follows_where_it_was_said(memory):
    write(memory, "private plan about the surprise party", "local_private")
    write(memory, "said on stream: the party theme is pirates", "public_stream")
    write(memory, "said on discord: the party is on saturday", "public_discord")
    with Session(memory._engine) as db:
        db.add(EpisodicMemory(summary="old row about the party, before visibility existed"))
        db.commit()
    everything = memory.search_memories("party", limit=10, cross_session=True)
    assert len(everything) == 4  # the owner's private view is unchanged
    stream = memory.search_memories("party", limit=10, cross_session=True, visibility={"stream"})
    assert [m.summary for m in stream] == ["said on stream: the party theme is pirates"]
    discord = memory.search_memories("party", limit=10, cross_session=True, visibility={"discord"})
    assert [m.summary for m in discord] == ["said on discord: the party is on saturday"]
    owner = {m.summary for m in memory.search_memories("party", limit=10, cross_session=True, visibility={"owner_only"})}
    assert owner == {"private plan about the surprise party", "old row about the party, before visibility existed"}


def test_facts_filter_the_same_way(memory):
    origin = MemoryOrigin(reporter_name="Owner", is_owner=True, source="test", privacy_scope="local_private")
    memory.persist_candidates([MemoryCandidate(type="fact", text="Owner's home city is Springfield")], origin=origin)
    assert memory.get_relevant_facts("home city", visibility={"stream"}) == []
    assert len(memory.get_relevant_facts("home city", visibility={"owner_only"})) == 1
