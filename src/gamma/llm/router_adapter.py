from __future__ import annotations

import time

from ..errors import ConfigurationError, ContextBudgetError, ContextOverflowError
from .base import LLMAdapter, LLMCallContext, LLMImageInput, LLMReply
from .capabilities import estimate_text_tokens, model_capability
from .provider_health import ProviderHealthMixin
from .route_events import RouteEventsMixin
from .route_policy import RoutePolicyMixin


class RouterLLMAdapter(RoutePolicyMixin, ProviderHealthMixin, RouteEventsMixin, LLMAdapter):
    """Routes each request between local Ollama models and hosted providers.

    generate_reply walks the route chain, checks each candidate's context budget
    and availability, calls the provider adapter and retries once with a
    compacted prompt on context overflow. Route choice lives in route_policy,
    health and backoff in provider_health, and logging in route_events.
    """

    _provider_backoff_until_global: dict[str, float] = {}

    def __init__(self) -> None:
        """Initialize router adapter with adapters and health cache."""
        self._adapters: dict[str, LLMAdapter] = {}
        self._health_cache: dict[tuple[str, str], tuple[float, dict[str, object]]] = {}

    @property
    def supports_vision(self) -> bool:
        """Check if any provider supports vision inputs."""
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
        """Route to available provider and generate reply.
        
        Args:
            system_prompt: System prompt.
            user_text: User text.
            image_inputs: Optional image inputs.
            call_context: Call context.
            model_override: Optional model override.
            
        Returns:
            LLMReply:
            
        Raises:
            ConfigurationError: If no usable provider available.
        """
        call_context = call_context or LLMCallContext()
        decisions = self._route_candidates(
            system_prompt=system_prompt,
            user_text=user_text,
            image_inputs=image_inputs,
            call_context=call_context,
            model_override=model_override,
        )
        last_exc: Exception | None = None
        seen: set[tuple[str, str | None]] = set()
        for index, decision in enumerate(decisions):
            key = (decision.provider, decision.model)
            if key in seen:
                continue
            seen.add(key)
            capability = model_capability(decision.provider, decision.model)
            candidate_system_prompt = system_prompt
            candidate_user_text = user_text
            prompt_metrics: dict[str, object] = {
                "estimated_input_tokens": estimate_text_tokens(system_prompt, user_text),
                "model_context_limit": capability.advertised_context_tokens,
                "usable_input_tokens": capability.usable_input_tokens,
                "reserved_output_tokens": capability.reserved_output_tokens,
                "safety_margin_tokens": capability.safety_margin_tokens,
                "context_provenance": capability.provenance,
                "compaction_level": 0,
                "compaction_actions": [],
            }
            if call_context.prompt_builder is not None:
                try:
                    candidate_system_prompt, candidate_user_text, built_metrics = call_context.prompt_builder(
                        capability.usable_input_tokens
                    )
                except ContextBudgetError as exc:
                    self._record_route_event(
                        decision=decision,
                        call_context=call_context,
                        user_text=user_text,
                        has_images=bool(image_inputs),
                        status="skipped",
                        duration_ms=0.0,
                        detail=str(exc),
                        error_class="context_budget",
                        fallback_index=index,
                        prompt_metrics=prompt_metrics,
                    )
                    continue
                prompt_metrics.update(built_metrics)
                prompt_metrics["estimated_input_tokens"] = estimate_text_tokens(
                    candidate_system_prompt,
                    candidate_user_text,
                )
            capability_error = None
            if call_context.persona_sensitive and not capability.persona_capable:
                capability_error = "model-is-not-persona-capable"
            elif capability.usable_input_tokens < max(0, call_context.minimum_context_tokens):
                capability_error = "model-context-below-required-minimum"
            elif int(prompt_metrics["estimated_input_tokens"]) > capability.usable_input_tokens:
                capability_error = "assembled-prompt-exceeds-model-budget"
            if capability_error:
                self._record_route_event(
                    decision=decision,
                    call_context=call_context,
                    user_text=candidate_user_text,
                    has_images=bool(image_inputs),
                    status="skipped",
                    duration_ms=0.0,
                    detail=capability_error,
                    error_class="capability_mismatch",
                    fallback_index=index,
                    prompt_metrics=prompt_metrics,
                )
                continue
            availability = self._provider_availability(
                provider=decision.provider,
                model=decision.model,
                has_images=bool(image_inputs),
                route_family=decision.route_family,
                endpoint=decision.endpoint,
            )
            if not availability.get("ok"):
                self._record_route_event(
                    decision=decision,
                    call_context=call_context,
                    user_text=candidate_user_text,
                    has_images=bool(image_inputs),
                    status="skipped",
                    duration_ms=0.0,
                    detail=str(availability.get("detail", "unavailable")),
                    error_class="provider_unavailable",
                    fallback_index=index,
                    prompt_metrics=prompt_metrics,
                )
                continue
            started_at = time.perf_counter()
            try:
                adapter = self._adapter_for_provider(decision.provider)
                reply = adapter.generate_reply(
                    system_prompt=candidate_system_prompt,
                    user_text=candidate_user_text,
                    image_inputs=image_inputs,
                    call_context=call_context,
                    model_override=decision.model,
                    endpoint_override=decision.endpoint,
                )
                duration_ms = round((time.perf_counter() - started_at) * 1000, 1)
                event = self._record_route_event(
                    decision=decision,
                    call_context=call_context,
                    user_text=candidate_user_text,
                    has_images=bool(image_inputs),
                    status="ok",
                    duration_ms=duration_ms,
                    detail=str(availability.get("detail", "ready")),
                    fallback_index=index,
                    prompt_metrics=prompt_metrics,
                )
                metadata = dict(reply.metadata or {})
                metadata["route"] = event
                reply.metadata = metadata
                self._clear_provider_backoff(
                    decision.provider,
                    has_images=bool(image_inputs),
                    route_family=decision.route_family,
                )
                return reply
            except ContextOverflowError as exc:
                last_exc = exc
                duration_ms = round((time.perf_counter() - started_at) * 1000, 1)
                original_estimate = int(prompt_metrics.get("estimated_input_tokens") or 0)
                overflow_metrics = {
                    **prompt_metrics,
                    "overflow_original_estimated_input_tokens": original_estimate,
                }
                self._record_route_event(
                    decision=decision,
                    call_context=call_context,
                    user_text=candidate_user_text,
                    has_images=bool(image_inputs),
                    status="context_overflow",
                    duration_ms=duration_ms,
                    detail=str(exc),
                    error_class="context_overflow",
                    fallback_index=index,
                    prompt_metrics=overflow_metrics,
                )
                if call_context.prompt_builder is not None:
                    retry_budget = max(
                        max(512, call_context.minimum_context_tokens),
                        int(capability.usable_input_tokens * 0.75),
                    )
                    if retry_budget < capability.usable_input_tokens:
                        try:
                            retry_system, retry_user, retry_built_metrics = call_context.prompt_builder(retry_budget)
                            retry_metrics = {
                                **prompt_metrics,
                                **retry_built_metrics,
                                "estimated_input_tokens": estimate_text_tokens(retry_system, retry_user),
                                "overflow_original_estimated_input_tokens": original_estimate,
                                "overflow_retry_budget": retry_budget,
                            }
                            retry_started = time.perf_counter()
                            retry_reply = adapter.generate_reply(
                                system_prompt=retry_system,
                                user_text=retry_user,
                                image_inputs=image_inputs,
                                call_context=call_context,
                                model_override=decision.model,
                                endpoint_override=decision.endpoint,
                            )
                            retry_duration = round((time.perf_counter() - retry_started) * 1000, 1)
                            event = self._record_route_event(
                                decision=decision,
                                call_context=call_context,
                                user_text=retry_user,
                                has_images=bool(image_inputs),
                                status="ok",
                                duration_ms=retry_duration,
                                detail="context-overflow-compacted-retry",
                                fallback_index=index,
                                prompt_metrics=retry_metrics,
                            )
                            metadata = dict(retry_reply.metadata or {})
                            metadata["route"] = event
                            retry_reply.metadata = metadata
                            return retry_reply
                        except (ContextOverflowError, ContextBudgetError) as retry_exc:
                            last_exc = retry_exc
                            self._record_route_event(
                                decision=decision,
                                call_context=call_context,
                                user_text=candidate_user_text,
                                has_images=bool(image_inputs),
                                status="context_overflow",
                                duration_ms=0.0,
                                detail=f"compacted retry failed: {retry_exc}",
                                error_class="context_overflow",
                                fallback_index=index,
                                prompt_metrics={**overflow_metrics, "overflow_retry_budget": retry_budget},
                            )
                        except Exception as retry_exc:
                            last_exc = retry_exc
                            self._mark_provider_failure(
                                decision.provider,
                                has_images=bool(image_inputs),
                                route_family=decision.route_family,
                            )
                            self._record_route_event(
                                decision=decision,
                                call_context=call_context,
                                user_text=candidate_user_text,
                                has_images=bool(image_inputs),
                                status="error",
                                duration_ms=0.0,
                                detail=f"compacted retry provider failure: {retry_exc}",
                                error_class="provider_error",
                                fallback_index=index,
                                prompt_metrics={**overflow_metrics, "overflow_retry_budget": retry_budget},
                            )
            except Exception as exc:
                last_exc = exc
                duration_ms = round((time.perf_counter() - started_at) * 1000, 1)
                self._mark_provider_failure(
                    decision.provider,
                    has_images=bool(image_inputs),
                    route_family=decision.route_family,
                )
                self._record_route_event(
                    decision=decision,
                    call_context=call_context,
                    user_text=candidate_user_text,
                    has_images=bool(image_inputs),
                    status="error",
                    duration_ms=duration_ms,
                    detail=str(exc),
                    error_class="provider_error",
                    fallback_index=index,
                    prompt_metrics=prompt_metrics,
                )
        if last_exc is not None:
            raise last_exc
        raise ConfigurationError("No usable routed LLM provider is available.")

    def _adapter_for_provider(self, provider: str) -> LLMAdapter:
        """Get or create adapter for provider.
        
        Args:
            provider: Provider name.
            
        Returns:
            LLMAdapter: Adapter instance.
        """
        normalized = provider.strip().lower()
        if normalized not in self._adapters:
            self._adapters[normalized] = self._build_provider_adapter(normalized)
        return self._adapters[normalized]

    def _build_provider_adapter(self, provider: str) -> LLMAdapter:
        """Build adapter for provider.
        
        Args:
            provider: Provider name.
            
        Returns:
            LLMAdapter: Built adapter instance.
        """
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

