from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from fastapi import Request, WebSocket
from ..config import settings
from .passwords import load_credentials_file, verify_password

@dataclass(frozen=True, slots=True)
class DashboardAuthConfig:
    enabled: bool
    username: str
    password: str = field(repr=False)
    session_secret: str = field(repr=False)
    cookie_secure: bool
    password_hash: str = field(default='',repr=False)
    credential_version: str = ''

def auth_config() -> DashboardAuthConfig:
    filename=getattr(settings,'dashboard_credentials_file','')
    if filename:
        try:
            record=load_credentials_file(filename)
            return DashboardAuthConfig(True,record['username'],'',record['session_secret'],settings.dashboard_cookie_secure,
                                       record['password_hash'],record['credential_version'])
        except (OSError,ValueError,TypeError):
            # Configured persistent credentials never fall back to a legacy password.
            return DashboardAuthConfig(True,'','','',settings.dashboard_cookie_secure)
    return DashboardAuthConfig(settings.dashboard_auth_enabled,settings.dashboard_auth_username,
        settings.dashboard_auth_password,settings.dashboard_session_secret,settings.dashboard_cookie_secure)

def _ready(config):
    return config.enabled and bool(config.username and config.session_secret and (config.password_hash or config.password))

def dashboard_auth_ready() -> bool:
    return _ready(auth_config())

def _verify(config,username,password):
    if not _ready(config) or not isinstance(username,str) or not isinstance(password,str):
        return False
    try:
        matching_user=secrets.compare_digest(username.encode('utf-8'),config.username.encode('utf-8'))
    except UnicodeError:
        return False
    if not matching_user:
        return False
    if config.password_hash:
        return verify_password(password,config.password_hash)
    return secrets.compare_digest(password.encode('utf-8'),config.password.encode('utf-8'))

def verify_login(username: str,password: str) -> bool:
    return _verify(auth_config(),username,password)

def _cookie(config,username):
    message=username.encode('utf-8')
    if config.password_hash:
        message+=b'\0credential-v1\0'+config.credential_version.encode('ascii')+b'\0'+hashlib.sha256(config.password_hash.encode('ascii')).digest()
    digest=hmac.new(config.session_secret.encode('utf-8'),message,hashlib.sha256).hexdigest()
    return username+':'+digest

def authenticate_login(username: str,password: str) -> str | None:
    config=auth_config()
    return _cookie(config,username) if _verify(config,username,password) else None

def session_cookie_value(username: str) -> str:
    return _cookie(auth_config(),username)

def _authenticated(cookies):
    config=auth_config()
    if not config.enabled:
        return True
    if not _ready(config):
        return False
    cookie=cookies.get('gamma_dashboard_session')
    if not isinstance(cookie,str) or len(cookie)>256:
        return False
    try:
        username,provided=cookie.split(':',1)
        expected=_cookie(config,username).split(':',1)[1]
        return secrets.compare_digest(username.encode('utf-8'),config.username.encode('utf-8')) and secrets.compare_digest(provided.encode('utf-8'),expected.encode('utf-8'))
    except (ValueError,UnicodeError):
        return False

def is_authenticated(request: Request) -> bool:
    return _authenticated(request.cookies)

def websocket_is_authenticated(websocket: WebSocket) -> bool:
    return _authenticated(websocket.cookies)
