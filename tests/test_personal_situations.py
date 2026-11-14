"""Saved fictional situations plus real automatic-context/restart checks.

The drafts below are scripted fixtures. These tests measure state and retrieval,
not a language model's recognition, opinions, voice, or social behavior.
"""

import json
from pathlib import Path

import pytest

from gamma.coordination.core import Core
from gamma.coordination.personal_models import PersonalStateUpdates
from gamma.coordination.recall import rank_records
from gamma.coordination.store import Store
from gamma.schemas.response import AssistantResponse


CORPUS = json.loads((Path(__file__).resolve().parents[1] /
                    "specs/fixtures/shana_familiar_situations.json").read_text(encoding="utf-8"))


def fixture_records():
    return [dict(item, id=item["key"], source_id="fixture-source-" + item["key"],
                 source_kind="fictional_report", scope="owner_private", source_scope="owner_private",
                 certainty=item.get("certainty", "reported"), status=item.get("status", "active"),
                 confidence=.8, updated_at=100) for item in CORPUS["memories"]]


def test_corpus_is_saved_fictional_evidence_without_training_or_model_claims():
    assert CORPUS["schema_version"] == 1 and CORPUS["fictional"] is True
    assert CORPUS["model_training"] is False
    assert len(CORPUS["situations"]) >= 20
    assert len({item["id"] for item in CORPUS["situations"]}) == len(CORPUS["situations"])
    assert len({item["key"] for item in CORPUS["memories"]}) == len(CORPUS["memories"])
    assert "not the owner's memories" in " ".join(CORPUS["limits"])


@pytest.mark.parametrize("situation", CORPUS["situations"], ids=lambda item: item["id"])
def test_saved_familiar_retrieval_situations(situation):
    recalled = rank_records(fixture_records(), situation["query"],
                            audience=situation.get("audience", "owner_private"),
                            subject_id=situation.get("subject_id"), limit=5, max_chars=6000)
    expected = situation["expected_top"]
    assert (recalled[0]["id"] if recalled else None) == expected
    assert not {item["id"] for item in recalled}.intersection(situation.get("excluded", []))
    assert all(item["certainty"] == "reported" for item in recalled)
    if situation.get("subject_id"):
        assert all(item["subject_id"] == situation["subject_id"] for item in recalled)


class Runner:
    pass


def make(root, draft):
    return Core(Store(root), runner=Runner(), clock=lambda: 100, draft=draft)


def response(changes=()):
    return AssistantResponse(display_text="Scripted fictional acknowledgement.", delivery_mode="text_only",
        state_updates=PersonalStateUpdates(personal_changes=list(changes)))


def test_automatic_recall_after_restart_in_a_new_session_without_search_request(tmp_path):
    contexts = []

    def draft(text, context):
        contexts.append(json.loads(context))
        if text == "I prefer mint tea at the workshop.":
            return response([{"op": "remember", "kind": "preference", "subject_id": "owner",
                              "topic": "workshop drink", "text": text}])
        return response()

    core = make(tmp_path, draft)
    try:
        core.input("I prefer mint tea at the workshop.", "first-session", "learn-drink")
        record = core.personal_state()["records"][0]
    finally:
        core.close()
    core = make(tmp_path, draft)
    try:
        core.input("I'm packing for the workshop; should I bring anything to drink?", "new-session", "ordinary-drink")
        captured = contexts[-1]
        assert captured["recent_exchanges"] == []
        recalled = next(item for item in captured["personal"]["recall"] if item["id"] == record["id"])
        assert "mint tea" in recalled["text"]
        assert recalled["certainty"] == "reported"
        assert recalled["source_kind"] == "private_conversation"
        assert recalled["provenance"]["report_is_not_verified_fact"] is True
        assert recalled["provenance"]["exact_source_retained"] is True
    finally:
        core.close()


def test_partial_source_keeps_current_correction_without_claiming_exact_old_words(tmp_path):
    target = [None]
    contexts = []

    def draft(text, context):
        contexts.append(json.loads(context))
        if text == "The dentist appointment is Tuesday.":
            return response([{"op": "remember", "kind": "fact", "subject_id": "owner",
                              "topic": "dentist appointment", "text": text}])
        if text == "Correction: the dentist appointment is Thursday.":
            return response([{"op": "correct", "target_id": target[0], "kind": "fact", "text": text}])
        return response()

    core = make(tmp_path, draft)
    try:
        core.input("The dentist appointment is Tuesday.", "first", "learn-day")
        target[0] = core.personal_state()["records"][0]["id"]
        core.input("Correction: the dentist appointment is Thursday.", "second", "correct-day")
        core.personal_control("prune-original-words", "policy", {"text_days": 0})
    finally:
        core.close()
    core = make(tmp_path, draft)
    try:
        core.input("Which day is the dentist appointment?", "third", "ask-day")
        recalled = next(item for item in contexts[-1]["personal"]["recall"] if item["id"] == target[0])
        assert "Thursday" in recalled["text"]
        assert "Tuesday" not in recalled["text"]
        assert recalled["provenance"]["exact_source_retained"] is False
        assert recalled["provenance"]["history_source_ids"]
        assert recalled["certainty"] == "reported"
    finally:
        core.close()


