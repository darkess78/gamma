"""Changed facts replace only the matching older fact, which is kept as history."""
import pytest

from gamma.config import settings
from gamma.memory.attribution import MemoryOrigin
from gamma.memory.service import MemoryService
from gamma.schemas.response import MemoryCandidate


@pytest.fixture
def memory(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_url", "sqlite:///" + str(tmp_path / "m.db"))
    return MemoryService()


def say(memory, text, tag="preference"):
    memory.persist_candidates([MemoryCandidate(type="preference", text=text, importance=0.6, tags=[tag])])


def current(memory):
    return sorted(f.fact_text for f in memory.get_profile_facts(limit=50))


def test_different_likes_and_favourites_both_stay(memory):
    say(memory, "I like cats")
    say(memory, "I like pizza")
    say(memory, "My favorite game is Hollow Knight")
    say(memory, "My favorite food is ramen")
    assert current(memory) == ["I like cats", "I like pizza", "My favorite food is ramen", "My favorite game is Hollow Knight"]


def test_a_change_of_mind_replaces_and_keeps_the_old_value(memory):
    say(memory, "My favorite game is Hollow Knight")
    say(memory, "My favorite game is Balatro")
    say(memory, "I like mushrooms")
    say(memory, "I do not like mushrooms")
    facts = memory.get_profile_facts(limit=50)
    assert sorted(f.fact_text for f in facts) == ["I do not like mushrooms", "My favorite game is Balatro"]
    history = memory.previous_values([f.id for f in facts])
    by_text = {f.fact_text: history.get(f.id) for f in facts}
    assert by_text == {"My favorite game is Balatro": ["My favorite game is Hollow Knight"],
                       "I do not like mushrooms": ["I like mushrooms"]}
    assert all(f.fact_text != "My favorite game is Hollow Knight"
               for f in memory.get_relevant_facts("favorite game", limit=10))


def test_history_is_a_chain_newest_first(memory):
    for game in ("Celeste", "Hollow Knight", "Balatro"):
        say(memory, f"My favorite game is {game}")
    (fact,) = memory.get_profile_facts(limit=50)
    assert memory.previous_values([fact.id]) == {fact.id: ["My favorite game is Hollow Knight", "My favorite game is Celeste"]}


def test_going_back_to_an_old_value_makes_it_current_again(memory):
    say(memory, "My favorite game is Celeste")
    say(memory, "My favorite game is Balatro")
    say(memory, "My favorite game is Celeste")
    assert current(memory) == ["My favorite game is Celeste"]


def test_forgetting_a_fact_forgets_what_it_replaced(memory):
    say(memory, "My name is Sam", tag="identity")
    say(memory, "My name is Alex", tag="identity")
    (fact,) = memory.get_profile_facts(limit=50)
    memory.clear_selected([{"kind": "profile_fact", "id": fact.id}])
    assert memory.stats()["profile_count"] in (0, "0")


def test_undoing_a_recent_change_brings_the_old_value_back(memory):
    from datetime import datetime, timedelta, timezone
    from sqlmodel import Session, select
    from gamma.memory.models import ProfileFact
    say(memory, "My name is Sam", tag="identity")
    with Session(memory._engine) as db:  # make the first fact old
        old = db.exec(select(ProfileFact)).one(); old.created_at = datetime.now(timezone.utc) - timedelta(days=1)
        db.add(old); db.commit()
    say(memory, "My name is Alex", tag="identity")
    memory.clear_recent(minutes=10)
    assert current(memory) == ["My name is Sam"]


def test_owner_prompt_says_what_changed_and_shared_prompt_says_nothing(memory):
    from gamma.identity.resolver import IdentityResolver
    from gamma.persona.loader import build_system_prompt
    from gamma.schemas.conversation import RecipientContext
    say(memory, "My favorite game is Celeste")
    say(memory, "My favorite game is Balatro")
    owner = IdentityResolver().owner_profile()
    private = build_system_prompt(memory_service=memory, user_text="what's my favorite game", speaker=owner,
                                  recipient=RecipientContext(kind="owner_private", owner_authorized=True))
    assert "My favorite game is Balatro (previously: My favorite game is Celeste)" in private
    shared = build_system_prompt(memory_service=memory, user_text="what's my favorite game", speaker=owner,
                                 recipient=RecipientContext(kind="shared"))
    assert "Celeste" not in shared and "Balatro" not in shared
