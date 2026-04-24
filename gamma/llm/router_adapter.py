from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from ..config import settings
from ..errors import ConfigurationError
from .base import LLMAdapter, LLMCallContext, LLMImageInput, LLMReply

_ROUTE_TRACE = threading.local()


@dataclass(slots=True)
class RouteDecision:
    provider: str
    model: str | None
    reason: str
    route_family: str = "chat_default"


def begin_route_trace() -> None:
    _ROUTE_TRACE.events = []


def take_route_trace() -> list[dict[str, object]]:
    events = list(getattr(_ROUTE_TRACE, "events", []))
    _ROUTE_TRACE.events = []
    return events


class RouterLLMAdapter(LLMAdapter):
    _provider_backoff_until_global: dict[str, float] = {}

    def __init__(self) -> None:
        self._adapters: dict[str, LLMAdapter] = {}
        self._health_cache: dict[tuple[str, str], tuple[float, dict[str, object]]] = {}

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
            availability = self._provider_availability(
                provider=decision.provider,
                model=decision.model,
                has_images=bool(image_inputs),
                route_family=decision.route_family,
            )
            if not availability.get("ok"):
                self._record_route_event(
                    decision=decision,
                    call_context=call_context,
                    user_text=user_text,
                    has_images=bool(image_inputs),
                    status="skipped",
                    duration_ms=0.0,
                    detail=str(availability.get("detail", "unavailable")),
                    fallback_index=index,
                )
                continue
            started_at = time.perf_counter()
            try:
                adapter = self._adapter_for_provider(decision.provider)
                reply = adapter.generate_reply(
                    system_prompt=system_prompt,
                    user_text=user_text,
                    image_inputs=image_inputs,
                    call_context=call_context,
                    model_override=decision.model,
                )
                duration_ms = round((time.perf_counter() - started_at) * 1000, 1)
                event = self._record_route_event(
                    decision=decision,
                    call_context=call_context,
                    user_text=user_text,
                    has_images=bool(image_inputs),
                    status="ok",
                    duration_ms=duration_ms,
                    detail=str(availability.get("detail", "ready")),
                    fallback_index=index,
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
                    user_text=user_text,
                    has_images=bool(image_inputs),
                    status="error",
                    duration_ms=duration_ms,
                    detail=str(exc),
                    fallback_index=index,
                )
        if last_exc is not None:
            raise last_exc
        raise ConfigurationError("No usable routed LLM provider is available.")

    def _route_candidates(
        self,
        *,
        system_prompt: str,
        user_text: str,
        image_inputs: list[LLMImageInput] | None,
        call_context: LLMCallContext | None,
        model_override: str | None,
    ) -> list[RouteDecision]:
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
            and route_family not in {"chat_persona", "reasoning_heavy_persona", "voice_fast", "metadata"}
            and (hosted.provider, hosted.model) != (primary.provider, primary.model)
        ):
            hosted.reason = "hosted-escalation-fallback"
            hosted.route_family = route_family
            candidates.append(hosted)
        if not has_images and self._default_provider() != "mock":
            candidates.append(RouteDecision(provider="mock", model=None, reason="mock-last-resort", route_family=route_family))
        return candidates

    def _route_for_vision(self) -> RouteDecision:
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
        if has_images:
            return self._route_for_vision()
        return RouteDecision(provider=self._default_provider(), model=self._default_model(), reason="default-capability-route")

    def _hosted_route(self, *, force: bool = False) -> RouteDecision | None:
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
        profile = self._profile()
        if profile in {"local_only", "offline_safe"}:
            return "local"
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

    def _provider_availability(
        self,
        *,
        provider: str,
        model: str | None,
        has_images: bool,
        route_family: str | None = None,
    ) -> dict[str, object]:
        normalized = provider.strip().lower()
        now = time.time()
        backoff_key = self._backoff_key(provider=normalized, has_images=has_images, route_family=route_family)
        backoff_until = self._provider_backoff_until_global.get(backoff_key, 0.0)
        if backoff_until > now:
            return {"ok": False, "detail": f"backoff-active:{backoff_key}:{round(backoff_until - now, 1)}s"}
        cache_key = (normalized, "vision" if has_images else "text")
        cached = self._health_cache.get(cache_key)
        if cached and (now - cached[0]) < 5:
            return cached[1]
        if normalized == "openai":
            api_key = settings.openai_api_key or os.getenv("OPENAI_API_KEY")
            result = {"ok": bool(api_key and (model or settings.llm_model).strip()), "detail": "ready" if api_key else "missing-openai-api-key"}
        elif normalized in {"local", "ollama"}:
            local_health = self._check_local_llm_health()
            if not local_health.get("ok"):
                result = local_health
            elif has_images and not self._provider_supports_vision(normalized):
                result = {"ok": False, "detail": "vision-not-supported"}
            else:
                result = {"ok": True, "detail": str(local_health.get("detail", "ready"))}
        elif normalized == "mock":
            result = {"ok": not has_images, "detail": "ready" if not has_images else "mock-no-vision"}
        else:
            result = {"ok": False, "detail": f"unsupported-provider: {provider}"}
        self._health_cache[cache_key] = (now, result)
        return result

    def _check_local_llm_health(self) -> dict[str, object]:
        from ..system.status import SystemStatusService

        health = SystemStatusService()._check_ollama_health()
        if health.get("ok"):
            return {"ok": True, "detail": "ready"}
        return {"ok": False, "detail": str(health.get("detail", "local-llm-unhealthy"))}

    def _record_route_event(
        self,
        *,
        decision: RouteDecision,
        call_context: LLMCallContext,
        user_text: str,
        has_images: bool,
        status: str,
        duration_ms: float,
        detail: str,
        fallback_index: int,
    ) -> dict[str, object]:
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "purpose": call_context.purpose,
            "route_family": decision.route_family,
            "provider": decision.provider,
            "model": decision.model,
            "reason": decision.reason,
            "profile": self._profile(),
            "status": status,
            "detail": detail[:240],
            "duration_ms": duration_ms,
            "input_words": len((user_text or "").split()),
            "has_images": has_images,
            "fast_mode": bool(call_context.fast_mode),
            "brief_mode": bool(call_context.brief_mode),
            "micro_mode": bool(call_context.micro_mode),
            "reasoning_depth": call_context.reasoning_depth,
            "persona_sensitive": bool(call_context.persona_sensitive),
            "interaction_mode": call_context.interaction_mode,
            "cost_sensitive": bool(call_context.cost_sensitive),
            "fallback_index": fallback_index,
        }
        current = list(getattr(_ROUTE_TRACE, "events", []))
        current.append(event)
        _ROUTE_TRACE.events = current
        log_dir = settings.data_dir / "runtime"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "llm.routes.jsonl"
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def _mark_provider_failure(self, provider: str, *, has_images: bool, route_family: str | None = None) -> None:
        backoff_seconds = max(0, int(settings.llm_router_failure_backoff_seconds))
        if backoff_seconds <= 0:
            return
        key = self._backoff_key(
            provider=provider.strip().lower(),
            has_images=has_images,
            route_family=route_family,
        )
        self._provider_backoff_until_global[key] = time.time() + backoff_seconds

    def _clear_provider_backoff(self, provider: str, *, has_images: bool, route_family: str | None = None) -> None:
        key = self._backoff_key(
            provider=provider.strip().lower(),
            has_images=has_images,
            route_family=route_family,
        )
        self._provider_backoff_until_global.pop(key, None)

    def _profile(self) -> str:
        profile = (settings.llm_router_profile or "").strip().lower()
        if profile in {"balanced", "local_only", "low_latency_voice", "high_quality", "offline_safe"}:
            return profile
        return "balanced"

    @classmethod
    def provider_backoff_state(cls) -> dict[str, float]:
        now = time.time()
        return {
            provider: round(until - now, 1)
            for provider, until in cls._provider_backoff_until_global.items()
            if until > now
        }

    def _backoff_key(self, *, provider: str, has_images: bool, route_family: str | None) -> str:
        scope = "vision" if has_images else (route_family or "text")
        return f"{provider}:{scope}"

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
        return bool(settings.llm_router_allow_hosted_escalation and settings.llm_router_persona_heavy_hosted_fallback_enabled)

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
