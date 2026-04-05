from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from fastapi import Request, WebSocket

from ..config import settings


@dataclass(frozen=True, slots=True)
class DashboardAuthConfig:
    enabled: bool
    username: str
    password: str
    session_secret: str
    cookie_secure: bool


def auth_config() -> DashboardAuthConfig:
    return DashboardAuthConfig(
        enabled=settings.dashboard_auth_enabled,
        username=settings.dashboard_auth_username,
        password=settings.dashboard_auth_password,
        session_secret=settings.dashboard_session_secret,
        cookie_secure=settings.dashboard_cookie_secure,
    )


def dashboard_auth_ready() -> bool:
    config = auth_config()
    if not config.enabled:
        return False
    return bool(config.username and config.password and config.session_secret)


def verify_login(username: str, password: str) -> bool:
    config = auth_config()
    return secrets.compare_digest(username, config.username) and secrets.compare_digest(password, config.password)


def session_cookie_value(username: str) -> str:
    config = auth_config()
    digest = hmac.new(
        config.session_secret.encode("utf-8"),
        username.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{username}:{digest}"


def is_authenticated(request: Request) -> bool:
    config = auth_config()
    if not config.enabled:
        return True
    cookie = request.cookies.get("gamma_dashboard_session")
    if not cookie:
        return False
    try:
        username, provided = cookie.split(":", 1)
    except ValueError:
        return False
    expected = session_cookie_value(username).split(":", 1)[1]
    return secrets.compare_digest(username, config.username) and secrets.compare_digest(provided, expected)


def websocket_is_authenticated(websocket: WebSocket) -> bool:
    config = auth_config()
    if not config.enabled:
        return True
    cookie = websocket.cookies.get("gamma_dashboard_session")
    if not cookie:
        return False
    try:
        username, provided = cookie.split(":", 1)
    except ValueError:
        return False
    expected = session_cookie_value(username).split(":", 1)[1]
    return secrets.compare_digest(username, config.username) and secrets.compare_digest(provided, expected)
