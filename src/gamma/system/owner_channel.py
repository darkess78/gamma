"""Proof that an API request came from the owner's authenticated dashboard.

The Shana API accepts requests from several local processes (dashboard,
Twitch and Discord workers) and their bodies say who is speaking. Those claims
are not proof. The dashboard, which sits behind its own login, adds this secret
as a header on its server-side calls; the API treats a request as the owner only
when the header matches. The secret lives in a private file under data/runtime,
is created on first use, and never reaches a browser.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from ..config import settings

HEADER = "X-Gamma-Owner-Channel"


def _path() -> Path:
    return settings.data_dir / "runtime" / "owner-channel.key"


def owner_channel_secret() -> str:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(secrets.token_urlsafe(32))
    value = path.read_text(encoding="utf-8").strip()
    if len(value) < 32:
        raise RuntimeError(f"owner channel secret at {path} is missing or too short")
    return value


def is_owner_channel(header_value: str | None) -> bool:
    if not header_value:
        return False
    return secrets.compare_digest(header_value.encode("utf-8"), owner_channel_secret().encode("utf-8"))
