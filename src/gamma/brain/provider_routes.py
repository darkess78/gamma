"""Resolve only operator-configured local inference endpoints."""
from ..config import settings
from ..resources.runtime_registry import load_resource_routing_registry
from .route_protocol import validate_route


def configured_route(provider, endpoint):
    registry = load_resource_routing_registry()
    allowed = [settings.local_llm_endpoint]
    allowed.extend(item.url for item in registry.endpoints if item.provider in {"", "local", "ollama"})
    return validate_route(provider, endpoint, allowed)
