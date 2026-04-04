from __future__ import annotations

import os

from openai import OpenAI

from ..config import settings
from ..errors import ConfigurationError, ExternalServiceError
from .base import LLMAdapter, LLMReply


class OpenAIAdapter(LLMAdapter):
    def __init__(self) -> None:
        api_key = settings.openai_api_key or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ConfigurationError("OPENAI_API_KEY is not configured.")
        self._client = OpenAI(api_key=api_key)

    def generate_reply(self, system_prompt: str, user_text: str) -> LLMReply:
        try:
            response = self._client.responses.create(
                model=settings.llm_model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text},
                ],
            )
        except Exception as exc:
            raise ExternalServiceError(f"OpenAI response generation failed: {exc}") from exc
        text = getattr(response, "output_text", "").strip()
        if not text:
            raise ExternalServiceError("OpenAI returned an empty reply.")
        return LLMReply(text=text)
