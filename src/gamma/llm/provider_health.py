"""Provider availability, local LLM health checks and failure backoff."""
from __future__ import annotations

import os
import time

from ..config import settings


class ProviderHealthMixin:
    """Provider availability, local LLM health checks and failure backoff."""

    def _provider_supports_vision(self, provider: str) -> bool:
        """Check if provider supports vision inputs.
        
        Args:
            provider: Provider name.
            
        Returns:
            bool: Whether vision is supported.
        """
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
        endpoint: str | None = None,
    ) -> dict[str, object]:
        """Check provider health and availability.
        
        Args:
            provider: Provider name.
            model: Model for hosted providers.
            has_images: Whether request has images.
            route_family: Route family for backoff key.
            endpoint: Optional local endpoint override.
            
        Returns:
            dict[str, object]: Health check result.
        """
        normalized = provider.strip().lower()
        now = time.time()
        backoff_key = self._backoff_key(provider=normalized, has_images=has_images, route_family=route_family)
        backoff_until = self._provider_backoff_until_global.get(backoff_key, 0.0)
        if backoff_until > now:
            return {"ok": False, "detail": f"backoff-active:{backoff_key}:{round(backoff_until - now, 1)}s"}
        cache_key = (normalized, "vision" if has_images else "text", endpoint or "")
        cached = self._health_cache.get(cache_key)
        if cached and (now - cached[0]) < 5:
            return cached[1]
        if normalized == "openai":
            api_key = settings.openai_api_key or os.getenv("OPENAI_API_KEY")
            result = {"ok": bool(api_key and (model or settings.llm_model).strip()), "detail": "ready" if api_key else "missing-openai-api-key"}
        elif normalized in {"local", "ollama"}:
            local_health = self._check_local_llm_health(endpoint=endpoint)
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

    def _check_local_llm_health(self, *, endpoint: str | None = None) -> dict[str, object]:
        """Check local Ollama service health.
        
        Returns:
            dict[str, object]: Health check with ok/detail flags.
        """
        from ..system.status import probe_ollama_health

        health = probe_ollama_health(endpoint or settings.local_llm_endpoint, timeout_seconds=5)
        if health.get("ok"):
            return {"ok": True, "detail": "ready"}
        return {"ok": False, "detail": str(health.get("detail", "local-llm-unhealthy"))}

    def _mark_provider_failure(self, provider: str, *, has_images: bool, route_family: str | None = None) -> None:
        """Add failure backoff for provider.
        
        Args:
            provider: Provider name.
            has_images: Whether request had image inputs.
            route_family: Route family for backoff key.
        """
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
        """Clear failure backoff for provider.
        
        Args:
            provider: Provider name.
            has_images: Whether request had image inputs.
            route_family: Route family for backoff key.
        """
        key = self._backoff_key(
            provider=provider.strip().lower(),
            has_images=has_images,
            route_family=route_family,
        )
        self._provider_backoff_until_global.pop(key, None)

    @classmethod
    def provider_backoff_state(cls) -> dict[str, float]:
        """Get current provider backoff state.
        
        Returns:
            dict[str, float]: Provider names and remaining backoff seconds.
        """
        now = time.time()
        return {
            provider: round(until - now, 1)
            for provider, until in cls._provider_backoff_until_global.items()
            if until > now
        }

    def _backoff_key(self, *, provider: str, has_images: bool, route_family: str | None) -> str:
        """Generate unique backoff key for provider.
        
        Args:
            provider: Provider name.
            has_images: Whether using vision endpoint.
            route_family: Route family.
            
        Returns:
            str: Backoff key string.
        """
        scope = "vision" if has_images else (route_family or "text")
        return f"{provider}:{scope}"
