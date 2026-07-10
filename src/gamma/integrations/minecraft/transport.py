"""Transport-independent delivery boundary for Minecraft protocol messages."""

from __future__ import annotations

from typing import Protocol

from gamma.integrations.minecraft.protocol import ProtocolMessage


class MinecraftTransport(Protocol):
    """Deliver an already validated canonical protocol message."""

    async def send(self, message: ProtocolMessage) -> None:
        """Send one canonical message or raise when delivery is lost."""
