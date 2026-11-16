from __future__ import annotations

import json

import pytest

from gamma.config import settings
from gamma.conversation.service import _forget_floor_blocked_draft
from gamma.safety import floor_log
from gamma.safety.llm_reviewer import LLMReviewDecision, SpeechLLMReviewer
from gamma.safety.policy import SpeechSafetyPolicy
from gamma.schemas.response import ConversationTurnDraft


@pytest.fixture(autouse=True)
def _lists(monkeypatch, tmp_path):
    floor = tmp_path / "floor.txt"
    floor.write_text("# floor\nzorblat\n", encoding="utf-8")
    words = tmp_path / "words.txt"
    words.write_text("# words\nfrick\nheck off\n", encoding="utf-8")
    monkeypatch.setattr(settings, "speech_filter_floor_words_path", str(floor))
    monkeypatch.setattr(settings, "speech_filter_banned_words_path", str(words))
    monkeypatch.setattr(settings, "speech_filter_hard_block_enabled", True)
    monkeypatch.setattr(settings, "speech_filter_heuristic_enabled", True)
    monkeypatch.setattr(settings, "speech_filter_llm_enabled", False)
    monkeypatch.setattr(settings, "speech_filter_word_list_enabled", True)
    monkeypatch.setattr(settings, "speech_filter_extra_enabled", True)
    monkeypatch.setattr(settings, "speech_filter_curse_limit", 1)
    monkeypatch.setattr(settings, "speech_filter_auto_rewrite", True)
    monkeypatch.setattr(floor_log, "floor_log_path", lambda: tmp_path / "floor_events.jsonl")


def _with_reviewer(policy: SpeechSafetyPolicy, monkeypatch, category: str) -> SpeechSafetyPolicy:
    from gamma.safety.llm_reviewer import CATEGORY_ACTIONS

    monkeypatch.setattr(settings, "speech_filter_llm_enabled", True)
    policy._reviewer.review = lambda text: LLMReviewDecision(  # type: ignore[method-assign]
        action=CATEGORY_ACTIONS[category], reason=category, confidence=0.9)
    return policy


@pytest.mark.parametrize("level", ["strict", "light", "none"])
def test_floor_stops_the_whole_reply_at_every_level_and_logs_it_for_the_admin(level):
    result = SpeechSafetyPolicy(level).apply("Fine. You are a z0rbl@t, honestly.", source="direct_voice",
                                            session_id="s1")
    assert result.floor_blocked and result.spoken_text == "" and result.action == "floor_block"
    [event] = floor_log.recent_floor_events()
    assert event["text"] == "Fine. You are a z0rbl@t, honestly." and event["source"] == "direct_voice"
    assert event["session_id"] == "s1" and event["layers"] == ["floor_list"]


def test_word_list_bleeps_words_instead_of_dropping_the_reply():
    result = SpeechSafetyPolicy("light").apply("Oh frick. Heck off, that boss again.")
    assert not result.blocked and result.action == "bleep" and result.tiers == ["word_list"]
    assert result.spoken_text == "Oh bleep. bleep, that boss again."


def test_extra_safety_caps_curses_when_the_word_list_is_off(monkeypatch):
    monkeypatch.setattr(settings, "speech_filter_word_list_enabled", False)
    result = SpeechSafetyPolicy("light").apply("frick this, frick that, frick everything")
    assert result.spoken_text == "frick this, bleep that, bleep everything"
    assert result.tiers == ["extra"] and not result.blocked


def test_both_optional_tiers_off_lets_curses_through_but_not_the_floor(monkeypatch):
    monkeypatch.setattr(settings, "speech_filter_word_list_enabled", False)
    monkeypatch.setattr(settings, "speech_filter_extra_enabled", False)
    policy = SpeechSafetyPolicy("light")
    assert policy.apply("frick frick frick").spoken_text == "frick frick frick"
    assert policy.apply("zorblat").floor_blocked


