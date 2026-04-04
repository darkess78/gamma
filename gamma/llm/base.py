from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class LLMReply:
    text: str


class LLMAdapter:
    def generate_reply(self, system_prompt: str, user_text: str) -> LLMReply:
        raise NotImplementedError
