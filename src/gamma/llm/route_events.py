"""Recording each route attempt to the request trace and the llm.routes.jsonl log."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from ..config import settings
from .base import LLMCallContext
from .route_trace import _ROUTE_TRACE
from .route_types import RouteDecision, RouteErrorClass


class RouteEventsMixin:
    """Recording each route attempt to the request trace and the llm.routes.jsonl log."""

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
        error_class: RouteErrorClass | None = None,
        prompt_metrics: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Record route event for logging and metrics.
        
        Args:
            decision: Route decision.
            call_context: Call context.
            user_text: User text.
            has_images: Whether request has images.
            status: Route status.
            duration_ms: Execution duration.
            detail: Event detail (truncated).
            fallback_index: Fallback index or 0.
            error_class: Stable non-content failure taxonomy, or None for success.
            
        Returns:
            dict[str, object]: Logged event data.
        """
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "purpose": call_context.purpose,
            "route_family": decision.route_family,
            "provider": decision.provider,
            "model": decision.model,
            "endpoint_ref": decision.endpoint_ref,
            "endpoint": decision.endpoint,
            "reason": decision.reason,
            "profile": self._profile(),
            "status": status,
            "error_class": error_class,
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
            "quality_tier": call_context.quality_tier,
            "minimum_context_tokens": call_context.minimum_context_tokens,
            "fallback_index": fallback_index,
        }
        if prompt_metrics:
            event.update(prompt_metrics)
        current = list(getattr(_ROUTE_TRACE, "events", []))
        current.append(event)
        _ROUTE_TRACE.events = current
        persist_event = bool(getattr(_ROUTE_TRACE, "persist", True)) and call_context.interaction_mode not in {
            "evaluation",
            "improvement",
        }
        if persist_event:
            log_dir = settings.data_dir / "runtime"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / "llm.routes.jsonl"
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def _profile(self) -> str:
        """Get routing profile name.
        
        Returns:
            str: Profile name or 'balanced' default.
        """
        profile = (settings.llm_router_profile or "").strip().lower()
        if profile in {"balanced", "local_only", "low_latency_voice", "high_quality", "offline_safe"}:
            return profile
        return "balanced"
