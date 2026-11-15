"""Real-time turns ask gpt-oss for low reasoning effort; everything else keeps the model default."""
import pytest

from gamma.config import settings
from gamma.llm import local_adapter
from gamma.llm.base import LLMCallContext
from gamma.llm.local_adapter import LocalLLMAdapter, reasoning_effort
from tests.pairwise import pairwise


@pytest.mark.parametrize(*pairwise(
    ("model", ["gpt-oss:20b", "qwen3.5:4b"]),
    ("fast", [True, False]),
    ("depth", ["light", "normal", "heavy"]),
    ("setting", ["low", ""]),
))
def test_reasoning_effort(monkeypatch, model, fast, depth, setting):
    monkeypatch.setattr(settings, "local_llm_fast_reasoning", setting)
    context = LLMCallContext(fast_mode=fast, reasoning_depth=depth)
    wanted = bool(setting) and model.startswith("gpt-oss") and (depth == "light" or (fast and depth != "heavy"))
    assert reasoning_effort(model, context) == ("low" if wanted else None)


def test_no_context_keeps_default(monkeypatch):
    monkeypatch.setattr(settings, "local_llm_fast_reasoning", "low")
    assert reasoning_effort("gpt-oss:20b", None) is None


def test_payload_carries_think_only_for_fast_turns(monkeypatch):
    import json
    sent = []
    monkeypatch.setattr(settings, "local_llm_fast_reasoning", "low")
    monkeypatch.setattr(settings, "local_llm_model", "gpt-oss:20b")
    monkeypatch.setattr(settings, "local_llm_enable_routing", False)
    adapter = LocalLLMAdapter()
    monkeypatch.setattr(adapter, "_post_generate", lambda endpoint, body: sent.append(json.loads(body)) or {"response": "hi"})
    monkeypatch.setattr(local_adapter, "model_capability", lambda *a: type("C", (), {"runtime_context_tokens": None})())
    adapter.generate_reply("sys", "hello", call_context=LLMCallContext(fast_mode=True))
    adapter.generate_reply("sys", "hello", call_context=LLMCallContext(fast_mode=False))
    assert sent[0]["think"] == "low"
    assert "think" not in sent[1]


@pytest.mark.parametrize("model, purpose, wanted", [
    ("qwen3.8:27b", "conversation_draft", True),
    ("qwen3.8:27b", "conversation_structured_repair", True),
    ("qwen3.8:27b", "tool_finalizer", True),
    ("qwen3.8:27b", "metadata_extraction", False),  # not a turn object
    ("gpt-oss:20b", "conversation_draft", False),   # already follows the format; left as measured
])
def test_turn_calls_ask_for_json(monkeypatch, model, purpose, wanted):
    import json
    sent = []
    monkeypatch.setattr(settings, "local_llm_model", model)
    monkeypatch.setattr(settings, "local_llm_enable_routing", False)
    adapter = LocalLLMAdapter()
    monkeypatch.setattr(adapter, "_post_generate", lambda endpoint, body: sent.append(json.loads(body)) or {"response": "{}"})
    monkeypatch.setattr(local_adapter, "model_capability", lambda *a: type("C", (), {"runtime_context_tokens": None})())
    adapter.generate_reply("sys", "hello", call_context=LLMCallContext(purpose=purpose))
    assert (sent[0].get("format") == "json") is wanted
    assert local_adapter.wants_json(model, None) is False
