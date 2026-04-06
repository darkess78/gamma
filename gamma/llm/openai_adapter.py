from __future__ import annotations

import base64
import os

from ..config import settings
from ..errors import ConfigurationError, ExternalServiceError
from .base import LLMAdapter, LLMImageInput, LLMReply


class OpenAIAdapter(LLMAdapter):
    def __init__(self) -> None:
        api_key = settings.openai_api_key or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ConfigurationError("OPENAI_API_KEY is not configured.")
        try:
            from openai import OpenAI
        except Exception as exc:
            raise ConfigurationError("The OpenAI SDK is required for SHANA_LLM_PROVIDER=openai.") from exc
        self._client = OpenAI(api_key=api_key)

    @property
    def supports_vision(self) -> bool:
        return True

    def generate_reply(
        self,
        system_prompt: str,
        user_text: str,
        image_inputs: list[LLMImageInput] | None = None,
    ) -> LLMReply:
        user_content: list[dict[str, object]] = [{"type": "input_text", "text": user_text}]
        for image_input in image_inputs or []:
            encoded = base64.b64encode(image_input.data).decode("ascii")
            user_content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:{image_input.media_type};base64,{encoded}",
                }
            )
        try:
            response = self._client.responses.create(
                model=settings.llm_model,
                input=[
                    {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                    {"role": "user", "content": user_content},
                ],
            )
        except Exception as exc:
            raise ExternalServiceError(f"OpenAI response generation failed: {exc}") from exc
        text = getattr(response, "output_text", "").strip()
        if not text:
            raise ExternalServiceError("OpenAI returned an empty reply.")
        return LLMReply(text=text)
