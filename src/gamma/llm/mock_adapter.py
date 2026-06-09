from __future__ import annotations

from ..errors import ConfigurationError
from .base import LLMAdapter, LLMCallContext, LLMImageInput, LLMReply


class MockLLMAdapter(LLMAdapter):
    def generate_reply(
        self,
        system_prompt: str,
        user_text: str,
        image_inputs: list[LLMImageInput] | None = None,
        *,
        call_context: LLMCallContext | None = None,
        model_override: str | None = None,
    ) -> LLMReply:
        _ = system_prompt
        _ = call_context
        _ = model_override
        if image_inputs:
            raise ConfigurationError("The mock LLM provider does not support image input.")
        return LLMReply(text=f"I heard you say: {user_text}")
