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
    assert "never claim a long-held favorite" in discord


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


class FakeExtractor:
    def __init__(self, reply):
        self.reply, self.calls = reply, 0

    def generate_reply(self, system_prompt, user_text, **kwargs):
        from gamma.llm.base import LLMReply
        self.calls += 1
        return LLMReply(text=self.reply)


def opinion_service(memory, llm):
    from gamma.conversation.memory_candidates import MemoryCandidatesMixin

    class Service(MemoryCandidatesMixin, TurnMetadataMixin):
        def __init__(self):
            self._memory = memory

        def _llm_adapter(self):
            return llm
    return Service()


OWNER = MemoryOrigin(reporter_name="Owner", is_owner=True, source="dashboard", privacy_scope="local_private")


def test_extractor_stores_what_she_decided(memory):
    llm = FakeExtractor('{"opinions": ["I prefer rainy days to sunny ones", "not first person", "I prefer rainy days to sunny ones"]}')
    opinion_service(memory, llm)._background_own_opinions(
        "Rainy or sunny?", "Rain. It is quiet. Sun is loud and careless, I prefer the rain.", "s", OWNER)
    assert [o.fact_text for o in memory.own_opinions("rain", visibility={"owner_only"})] == ["I prefer rainy days to sunny ones"]


def test_extractor_skips_replies_without_a_view(memory):
    llm = FakeExtractor('{"opinions": ["I like everything"]}')
    opinion_service(memory, llm)._background_own_opinions("What time is it?", "It is ten past three.", "s", OWNER)
    assert llm.calls == 0 and memory.own_opinions("", visibility={"owner_only"}) == []


def test_extractor_tolerates_bad_json(memory):
    llm = FakeExtractor("no json here")
    opinion_service(memory, llm)._background_own_opinions("Pizza?", "Pineapple on pizza is a mistake.", "s", OWNER)
    assert llm.calls == 1 and memory.own_opinions("", visibility={"owner_only"}) == []


def test_restating_an_opinion_is_not_stored_twice(memory):
    decide(memory, "I think Hades is a solid game")
    llm = FakeExtractor('{"opinions": ["I think Hades is solid."]}')
    opinion_service(memory, llm)._background_own_opinions("Still?", "Still the same. Hades is solid.", "s", OWNER)
    assert [o.fact_text for o in memory.own_opinions("Hades", visibility={"owner_only"})] == ["I think Hades is a solid game"]


def test_a_new_stance_is_a_change_of_mind(memory):
    decide(memory, "I like Skyrim")
    llm = FakeExtractor('{"opinions": ["I dislike Skyrim"]}')
    opinion_service(memory, llm)._background_own_opinions("Skyrim?", "Skyrim is boring now. I dislike it.", "s", OWNER)
    assert [o.fact_text for o in memory.own_opinions("Skyrim", visibility={"owner_only"})] == ["I dislike Skyrim"]
