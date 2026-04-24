from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class LLMImageInput:
    data: bytes
    media_type: str
    filename: str | None = None


@dataclass(slots=True)
class LLMReply:
    text: str
    metadata: dict[str, object] | None = None


@dataclass(slots=True)
class LLMCallContext:
    purpose: str = "conversation"
    fast_mode: bool = False
    brief_mode: bool = False
    micro_mode: bool = False


class LLMAdapter:
    @property
    def supports_vision(self) -> bool:
        return False

    def generate_reply(
        self,
        system_prompt: str,
        user_text: str,
        image_inputs: list[LLMImageInput] | None = None,
        *,
        call_context: LLMCallContext | None = None,
        model_override: str | None = None,
    ) -> LLMReply:
        raise NotImplementedError
