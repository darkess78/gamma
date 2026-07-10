"""Bounded protocol contract for the future Minecraft companion integration."""

from gamma.integrations.minecraft.protocol import (
    PROTOCOL_MESSAGE_ADAPTER,
    ProtocolMessage,
    parse_protocol_message,
)

__all__ = [
    "PROTOCOL_MESSAGE_ADAPTER",
    "ProtocolMessage",
    "parse_protocol_message",
]
