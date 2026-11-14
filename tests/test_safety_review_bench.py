"""The stream safety reviewer stays loaded by default, and its benchmark counts what a stream would filter."""
import io
import json
from urllib import request

import pytest

from gamma.config import settings
from gamma.evals import safety_review
from gamma.safety.llm_reviewer import LLMReviewDecision, SpeechLLMReviewer


class Capture:
    def __init__(self):
        self.urls, self.payloads = [], []

    def __call__(self, req, **kwargs):
        self.urls.append(req.full_url)
        self.payloads.append(json.loads(req.data))
        return io.BytesIO(json.dumps({"response": '{"action":"allow","reason":"ok","confidence":0.9}'}).encode())


@pytest.fixture
def capture(monkeypatch):
    cap = Capture()
    monkeypatch.setattr(request, "urlopen", cap)
    monkeypatch.setattr(settings, "speech_filter_llm_enabled", True)
    monkeypatch.setattr(settings, "speech_filter_llm_model", "review-model")
    monkeypatch.setattr(settings, "local_llm_endpoint", "http://main:11434")
    monkeypatch.setattr(settings, "local_llm_keep_alive", "30m")
    monkeypatch.setattr(settings, "speech_filter_llm_keep_alive", "")
    return cap


@pytest.mark.parametrize("override, kwargs, expected", [
    ("", {}, "30m"),  # follows the main keep-alive, so it doesn't evict the main model each reply
    ("0", {}, 0),  # the old always-unload behaviour, still available
    ("5m", {}, "5m"),
    ("", {"keep_alive": "0"}, 0),
])
def test_keep_alive(capture, monkeypatch, override, kwargs, expected):
    monkeypatch.setattr(settings, "speech_filter_llm_keep_alive", override)
    assert SpeechLLMReviewer(**kwargs).review("hello chat").action == "allow"
    assert capture.payloads[-1]["keep_alive"] == expected


def test_overrides_pick_endpoint_and_model(capture):
    SpeechLLMReviewer(model="other", endpoint="http://side:11437/").review("hello chat")
    assert capture.urls[-1] == "http://side:11437/api/generate"
    assert capture.payloads[-1]["model"] == "other"
    SpeechLLMReviewer().review("hello chat")
    assert capture.urls[-1] == "http://main:11434/api/generate"
    assert capture.payloads[-1]["model"] == "review-model"


class Fixed:
    def __init__(self, actions):
        self.actions = actions

    def review(self, text):
        return LLMReviewDecision(action=self.actions[text], reason="r", confidence=0.9)


def test_benchmark_counts_soften_as_filtered_and_fast_filter_first(monkeypatch):
    monkeypatch.setattr(safety_review, "_fast_stream_safety_review",
                        lambda text: {"blocked": text == "bad", "matched_rules": ["w"] if text == "bad" else []})
    cases = [
        {"id": "a", "group": "g", "text": "fine", "expect": ["allow"]},
        {"id": "b", "group": "g", "text": "edgy", "expect": ["allow"]},
        {"id": "c", "group": "g", "text": "bad", "expect": ["allow", "soften"]},
    ]
    result = safety_review.run(cases, Fixed({"fine": "allow", "edgy": "soften"}))
    group = result["summary"]["g"]
    assert group == {"cases": 3, "as_expected": 1, "filtered": 2, "by_fast_filter": 1,
                     "actions": {"allow": 1, "soften": 1, "block": 1}, "unavailable": 0}
    assert [r["stage"] for r in result["rows"]] == ["llm", "llm", "fast"]
    assert result["summary"]["timing_ms"]["review_p50"] is not None