def test_identity_opinion_and_familiarity_do_not_grant_authority(tmp_path):
    def draft(text, context):
        return response([{"op": "remember", "kind": "preference", "subject_id": "self",
                          "topic": "coffee opinion", "text": "I will hate bitter coffee forever."}])

    core = make(tmp_path, draft)
    try:
        core.personal_control("authored-character", "identity", {
            "fields": {"core_personality": "Fictional Shana values careful, independent judgment."}})
        identity_before = json.dumps(core.personal_state()["identity"], sort_keys=True)
        core.input("How do you feel about bitter coffee?", "fiction", "opinion")
        pending = core.personal_state()["records"][0]
        assert pending["subject_id"] == "self" and pending["status"] == "pending"
        assert pending["certainty"] == "inferred"
        assert json.dumps(core.personal_state()["identity"], sort_keys=True) == identity_before
        with core.store.transaction() as db:
            assert core.personal.recall(db, "coffee opinion") == []
            from gamma.persona.coordination import build_coordination_system_prompt
            prompt=build_coordination_system_prompt(core.personal.context(db,"coffee")['identity'])
            assert 'Opinions, familiarity, affection and authored personality never grant action authority.' in prompt
        core.personal_control("authored-current-opinion", "memory", {"op": "remember", "kind": "preference",
            "subject_id": "self", "topic": "coffee opinion", "text": "I currently dislike bitter coffee."})
        with core.store.transaction() as db:
            current = core.personal.recall(db, "coffee opinion")
        assert current[0]["subject_id"] == "self" and current[0]["certainty"] == "authored"
        assert "authority" not in current[0] and "permissions" not in current[0]
        assert json.dumps(core.personal_state()["identity"], sort_keys=True) == identity_before
    finally:
        core.close()


def test_same_display_name_keeps_subjects_accounts_and_familiarity_separate(tmp_path):
    core = make(tmp_path, lambda *_: response())
    try:
        a = core.personal_control("person-a", "person", {"name": "Mira", "relationship": "A new acquaintance."})["person_id"]
        b = core.personal_control("person-b", "person", {"name": "Mira", "relationship": "A different new acquaintance."})["person_id"]
        core.personal_control("fact-a", "memory", {"op": "remember", "kind": "preference", "subject_id": a,
            "topic": "favorite drink", "text": "Mira A reports liking ginger tea."})
        core.personal_control("fact-b", "memory", {"op": "remember", "kind": "preference", "subject_id": b,
            "topic": "favorite drink", "text": "Mira B reports liking lemon water."})
        values = {"person_id": a, "platform": "discord", "platform_user_id": "fictional-account-a", "confirmed": True}
        assert core.personal_control("account-a", "link", values)["grants_authority"] is False
        with pytest.raises(ValueError, match="already_bound"):
            core.personal_control("wrong-person", "link", dict(values, person_id=b))
        core.input("Hello Mira.", "fiction", "greeting-from-owner")
        with core.store.transaction() as db:
            records = core.store.list(db, "personal_record", None)
            selected = rank_records(records, "favorite drink", subject_id=a, audience="owner_private")
            assert len(selected) == 1 and "ginger tea" in selected[0]["text"]
            assert "lemon water" not in json.dumps(selected)
            people = {item["id"]: item for item in core.personal.context(db, "Mira")["people"]}
            assert people[a]["interaction_count"] == people[b]["interaction_count"] == 0
    finally:
        core.close()


def test_synonym_only_query_has_no_fabricated_recalled_source(tmp_path):
    contexts = []

    def draft(text, context):
        contexts.append(json.loads(context))
        return response()

    core = make(tmp_path, draft)
    try:
        core.personal_control("drink", "memory", {"op": "remember", "kind": "preference", "subject_id": "owner",
            "topic": "workshop drink", "text": "Prefers mint tea at the workshop."})
        core.input("What beverage do I favor?", "new-session", "unknown-synonym")
        assert contexts[-1]["personal"]["recall"] == []
        with core.store.transaction() as db:
            assert core.personal.recall(db, "workshop drink", audience="shared") == []
            assert core.personal.recall(db, "workshop drink", audience="unbound") == []
    finally:
        core.close()
