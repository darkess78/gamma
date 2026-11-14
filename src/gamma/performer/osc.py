"""Minimal Open Sound Control (OSC 1.0) encoder.

Only what the VMC protocol needs: messages with string, int32, float32 and
boolean arguments, and bundles. Big-endian, every field padded to 4 bytes.
"""
from __future__ import annotations

import struct
from collections.abc import Iterable

OscArg = str | int | float | bool

# Time tag 1 means "immediately" in OSC 1.0.
OSC_IMMEDIATE = 1


def encode_osc_string(value: str) -> bytes:
    data = value.encode("utf-8") + b"\x00"
    return data + b"\x00" * (-len(data) % 4)


def encode_osc_message(address: str, *args: OscArg) -> bytes:
    if not address.startswith("/"):
        raise ValueError("OSC address must start with '/'")
    tags = [","]
    payload = bytearray()
    for arg in args:
        # bool before int: bool is an int subclass.
        if isinstance(arg, bool):
            tags.append("T" if arg else "F")
        elif isinstance(arg, int):
            tags.append("i")
            payload += struct.pack(">i", arg)
        elif isinstance(arg, float):
            tags.append("f")
            payload += struct.pack(">f", arg)
        elif isinstance(arg, str):
            tags.append("s")
            payload += encode_osc_string(arg)
        else:
            raise TypeError(f"unsupported OSC argument type: {type(arg).__name__}")
    return encode_osc_string(address) + encode_osc_string("".join(tags)) + bytes(payload)


def encode_osc_bundle(messages: Iterable[bytes], *, timetag: int = OSC_IMMEDIATE) -> bytes:
    out = bytearray(encode_osc_string("#bundle"))
    out += struct.pack(">Q", timetag)
    for message in messages:
        out += struct.pack(">i", len(message))
        out += message
    return bytes(out)
