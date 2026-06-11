from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class LLMCallContext:
    """Call context for LLM routing.
    
    Attributes:
        session_id: Active session identifier.
        call_id: LLM call/trail identifier.
        provider: LLM provider being used.
        model: Model name for this call.
        purpose: Call purpose (conversation, etc.).
        fast_mode: Use fast/simple model.
        brief_mode: Return brief responses.
        micro_mode: Micro-prompt mode.
        reasoning_depth: Reasoning detail level.
        persona_sensitive: Whether persona applies.
        interaction_mode: Chat or other mode.
        cost_sensitive: Consider cost implications.
    """
    session_id: str | None = None
    call_id: str | None = None
    provider: str | None = None
    model: str | None = None
    purpose: str = "conversation"
    fast_mode: bool = False
    brief_mode: bool = False
    micro_mode: bool = False
    reasoning_depth: str = "normal"
    persona_sensitive: bool = False
    interaction_mode: str = "chat"
    cost_sensitive: bool = False


@dataclass(slots=True)
class LLMImageInput:
    """Image data to send with a text prompt.
    
    Attributes:
        data: Base64 or binary image bytes.
        media_type: MIME media type (image/png, etc.).
        filename: Optional original filename.
    """
    data: bytes
    media_type: str
    filename: str | None = None


@dataclass(slots=True)
class LLMReply:
    """LLM generation response.
    
    Attributes:
        text: Generated text response.
        metadata: Optional call metadata.
    """
    text: str
    metadata: dict[str, object] | None = None





class LLMAdapter:
    """Base abstract class for LLM provider adapters.
    
    Attributes:
        supports_vision: Whether adapter supports vision inputs.
    
    Methods:
        generate_reply: Generate a text response to prompts.
    """

    @property
    def supports_vision(self) -> bool:
        """Check if adapter supports vision inputs."""
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
        """Generate a text reply to a prompt.
        
        Args:
            system_prompt: System prompt for the model.
            user_text: User message text.
            image_inputs: Optional list of image inputs.
            call_context: Call context or None.
            model_override: Optional model name override.
            
        Returns:
            LLMReply with generated text.
        """
        raise NotImplementedError
