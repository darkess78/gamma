import io
import json
from unittest.mock import Mock

import pytest

from gamma.brain.model_worker import serve, direct_adapter
from gamma.brain.model_protocol import validate_request, validate_reply
from gamma.config import settings
from gamma.llm.base import LLMReply


def run(requests, factory):
    stream = io.BytesIO(b"".join((json.dumps({"schema_version": 1, "request_id": str(i), **r}) + "\n").encode() for i, r in enumerate(requests)))
    output = io.BytesIO()
    serve(stream, output, adapter_factory=factory)
    return [json.loads(line) for line in output.getvalue().splitlines()]


def test_lazy_reused_adapter_and_content_free_errors():
    adapter = Mock()
    adapter.generate_reply.side_effect = [LLMReply("first"), RuntimeError("private prompt"), LLMReply("third")]
    factory = Mock(return_value=adapter)
    prompt = {"type": "generate", "system_prompt": "system", "user_text": "fictional"}
    frames = run([{"type": "status"}, prompt, prompt, prompt, {"type": "shutdown"}], factory)
    assert [f["type"] for f in frames] == ["ready", "status", "result", "error", "result", "stopped"]
    assert frames[1]["adapter_ready"] is False
    assert frames[-2]["text"] == "third"
    assert "private prompt" not in json.dumps(frames)
    factory.assert_called_once()
    assert adapter.generate_reply.call_count == 3


@pytest.mark.parametrize("fields", [
    {"system_prompt": "x", "user_text": "y", "endpoint_override": "http://invalid"},
    {"system_prompt": "x", "user_text": "y", "image_inputs": []},
    {"system_prompt": "x", "user_text": "é" * 17000},
    {"system_prompt": "x", "user_text": "\ud800"},
    {"system_prompt": "x", "user_text": "y", "model_override": "a b"},
])
def test_invalid_inputs_never_construct_provider(fields):
    factory = Mock()
    assert run([{"type": "generate", **fields}], factory)[-1]["type"] == "error"
    factory.assert_not_called()


def test_protocol_byte_bounds():
    assert validate_request({"system_prompt": "", "user_text": "é" * 16384})["user_text"]
    assert validate_reply("é" * 8192)
    for value in ["é" * 8193, "\ud800", "\x00", 42, None]:
        with pytest.raises(ValueError):
            validate_reply(value)


def test_child_builds_only_direct_adapters_even_with_router_enabled(monkeypatch):
    monkeypatch.setattr(settings, "llm_router_enabled", True)
    from gamma.llm.mock_adapter import MockLLMAdapter
    assert isinstance(direct_adapter("mock"), MockLLMAdapter)
    with pytest.raises(ValueError, match="unsupported"):
        direct_adapter("openai")
