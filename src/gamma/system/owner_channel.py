"""Proof that an API request came from the owner's authenticated dashboard.

The Shana API accepts requests from several local processes (dashboard,
Twitch and Discord workers) and their bodies say who is speaking. Those claims
are not proof. The dashboard, which sits behind its own login, adds this secret
as a header on its server-side calls; the API treats a request as the owner only
when the header matches. The secret lives in a private file under data/runtime,
is created on first use, and never reaches a browser.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

from ..config import settings

HEADER = "X-Gamma-Owner-Channel"
# Gamma's own integration workers (Twitch, Discord) add this so the API can
# believe the platform identity in their events. A body that only *claims*
# a platform id (say, the owner's public Discord id) proves nothing.
INGRESS_HEADER = "X-Gamma-Ingress"


def _path(name: str = "owner-channel.key") -> Path:
    return settings.data_dir / "runtime" / name


def owner_channel_secret() -> str:
    return _secret("owner-channel.key")


def ingress_secret() -> str:
    return _secret("ingress.key")


def _secret(name: str) -> str:
    path = _path(name)
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
        raise RuntimeError(f"secret at {path} is missing or too short")
    return value


def is_owner_channel(header_value: str | None) -> bool:
    if not header_value:
        return False
    return secrets.compare_digest(header_value.encode("utf-8"), owner_channel_secret().encode("utf-8"))


def is_ingress(header_value: str | None) -> bool:
    if not header_value:
        return False
    return secrets.compare_digest(header_value.encode("utf-8"), ingress_secret().encode("utf-8"))


def identity_proven(headers) -> bool:
    """The caller is the owner's dashboard or one of Gamma's own integration workers."""
    return is_owner_channel(headers.get(HEADER)) or is_ingress(headers.get(INGRESS_HEADER))


TICKET_SECONDS = 600


def issue_ticket(now: float | None = None) -> str:
    """A short-lived pass for the owner's private live feed (Monitor), issued by the logged-in
    dashboard. Browsers can't put headers on WebSockets, and a URL-borne master token leaks
    into exports and logs; a ticket that expires in minutes is harmless if it does."""
    expires = int((now or time.time()) + TICKET_SECONDS)
    mac = hmac.new(owner_channel_secret().encode(), f"ticket:{expires}".encode(), hashlib.sha256).hexdigest()[:40]
    return f"{expires}.{mac}"


def ticket_ok(ticket: str | None, now: float | None = None) -> bool:
    try:
        expires_text, mac = str(ticket or "").split(".", 1)
        expires = int(expires_text)
    except ValueError:
        return False
    if expires < (now or time.time()) or expires > (now or time.time()) + TICKET_SECONDS + 5:
        return False
    expected = hmac.new(owner_channel_secret().encode(), f"ticket:{expires}".encode(), hashlib.sha256).hexdigest()[:40]
    return secrets.compare_digest(mac, expected)
