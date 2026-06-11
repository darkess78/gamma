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
