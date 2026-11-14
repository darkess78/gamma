"""The Shana API router: includes each route module."""
from __future__ import annotations

from fastapi import APIRouter

from . import pages, routes_conversation, routes_memory, routes_performer, routes_stream, routes_voice

router = APIRouter()
for _module in (pages, routes_conversation, routes_memory, routes_stream, routes_performer, routes_voice):
    router.include_router(_module.router)
