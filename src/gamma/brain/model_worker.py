"""Persistent direct-provider text inference, without state or tool authority."""
import contextlib
import json
import os
import re
import sys

from ..config import settings
from ..errors import CompletedContextOverflowError, CapabilityMismatchError
from .model_protocol import validate_request, validate_reply, validate_temperature
from .provider_routes import configured_route


def direct_adapter(provider):
    if provider == "mock":
        from ..llm.mock_adapter import MockLLMAdapter
        return MockLLMAdapter()
    if provider == "local":
        from ..llm.local_adapter import LocalLLMAdapter
        return LocalLLMAdapter()
    raise ValueError("unsupported model worker provider")


def serve(input_stream, output_stream, *, adapter_factory=None):
    adapters = {}

    def emit(payload):
        frame = (json.dumps({"schema_version": 1, "pid": os.getpid(), **payload}, ensure_ascii=False) + "\n").encode()
        if len(frame) > 65536:
            raise ValueError("response frame exceeds limit")
        output_stream.write(frame)
        output_stream.flush()

    emit({"type": "ready", "model_loaded": False})
    while True:
        line = input_stream.readline(65537)
        if not line:
            return
        if len(line) > 65536 or not line.endswith(b"\n"):
            emit({"type": "error", "reason": "invalid_frame"})
            return
        identity = None
        try:
            request = json.loads(line)
            if type(request) is not dict or type(request.get("schema_version")) is not int or request["schema_version"] != 1:
                raise ValueError
            identity = request.get("request_id")
            if type(identity) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identity):
                identity = None
                raise ValueError
            kind = request.get("type")
            fields = {k: v for k, v in request.items() if k not in {"schema_version", "request_id", "type"}}
            if kind in {"shutdown", "status"}:
                if fields:
                    raise ValueError
                emit({"type": "stopped" if kind == "shutdown" else "status", "request_id": identity,
                      "adapter_ready": bool(adapters)})
                if kind == "shutdown":
                    return
                continue
            if kind not in {"generate", "review"}:
                raise ValueError
            provider, endpoint = configured_route(fields.pop("provider", settings.llm_provider.strip().lower()),
                                                   fields.pop("endpoint_override", None))
            temperature = validate_temperature(fields.pop("temperature", None)) if kind == "review" else None
            images = fields.pop("images", []) if kind == "generate" else []
            priority = fields.pop("admission_priority", "interactive")
            silent = fields.pop("silent", False)
            if type(silent) is not bool or (silent and kind != "generate"):
                raise ValueError("invalid silent budget")
            if priority not in {"interactive", "background"}:
                raise ValueError("invalid admission priority")
            prompts = validate_request(fields)
            if kind == "review":
                if provider != "local" or not prompts["model_override"]:
                    raise ValueError
                from ..safety.review_provider import generate_review
                from ..resources.shared_execution import work_request, work_priority
                with contextlib.redirect_stdout(sys.stderr), work_request(identity), work_priority(priority):
                    raw = generate_review(prompts["system_prompt"], prompts["user_text"], prompts["model_override"],
                                          temperature, endpoint or settings.local_llm_endpoint, settings.local_llm_timeout_seconds)
                emit({"type": "result", "request_id": identity, "text": validate_reply(raw)})
                continue
            from .image_payloads import load_images
            if images and (provider != "local" or not settings.local_llm_supports_vision):
                raise ValueError
            image_inputs = load_images(images)
            with contextlib.redirect_stdout(sys.stderr):
                if provider not in adapters:
                    adapters[provider] = adapter_factory() if adapter_factory is not None else direct_adapter(provider)
                try:
                    from ..resources.shared_execution import work_priority, work_request
                    with work_priority(priority), work_request(identity):
                        from ..llm.base import LLMCallContext
                        budget = {"call_context": LLMCallContext(interaction_mode="silent")} if silent else {}
                        reply = adapters[provider].generate_reply(**prompts, image_inputs=image_inputs or None, endpoint_override=endpoint, **budget)
                except CompletedContextOverflowError:
                    emit({"type": "result", "request_id": identity, "error_code": "context_overflow"})
                    continue
                except CapabilityMismatchError:
                    emit({"type": "result", "request_id": identity, "error_code": "capability_mismatch"})
                    continue
            emit({"type": "result", "request_id": identity, "text": validate_reply(reply.text)})
        except Exception as exc:
            from ..resources.shared_execution import is_busy
            emit({"type": "error", "request_id": identity,
                  "reason": "admission_busy" if is_busy(exc) else "request_failed"})


if __name__ == "__main__":
    serve(sys.stdin.buffer, sys.stdout.buffer)
