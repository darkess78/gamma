"""Shana's own opinions: stored from her turns, recalled into her prompt, kept per audience."""
import pytest

from gamma.config import settings
from gamma.conversation.turn_metadata import TurnMetadataMixin
from gamma.memory.attribution import MemoryOrigin
from gamma.memory.service import OWN_OPINION, MemoryService
from gamma.persona import loader
from gamma.schemas.conversation import RecipientContext
from gamma.schemas.response import MemoryCandidate

STREAM = RecipientContext(kind="shared", target_policy="stream_public")
DISCORD = RecipientContext(kind="shared", target_policy="discord_call")
PRIVATE = RecipientContext(kind="owner_private", owner_authorized=True)


@pytest.fixture
def memory(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_url", "sqlite:///" + str(tmp_path / "memory.db"))
    monkeypatch.setattr(settings, "memory_enabled", True)
    monkeypatch.setattr(settings, "memory_write_mode", "selective")
    monkeypatch.setattr(settings, "assistant_state_enabled", False)
    return MemoryService()


def decide(memory, text, *, scope="local_private", source="dashboard"):
    origin = MemoryOrigin(reporter_name="Owner", is_owner=True, source=source, privacy_scope=scope)
    return memory.persist_candidates([MemoryCandidate(type="opinion", text=text, subject_type="shana")], origin=origin)


def section(prompt, name):
    return prompt.split("# " + name + "\n", 1)[1].split("\n\n#", 1)[0]


def test_opinion_is_stored_as_hers_not_the_users(memory):
    assert decide(memory, "I like melon bread.") == 1
    [opinion] = memory.own_opinions("melon bread", visibility={"owner_only"})
    assert (opinion.subject_type, opinion.category, opinion.fact_text) == (OWN_OPINION, "opinion", "I like melon bread")
    assert memory.get_relevant_facts("melon bread", subject_type="primary_user") == []
    assert decide(memory, "I like melon bread") == 0  # a repeat is not a second opinion


def test_changing_her_mind_replaces_the_old_opinion_across_channels(memory):
    decide(memory, "I like Skyrim", source="voice")
    decide(memory, "I dislike Skyrim", source="dashboard")
    [current] = memory.own_opinions("Skyrim", visibility={"owner_only"})
    assert current.fact_text == "I dislike Skyrim"
    assert memory.previous_values([current.id]) == {current.id: ["I like Skyrim"]}


def test_a_stream_turn_cannot_overwrite_a_private_opinion(memory):
    decide(memory, "I like Skyrim")
    decide(memory, "I dislike Skyrim", scope="public_stream", source="twitch")
    assert [o.fact_text for o in memory.own_opinions("Skyrim", visibility={"owner_only"})] == ["I like Skyrim"]
    assert [o.fact_text for o in memory.own_opinions("Skyrim", visibility={"stream"})] == ["I dislike Skyrim"]


def test_reactions_are_not_opinions(memory):
    assert decide(memory, "lol") == 0


def test_prompt_shows_opinions_per_audience(memory):
    decide(memory, "I think pineapple on pizza is fine")
    decide(memory, "I like rhythm games", scope="public_stream", source="twitch")
    private = section(loader.build_system_prompt(memory, "pizza and games", recipient=PRIVATE), "My Own Opinions")
    assert "pineapple on pizza is fine" in private and "rhythm games" in private
    stream = section(loader.build_system_prompt(memory, "pizza and games", recipient=STREAM), "My Own Opinions")
    assert "rhythm games" in stream and "pineapple" not in stream
    discord = section(loader.build_system_prompt(memory, "pizza and games", recipient=DISCORD), "My Own Opinions")
    assert "None stored" in discord
    assert "never invent a settled favorite" in discord


def test_previous_opinion_is_shown_as_history(memory):
    decide(memory, "I like Skyrim")
    decide(memory, "I dislike Skyrim")
    block = section(loader.build_system_prompt(memory, "Skyrim", recipient=PRIVATE), "My Own Opinions")
    assert "- I dislike Skyrim (you used to think: I like Skyrim)" in block


def test_metadata_extractor_keeps_shana_subject():
    class Extractor(TurnMetadataMixin):
        @staticmethod
        def _normalize_subject_name(value):
            return value

    extractor = Extractor()
    [candidate] = extractor._normalize_memory_candidates(
        [{"type": "opinion", "text": "I think horror games are overrated", "subject_type": "shana"}])
    assert candidate.subject_type == "shana"


def test_dashboard_entries_for_shana_count_as_her_opinions(memory):
    memory.create_item({"kind": "profile_fact", "summary": "I like quiet mornings", "subject_type": "assistant"})
    assert [o.fact_text for o in memory.own_opinions("mornings", visibility={"owner_only"})] == ["I like quiet mornings"]
