from __future__ import annotations

from .base import LLMAdapter, LLMReply


class MockLLMAdapter(LLMAdapter):
    def generate_reply(self, system_prompt: str, user_text: str) -> LLMReply:
        _ = system_prompt
        return LLMReply(text=f"I heard you say: {user_text}")
