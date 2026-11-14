"""The Shana API router: includes each route module and owns the Minecraft coordinator lifecycle."""
from __future__ import annotations

from fastapi import APIRouter

from ..config import settings
from ..integrations.minecraft.coordinator import MinecraftCoordinator
from ..integrations.minecraft.websocket import create_minecraft_control_router
from ..system.lazy_singleton import LazySingleton
from . import pages, routes_conversation, routes_memory, routes_performer, routes_stream, routes_voice


router = APIRouter()
minecraft_coordinator = LazySingleton[MinecraftCoordinator]()
_minecraft_lifespan_active = False


def get_minecraft_coordinator() -> MinecraftCoordinator:
    """Return the authoritative Shana-owned Minecraft coordinator."""

    return minecraft_coordinator.get(
        lambda: MinecraftCoordinator(enabled=settings.minecraft.configured)
    )


def start_minecraft_coordinator() -> MinecraftCoordinator:
    """Claim the process-local coordinator for one Shana application lifespan."""

    global _minecraft_lifespan_active
    if _minecraft_lifespan_active:
        raise RuntimeError("a Shana application lifespan is already active")
    coordinator = get_minecraft_coordinator()
    _minecraft_lifespan_active = True
    return coordinator


def get_active_minecraft_coordinator() -> MinecraftCoordinator | None:
    """Return the coordinator only while its owning lifespan remains active."""

    if not _minecraft_lifespan_active:
        return None
    return minecraft_coordinator._value


def reset_minecraft_coordinator(coordinator: MinecraftCoordinator) -> None:
    """Detach all authority and discard the process-local coordinator."""

    global _minecraft_lifespan_active
    if minecraft_coordinator._value is not coordinator:
        return
    _minecraft_lifespan_active = False
    coordinator.detach_transport()
    coordinator.reset()
    minecraft_coordinator.set(None)


router.include_router(
    create_minecraft_control_router(
        coordinator_provider=get_active_minecraft_coordinator,
        control_token_provider=lambda: settings.minecraft.control_token.get_secret_value(),
        maximum_inbound_bytes_provider=lambda: settings.minecraft.maximum_inbound_bytes,
    )
)

for _module in (pages, routes_conversation, routes_memory, routes_stream, routes_performer, routes_voice):
    router.include_router(_module.router)
