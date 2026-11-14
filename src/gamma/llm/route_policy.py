"""Choosing a route: classify the turn, pick provider and model, and build the fallback chain."""
from __future__ import annotations

from ..config import settings
from .base import LLMCallContext, LLMImageInput
from .route_types import RouteDecision


class RoutePolicyMixin:
    """Choosing a route: classify the turn, pick provider and model, and build the fallback chain."""

    def _route_candidates(
        self,
        *,
        system_prompt: str,
        user_text: str,
        image_inputs: list[LLMImageInput] | None,
        call_context: LLMCallContext | None,
        model_override: str | None,
    ) -> list[RouteDecision]:
        """Build list of route candidates.
        
        Args:
            system_prompt: System prompt.
            user_text: User text.
            image_inputs: Optional image inputs.
            call_context: Call context.
            model_override: Optional model override.
            
        Returns:
            list[RouteDecision]: Primary candidate plus fallbacks.
        """
        route_family = self._classify_route_family(
            user_text=user_text,
            image_inputs=image_inputs,
            call_context=call_context,
            model_override=model_override,
        )
        primary = self._route_request(
            system_prompt=system_prompt,
            user_text=user_text,
            image_inputs=image_inputs,
            call_context=call_context,
            model_override=model_override,
            route_family=route_family,
        )
        return self._build_route_chain(primary=primary, route_family=route_family, has_images=bool(image_inputs))

    def _route_request(
        self,
        *,
        system_prompt: str,
        user_text: str,
        image_inputs: list[LLMImageInput] | None,
        call_context: LLMCallContext | None,
        model_override: str | None,
        route_family: str | None = None,
    ) -> RouteDecision:
        """Make primary route decision for request parameters.
        
        Args:
            system_prompt: System prompt.
            user_text: User text.
            image_inputs: Optional image inputs.
            call_context: Call context.
            model_override: Optional model override.
            route_family: Optional route family classification.
            
        Returns:
            RouteDecision: Primary route decision.
        """
        route_family = route_family or self._classify_route_family(
            user_text=user_text,
            image_inputs=image_inputs,
            call_context=call_context,
            model_override=model_override,
        )
        if model_override:
            default_provider = self._default_provider()
            return RouteDecision(
                provider=default_provider,
                model=model_override,
                reason="explicit-model-override",
                route_family=route_family,
            )

        if image_inputs:
            return self._route_for_vision()

        purpose = (call_context.purpose if call_context else "conversation").strip().lower()
        profile = self._profile()
        if route_family == "metadata":
            tagging_model = (settings.local_llm_tagging_model or "").strip()
            if tagging_model:
                return RouteDecision(provider="local", model=tagging_model, reason="metadata-tagging-model", route_family=route_family)
        if route_family == "voice_fast":
            light_or_tagging = (settings.local_llm_tagging_model or "").strip() or (settings.local_llm_light_model or "").strip()
            if light_or_tagging:
                return RouteDecision(provider="local", model=light_or_tagging, reason="voice-helper-local-worker", route_family=route_family)
        if route_family == "tool_finalize":
            light_model = (settings.local_llm_light_model or "").strip()
            if light_model:
                return RouteDecision(provider="local", model=light_model, reason="tool-finalizer-light-model", route_family=route_family)
        if route_family == "presence_wake":
            return RouteDecision(
                provider=self._default_provider(),
                model=self._default_model(),
                reason="presence-wake-primary-quality",
                route_family=route_family,
            )

        light_model = (settings.local_llm_light_model or "").strip()
        fast_requested = bool(call_context and (call_context.fast_mode or call_context.brief_mode or call_context.micro_mode))
        if profile == "high_quality" and route_family in {"chat_light", "chat_default", "reasoning_heavy"}:
            hosted = self._hosted_route(force=True)
            if hosted is not None:
                return RouteDecision(provider=hosted.provider, model=hosted.model, reason="high-quality-hosted-default", route_family=route_family)

        if fast_requested and light_model and route_family in {"chat_light", "voice_fast"} and profile in {"balanced", "low_latency_voice", "local_only", "offline_safe"}:
            return RouteDecision(provider="local", model=light_model, reason="fast-mode-light-model", route_family=route_family)

        if light_model and route_family == "chat_light" and profile in {"balanced", "low_latency_voice", "local_only", "offline_safe"}:
            return RouteDecision(provider="local", model=light_model, reason="lightweight-turn", route_family=route_family)

        if (
            profile == "low_latency_voice"
            and route_family in {"chat_light", "chat_persona"}
            and light_model
            and len((user_text or "").split()) <= min(settings.llm_router_chat_light_max_input_words, 24)
        ):
            return RouteDecision(provider="local", model=light_model, reason="low-latency-short-turn", route_family=route_family)

        if route_family not in {"chat_persona", "reasoning_heavy_persona"} and self._should_escalate_to_hosted(user_text=user_text, purpose=purpose):
            hosted = self._hosted_route()
            if hosted is not None:
                hosted.route_family = route_family
                return hosted

        return RouteDecision(
            provider=self._default_provider(),
            model=self._default_model(),
            reason="default-route",
            route_family=route_family,
        )

    def _classify_route_family(
        self,
        *,
        user_text: str,
        image_inputs: list[LLMImageInput] | None,
        call_context: LLMCallContext | None,
        model_override: str | None,
    ) -> str:
        """Classify turn into route family.
        
        Args:
            user_text: User text.
            image_inputs: Optional image inputs.
            call_context: Call context.
            model_override: Optional model override.
            
        Returns:
            str: Route family name.
        """
        if model_override:
            return "explicit_override"
        if image_inputs:
            return "vision"
        purpose = (call_context.purpose if call_context else "conversation").strip().lower()
        if purpose == "metadata_extraction":
            return "metadata"
        if purpose in {"voice_reply_planner", "voice_sentence_generator"}:
            return "voice_fast"
        if purpose == "tool_finalizer":
            return "tool_finalize"
        if purpose == "presence_wake":
            return "presence_wake"
        reasoning_depth = (call_context.reasoning_depth if call_context else "normal").strip().lower()
        persona_sensitive = bool(call_context and call_context.persona_sensitive)
        fast_requested = bool(call_context and (call_context.fast_mode or call_context.brief_mode or call_context.micro_mode))
        if reasoning_depth == "heavy" or self._should_escalate_to_hosted(user_text=user_text, purpose=purpose):
            return "reasoning_heavy_persona" if persona_sensitive else "reasoning_heavy"
        if persona_sensitive and not fast_requested:
            return "chat_persona"
        if fast_requested or self._is_lightweight_text(user_text):
            return "chat_light"
        return "chat_default"

    def _build_route_chain(self, *, primary: RouteDecision, route_family: str, has_images: bool) -> list[RouteDecision]:
        """Build chain of route candidates with fallbacks.
        
        Args:
            primary: Primary route decision.
            route_family: Route family type.
            has_images: Whether request has images.
            
        Returns:
            list[RouteDecision]: Chain of candidates for fallback.
        """
        candidates = [primary]
        default_route = RouteDecision(
            provider=self._default_provider(),
            model=self._default_model(),
            reason="default-route-fallback",
            route_family=route_family,
        )
        local_primary = (settings.local_llm_model or "").strip() or None
        light_model = (settings.local_llm_light_model or "").strip() or None
        tagging_model = (settings.local_llm_tagging_model or "").strip() or None

        if route_family == "voice_fast":
            if light_model and (primary.provider, primary.model) != ("local", light_model):
                candidates.append(RouteDecision(provider="local", model=light_model, reason="voice-light-fallback", route_family=route_family))
            if local_primary and (primary.provider, primary.model) != ("local", local_primary):
                candidates.append(RouteDecision(provider="local", model=local_primary, reason="voice-primary-fallback", route_family=route_family))
        elif route_family == "metadata":
            if tagging_model and (primary.provider, primary.model) != ("local", tagging_model):
                candidates.append(RouteDecision(provider="local", model=tagging_model, reason="metadata-tagging-fallback", route_family=route_family))
            if local_primary and (primary.provider, primary.model) != ("local", local_primary):
                candidates.append(RouteDecision(provider="local", model=local_primary, reason="metadata-primary-fallback", route_family=route_family))
        elif route_family == "tool_finalize":
            if light_model and (primary.provider, primary.model) != ("local", light_model):
                candidates.append(RouteDecision(provider="local", model=light_model, reason="tool-light-fallback", route_family=route_family))
            if local_primary and (primary.provider, primary.model) != ("local", local_primary):
                candidates.append(RouteDecision(provider="local", model=local_primary, reason="tool-primary-fallback", route_family=route_family))
        elif route_family == "presence_wake":
            if local_primary and (primary.provider, primary.model) != ("local", local_primary):
                candidates.append(RouteDecision(provider="local", model=local_primary, reason="presence-wake-primary-fallback", route_family=route_family))
            if light_model and (primary.provider, primary.model) != ("local", light_model):
                candidates.append(RouteDecision(provider="local", model=light_model, reason="presence-wake-persona-fallback", route_family=route_family))
            if self._persona_heavy_hosted_fallback_enabled():
                hosted_primary = self._hosted_route()
                if hosted_primary is not None and (hosted_primary.provider, hosted_primary.model) != (primary.provider, primary.model):
                    hosted_primary.reason = "presence-wake-hosted-fallback"
                    hosted_primary.route_family = route_family
                    candidates.append(hosted_primary)
        elif route_family in {"chat_light", "chat_persona", "chat_default", "reasoning_heavy", "reasoning_heavy_persona"}:
            if route_family in {"chat_light", "chat_persona"} and light_model and (primary.provider, primary.model) != ("local", light_model):
                candidates.append(RouteDecision(provider="local", model=light_model, reason="local-light-fallback", route_family=route_family))
            if local_primary and (primary.provider, primary.model) != ("local", local_primary):
                candidates.append(RouteDecision(provider="local", model=local_primary, reason="local-primary-fallback", route_family=route_family))
            if route_family == "reasoning_heavy_persona" and self._persona_heavy_hosted_fallback_enabled():
                hosted_primary = self._hosted_route()
                if hosted_primary is not None and (hosted_primary.provider, hosted_primary.model) != (primary.provider, primary.model):
                    hosted_primary.reason = "persona-heavy-hosted-fallback"
                    hosted_primary.route_family = route_family
                    candidates.append(hosted_primary)

        if (default_route.provider, default_route.model) != (primary.provider, primary.model):
            candidates.append(default_route)
        hosted = self._hosted_route()
        if (
            hosted is not None
            and route_family not in {"chat_persona", "reasoning_heavy_persona", "presence_wake", "voice_fast", "metadata"}
            and (hosted.provider, hosted.model) != (primary.provider, primary.model)
        ):
            hosted.reason = "hosted-escalation-fallback"
            hosted.route_family = route_family
            candidates.append(hosted)
        if not has_images and self._default_provider() != "mock" and route_family != "presence_wake":
            candidates.append(RouteDecision(provider="mock", model=None, reason="mock-last-resort", route_family=route_family))
        return candidates

    def _route_for_vision(self) -> RouteDecision:
        """Route for vision input request.
        
        Returns:
            RouteDecision: Provider supporting vision.
        """
        default_provider = self._default_provider()
        if self._provider_supports_vision(default_provider):
            return RouteDecision(provider=default_provider, model=self._default_model(), reason="default-vision-route", route_family="vision")
        hosted = self._hosted_route()
        if hosted is not None and self._provider_supports_vision(hosted.provider):
            return RouteDecision(provider=hosted.provider, model=hosted.model, reason="hosted-vision-route", route_family="vision")
        if settings.local_llm_supports_vision:
            return RouteDecision(
                provider="local",
                model=(settings.local_llm_vision_model or "").strip() or None,
                reason="local-vision-route",
                route_family="vision",
            )
        return RouteDecision(provider=default_provider, model=self._default_model(), reason="fallback-vision-route", route_family="vision")

    def _route_for_capabilities(self, *, has_images: bool) -> RouteDecision:
        """Route based on capability requirements.
        
        Args:
            has_images: Whether request has image inputs.
            
        Returns:
            RouteDecision: Route decision.
        """
        if has_images:
            return self._route_for_vision()
        return RouteDecision(provider=self._default_provider(), model=self._default_model(), reason="default-capability-route")

    def _hosted_route(self, *, force: bool = False) -> RouteDecision | None:
        """Get hosted provider route.
        
        Args:
            force: Force hosted even if config disabled.
            
        Returns:
            RouteDecision | None: Hosted route or None.
        """
        if not force and not settings.llm_router_allow_hosted_escalation:
            return None
        if self._profile() in {"local_only", "offline_safe"}:
            return None
        provider = (settings.llm_router_hosted_provider or "").strip().lower()
        if not provider:
            return None
        model = (settings.llm_router_hosted_model or "").strip() or None
        if provider == "openai" and not (model or settings.llm_model.strip()):
            return None
        return RouteDecision(provider=provider, model=model, reason="hosted-escalation")

    def _default_provider(self) -> str:
        """Get default provider based on profile.
        
        Returns:
            str: Provider name.
        """
        profile = self._profile()
        if profile in {"local_only", "offline_safe"}:
            return "local"
        provider = (settings.llm_router_default_provider or "").strip().lower()
        if provider:
            return provider
        return settings.llm_provider.strip().lower()

    def _default_model(self) -> str | None:
        """Get default model for current provider.
        
        Returns:
            str | None: Model name or None.
        """
        provider = self._default_provider()
        configured = (settings.llm_router_default_model or "").strip()
        if configured:
            return configured
        if provider in {"local", "ollama"}:
            return (settings.local_llm_model or "").strip() or None
        if provider == "openai":
            return (settings.llm_model or "").strip() or None
        return None

    def _is_lightweight_text(self, user_text: str) -> bool:
        """Check if text is light/simple for routing.
        
        Args:
            user_text: User text.
            
        Returns:
            bool: True if text is lightweight.
        """
        lowered = (user_text or "").lower()
        if len(lowered.split()) > settings.llm_router_chat_light_max_input_words:
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

    def _persona_heavy_hosted_fallback_enabled(self) -> bool:
        """Check if persona-heavy routing allows hosted fallback.
        
        Returns:
            bool: True if fallback enabled.
        """
        return bool(settings.llm_router_allow_hosted_escalation and settings.llm_router_persona_heavy_hosted_fallback_enabled)

    def _should_escalate_to_hosted(self, *, user_text: str, purpose: str) -> bool:
        """Check if should escalate to hosted provider.
        
        Args:
            user_text: User text.
            purpose: Call purpose.
            
        Returns:
            bool: True if escalation warranted.
        """
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
