from __future__ import annotations

import base64
import os

from ..config import settings
from ..errors import ConfigurationError, ExternalServiceError
from .base import LLMAdapter, LLMCallContext, LLMImageInput, LLMReply


class OpenAIAdapter(LLMAdapter):
    """OpenAI-backed LLM adapter.

    Uses the OpenAI Responses API for hosted GPT generation.

    Attributes:
        supports_vision: Always True for OpenAI adapter.

    Methods:
        __init__: Initialize with API configuration.
        generate_reply: Generate text responses via OpenAI.
    """

    def __init__(self) -> None:
        """Initialize OpenAI adapter with API configuration.
        
        Raises:
            ConfigurationError: If OPENAI_API_KEY not configured or SDK not available.
        """
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
        """Check if adapter supports vision inputs (always True)."""
        return True

    def generate_reply(
        self,
        system_prompt: str,
        user_text: str,
        image_inputs: list[LLMImageInput] | None = None,
        *,
        call_context: LLMCallContext | None = None,
        model_override: str | None = None,
    ) -> LLMReply:
        """Generate text response via OpenAI.
        
        Args:
            system_prompt: System prompt.
            user_text: User message text.
            image_inputs: Optional list of image inputs.
            call_context: Ignored (for interface compatibility).
            model_override: Optional model name override.
            
        Returns:
            LLMReply with generated text.
            
        Raises:
            ExternalServiceError: If response fails or returns empty text.
        """
        _ = call_context
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
                model=model_override or settings.llm_model,
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
