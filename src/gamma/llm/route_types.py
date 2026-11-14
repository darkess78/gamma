"""Route decision and route error types shared by the router modules."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


RouteErrorClass = Literal[
    "context_budget",
    "capability_mismatch",
    "provider_unavailable",
    "context_overflow",
    "provider_error",
]


@dataclass(slots=True)
class RouteDecision:
    """Routing decision for model selection.
    
    Attributes:
        provider: LLM provider to use.
        model: Model name or None.
        reason: Why this route was chosen.
        route_family: Type of turn (chat, voice, metadata, etc.).
    """
    provider: str
    model: str | None
    reason: str
    route_family: str = "chat_default"
    endpoint_ref: str | None = None
    endpoint: str | None = None
