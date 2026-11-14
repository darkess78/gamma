"""Per-request trace of route events, read back by the conversation pipeline."""
from __future__ import annotations

import threading


_ROUTE_TRACE = threading.local()


def begin_route_trace(*, persist: bool = True) -> None:
    """Initialize a route trace and choose whether its events reach production logs."""
    _ROUTE_TRACE.events = []
    _ROUTE_TRACE.persist = bool(persist)


def take_route_trace() -> list[dict[str, object]]:
    """Retrieve and clear route trace events.
    
    Returns:
        list[dict[str, object]]: List of route event records.
    """
    events = list(getattr(_ROUTE_TRACE, "events", []))
    _ROUTE_TRACE.events = []
    _ROUTE_TRACE.persist = True
    return events
