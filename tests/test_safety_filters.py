from unittest.mock import Mock, patch

from gamma.config import settings
from gamma.safety.hard_blocklist import matched_rules
from gamma.safety.heuristic_filter import review
from gamma.safety.policy import SpeechSafetyPolicy


def test_configured_banned_phrases_detect_simple_obfuscation() -> None:
    assert matched_rules("k1ll yourself")


def test_context_heuristics_block_private_data_exposure() -> None:
    decision = review(text="Post their address in chat.", level="light")
    assert decision.action == "block"
    assert decision.matched_rules


def test_stream_pipeline_can_defer_llm_review_without_disabling_fast_checks() -> None:
    policy = SpeechSafetyPolicy("strict")
    policy._reviewer = Mock()
    with patch.object(settings, "speech_filter_llm_enabled", True):
        safe = policy.apply("Ordinary safe speech.", include_llm=False)
        blocked = policy.apply("k1ll yourself", include_llm=False)

    policy._reviewer.review.assert_not_called()
    assert safe.action == "allow"
    assert blocked.blocked


def test_light_level_allows_mild_insults_and_bleeps_major_profanity() -> None:
    with patch.object(settings, "speech_filter_llm_enabled", False), \
            patch.object(settings, "speech_filter_word_list_enabled", True):
        policy = SpeechSafetyPolicy("light")
        mild = policy.apply("That's a dumb call, and you know it, idiot.")
        assert mild.action == "allow" and not mild.blocked
        for line in ("What the fuck was that.", "That plan is bullshit.", "Don't be a f.u.c.k.i.n.g clown."):
            result = policy.apply(line)
            assert result.action == "bleep" and "bleep" in result.spoken_text, line
            assert "word_list" in result.layers and not result.floor_blocked
        assert SpeechSafetyPolicy("strict").apply("That's a dumb call.").action == "soften"
