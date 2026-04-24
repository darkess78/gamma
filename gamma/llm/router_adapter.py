from __future__ import annotations

from dataclasses import dataclass

from ..config import settings
from ..errors import ConfigurationError
from .base import LLMAdapter, LLMCallContext, LLMImageInput, LLMReply


@dataclass(slots=True)
class RouteDecision:
    provider: str
    model: str | None
    reason: str


class RouterLLMAdapter(LLMAdapter):
    def __init__(self) -> None:
        self._adapters: dict[str, LLMAdapter] = {}

    @property
    def supports_vision(self) -> bool:
        decision = self._route_for_capabilities(has_images=True)
        try:
            return self._adapter_for_provider(decision.provider).supports_vision
        except Exception:
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
        decision = self._route_request(
            system_prompt=system_prompt,
            user_text=user_text,
            image_inputs=image_inputs,
            call_context=call_context,
            model_override=model_override,
        )
        adapter = self._adapter_for_provider(decision.provider)
        return adapter.generate_reply(
            system_prompt=system_prompt,
            user_text=user_text,
            image_inputs=image_inputs,
            call_context=call_context,
            model_override=decision.model,
        )

    def _route_request(
        self,
        *,
        system_prompt: str,
        user_text: str,
        image_inputs: list[LLMImageInput] | None,
        call_context: LLMCallContext | None,
        model_override: str | None,
    ) -> RouteDecision:
        if model_override:
            default_provider = self._default_provider()
            return RouteDecision(provider=default_provider, model=model_override, reason="explicit-model-override")

        if image_inputs:
            return self._route_for_vision()

        purpose = (call_context.purpose if call_context else "conversation").strip().lower()
        if purpose == "metadata_extraction":
            tagging_model = (settings.local_llm_tagging_model or "").strip()
            if tagging_model:
                return RouteDecision(provider="local", model=tagging_model, reason="metadata-tagging-model")
        if purpose in {"voice_reply_planner", "voice_sentence_generator"}:
            light_or_tagging = (settings.local_llm_tagging_model or "").strip() or (settings.local_llm_light_model or "").strip()
            if light_or_tagging:
                return RouteDecision(provider="local", model=light_or_tagging, reason="voice-helper-local-worker")
        if purpose == "tool_finalizer":
            light_model = (settings.local_llm_light_model or "").strip()
            if light_model:
                return RouteDecision(provider="local", model=light_model, reason="tool-finalizer-light-model")

        light_model = (settings.local_llm_light_model or "").strip()
        fast_requested = bool(call_context and (call_context.fast_mode or call_context.brief_mode or call_context.micro_mode))
        if fast_requested and light_model:
            return RouteDecision(provider="local", model=light_model, reason="fast-mode-light-model")

        if light_model and self._is_lightweight_text(user_text):
            return RouteDecision(provider="local", model=light_model, reason="lightweight-turn")

        if self._should_escalate_to_hosted(user_text=user_text, purpose=purpose):
            hosted = self._hosted_route()
            if hosted is not None:
                return hosted

        return RouteDecision(
            provider=self._default_provider(),
            model=self._default_model(),
            reason="default-route",
        )

    def _route_for_vision(self) -> RouteDecision:
        default_provider = self._default_provider()
        if self._provider_supports_vision(default_provider):
            return RouteDecision(provider=default_provider, model=self._default_model(), reason="default-vision-route")
        hosted = self._hosted_route()
        if hosted is not None and self._provider_supports_vision(hosted.provider):
            return RouteDecision(provider=hosted.provider, model=hosted.model, reason="hosted-vision-route")
        if settings.local_llm_supports_vision:
            return RouteDecision(
                provider="local",
                model=(settings.local_llm_vision_model or "").strip() or None,
                reason="local-vision-route",
            )
        return RouteDecision(provider=default_provider, model=self._default_model(), reason="fallback-vision-route")

    def _route_for_capabilities(self, *, has_images: bool) -> RouteDecision:
        if has_images:
            return self._route_for_vision()
        return RouteDecision(provider=self._default_provider(), model=self._default_model(), reason="default-capability-route")

    def _hosted_route(self) -> RouteDecision | None:
        if not settings.llm_router_allow_hosted_escalation:
            return None
        provider = (settings.llm_router_hosted_provider or "").strip().lower()
        if not provider:
            return None
        model = (settings.llm_router_hosted_model or "").strip() or None
        if provider == "openai" and not (model or settings.llm_model.strip()):
            return None
        return RouteDecision(provider=provider, model=model, reason="hosted-escalation")

    def _default_provider(self) -> str:
        provider = (settings.llm_router_default_provider or "").strip().lower()
        if provider:
            return provider
        return settings.llm_provider.strip().lower()

    def _default_model(self) -> str | None:
        provider = self._default_provider()
        configured = (settings.llm_router_default_model or "").strip()
        if configured:
            return configured
        if provider in {"local", "ollama"}:
            return (settings.local_llm_model or "").strip() or None
        if provider == "openai":
            return (settings.llm_model or "").strip() or None
        return None

    def _provider_supports_vision(self, provider: str) -> bool:
        if provider == "openai":
            return True
        if provider in {"local", "ollama"}:
            return settings.local_llm_supports_vision
        return False

    def _adapter_for_provider(self, provider: str) -> LLMAdapter:
        normalized = provider.strip().lower()
        if normalized not in self._adapters:
            self._adapters[normalized] = self._build_provider_adapter(normalized)
        return self._adapters[normalized]

    def _build_provider_adapter(self, provider: str) -> LLMAdapter:
        if provider == "openai":
            from .openai_adapter import OpenAIAdapter

            return OpenAIAdapter()
        if provider in {"local", "ollama"}:
            from .local_adapter import LocalLLMAdapter

            return LocalLLMAdapter()
        if provider == "mock":
            from .mock_adapter import MockLLMAdapter

            return MockLLMAdapter()
        raise ConfigurationError(f"Unsupported routed LLM provider: {provider}")

    def _is_lightweight_text(self, user_text: str) -> bool:
        lowered = (user_text or "").lower()
        if len(lowered.split()) > settings.local_llm_light_max_input_words:
            return False
        complex_markers = (
            "why ",
            "how ",
            "compare ",
            "plan ",
            "debug ",
            "error ",
            "remember ",
            "tool ",
            "provider ",
            "memory ",
            "code ",
        )
        return not any(marker in lowered for marker in complex_markers)

    def _should_escalate_to_hosted(self, *, user_text: str, purpose: str) -> bool:
        if not settings.llm_router_allow_hosted_escalation:
            return False
        if purpose not in {"conversation", "conversation_draft"}:
            return False
        lowered = (user_text or "").lower()
        if len(lowered.split()) > settings.llm_router_complex_max_input_words:
            return True
        escalation_markers = (
            "write code",
            "debug",
            "traceback",
            "stack trace",
            "refactor",
            "architecture",
            "design a system",
            "compare",
            "tradeoff",
            "step by step",
            "plan",
            "analyze",
        )
        return any(marker in lowered for marker in escalation_markers)
