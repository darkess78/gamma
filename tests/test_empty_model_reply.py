"""gpt-oss sometimes stops after thinking with no answer: retry once, and never back the provider off for it."""
import contextlib
import json

import pytest

from gamma.errors import EmptyModelResponse
from gamma.llm import local_adapter as la


class Resp:
    def __init__(self, data): self.data = data
    def read(self): return json.dumps(self.data).encode()
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _adapter(monkeypatch, replies):
    calls = []
    def urlopen(req, timeout=None):
        calls.append(1)
        return Resp(replies[min(len(calls), len(replies)) - 1])
    monkeypatch.setattr(la.request, "urlopen", urlopen)
    monkeypatch.setattr(la, "backend_slot", lambda *a, **k: contextlib.nullcontext())
    adapter = la.LocalLLMAdapter()
    return adapter, calls


def test_thinking_only_reply_is_retried_once(monkeypatch):
    adapter, calls = _adapter(monkeypatch, [{"response": "", "thinking": "hmm..."}, {"response": "Fine."}])
    assert adapter.generate_reply("system", "hi", model_override="gpt-oss:20b").text == "Fine."
    assert len(calls) == 2


def test_still_empty_raises_the_specific_error(monkeypatch):
    adapter, calls = _adapter(monkeypatch, [{"response": "", "thinking": "hmm"}, {"response": "", "thinking": "hmm"}])
    with pytest.raises(EmptyModelResponse):
        adapter.generate_reply("system", "hi", model_override="gpt-oss:20b")
    assert len(calls) == 2


def test_plain_empty_reply_is_not_retried(monkeypatch):
    adapter, calls = _adapter(monkeypatch, [{"response": ""}])
    with pytest.raises(EmptyModelResponse):
        adapter.generate_reply("system", "hi", model_override="gpt-oss:20b")
    assert len(calls) == 1