def test_reviewer_severe_category_is_the_floor_and_rude_is_extra_safety(monkeypatch):
    hate = _with_reviewer(SpeechSafetyPolicy("light"), monkeypatch, "hate").apply("some line")
    assert hate.floor_blocked and hate.layers == ["llm"] and hate.matched_rules == ["hate"]

    rude = _with_reviewer(SpeechSafetyPolicy("light"), monkeypatch, "rude").apply("some line")
    assert rude.action == "soften" and not rude.floor_blocked

    monkeypatch.setattr(settings, "speech_filter_extra_enabled", False)
    rude_off = _with_reviewer(SpeechSafetyPolicy("light"), monkeypatch, "rude").apply("some line")
    assert rude_off.action == "allow" and rude_off.spoken_text == "some line"
    hate_off = _with_reviewer(SpeechSafetyPolicy("none"), monkeypatch, "hate").apply("some line")
    assert hate_off.floor_blocked


def test_profanity_verdict_does_not_soften_a_reply_the_word_list_already_bleeped(monkeypatch):
    result = _with_reviewer(SpeechSafetyPolicy("light"), monkeypatch, "profanity").apply("frick, missed again")
    assert result.action == "bleep" and result.spoken_text == "bleep, missed again"


def test_mild_insults_soften_only_at_strict():
    assert SpeechSafetyPolicy("light").apply("You idiot, the door was right there.").action == "allow"
    assert SpeechSafetyPolicy("strict").apply("You idiot, the door was right there.").action == "soften"


def test_reviewer_maps_categories_and_still_reads_old_action_answers(monkeypatch):
    monkeypatch.setattr(settings, "speech_filter_llm_enabled", True)
    reviewer = SpeechLLMReviewer(model="m", endpoint="http://127.0.0.1:1")
    answers = iter(['{"category":"minors","confidence":0.9}', '{"category":"none","confidence":0.8}',
                    '{"action":"soften","reason":"harsh","confidence":0.7}', '{"category":"made_up","confidence":0.5}'])

    class _Resp:
        def __init__(self, body):
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"response": self._body}).encode()

    monkeypatch.setattr("gamma.safety.llm_reviewer.request.urlopen", lambda req, timeout: _Resp(next(answers)))
    assert (reviewer.review("x").action, reviewer.review("x").action) == ("block", "allow")
    assert reviewer.review("x").reason == "harsh"
    assert reviewer.review("x").reason == "llm-reviewer-unavailable"  # unknown category fails closed


def test_forgetting_a_floor_blocked_draft_keeps_nothing_shana_could_read_back():
    draft = ConversationTurnDraft.model_validate({
        "action": "reply", "requested_delivery": "speech", "final_text": "bad line",
        "internal_summary": "said the bad line", "motions": ["nod"],
        "memory_candidates": [{"type": "fact", "text": "bad line", "importance": 0.5}],
        "state_updates": {"current_objective": "keep saying it"},
    })
    forgotten = _forget_floor_blocked_draft(draft)
    assert forgotten.action == "stay_silent" and forgotten.final_text == ""
    assert forgotten.internal_summary is None and not forgotten.memory_candidates and not forgotten.motions
    assert forgotten.state_updates.model_dump(exclude_none=True) in ({}, {"relationship_signals": []})
    assert forgotten.reason_code == "safety_floor"


def test_dashboard_saves_apply_without_a_restart(monkeypatch):
    from gamma.safety import policy as policy_module

    policy = SpeechSafetyPolicy("light")
    assert policy.apply("oh frick").spoken_text == "oh bleep"
    # A dashboard save changes the config file; the switches follow it.
    monkeypatch.setattr(policy_module, "_STARTUP_MTIMES", (-1.0, -1.0))
    monkeypatch.setattr(policy_module, "_switch_cache", {})
    monkeypatch.setattr(policy_module, "load_app_file_config",
                        lambda: {"speech_filter_level": "light", "speech_filter_word_list_enabled": False,
                                 "speech_filter_extra_enabled": False})
    assert policy.apply("oh frick").spoken_text == "oh frick"
    assert policy.apply("zorblat").floor_blocked  # never the floor
