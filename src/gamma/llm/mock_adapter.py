from __future__ import annotations

from ..errors import ConfigurationError
from .base import LLMAdapter, LLMCallContext, LLMImageInput, LLMReply


class MockLLMAdapter(LLMAdapter):
    """Mock LLM adapter for testing.
    
    Always returns canned responses; does not support vision inputs.
    
    Attributes:
        supports_vision: Always False for mock adapter.
    """

    def generate_reply(
        self,
        system_prompt: str,
        user_text: str,
        image_inputs: list[LLMImageInput] | None = None,
        *,
        call_context: LLMCallContext | None = None,
        model_override: str | None = None,
    ) -> LLMReply:
        """Generate a mock reply.
        
        Args:
            system_prompt: Ignored (for interface compatibility).
            user_text: User message text.
            image_inputs: Ignored; raises error if provided.
            call_context: Ignored (for interface compatibility).
            model_override: Ignored (for interface compatibility).
            
        Returns:
            LLMReply with canned text response.
            
        Raises:
            ConfigurationError: If image_inputs is provided.
        """
        _ = system_prompt
        _ = call_context
        _ = model_override
        if image_inputs:
            raise ConfigurationError("The mock LLM provider does not support image input.")
        return LLMReply(text=f"I heard you say: {user_text}")
