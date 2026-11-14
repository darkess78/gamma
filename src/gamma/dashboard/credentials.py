"""Dashboard login credentials: password hashing and the reset command.

Run on Gamma PC from the Gamma checkout:

    .venv/bin/python -m gamma.dashboard.reset_password [--username NAME] [--restart]

The password is read with hidden input, stored only as a salted scrypt hash in
config/app.local.toml, and the session secret is rotated so every browser has to
sign in again.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
from pathlib import Path

_SCHEME = "scrypt"
_N, _R, _P = 2**14, 8, 1
MINIMUM_PASSWORD_LENGTH = 10


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    encode = lambda raw: base64.b64encode(raw).decode("ascii")
    return f"{_SCHEME}${_N}${_R}${_P}${encode(salt)}${encode(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != _SCHEME:
            return False
        digest = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt),
                                n=int(n), r=int(r), p=int(p), dklen=len(base64.b64decode(expected)))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, base64.b64decode(expected))


_TABLE = re.compile(r"^\s*\[")


def set_top_level_keys(text: str, values: dict[str, str | bool], remove: tuple[str, ...] = ()) -> str:
    """Set top-level TOML keys, keeping them above the first [table] header.

    A key written after a table header would belong to that table, so new keys
    are inserted before it and existing top-level lines are replaced in place.
    """
    lines = text.splitlines()
    first_table = next((i for i, line in enumerate(lines) if _TABLE.match(line)), len(lines))
    head, tail = lines[:first_table], lines[first_table:]

    def render(key: str, value: str | bool) -> str:
        return f"{key} = {'true' if value is True else 'false' if value is False else json.dumps(value)}"

    def key_of(line: str) -> str | None:
        match = re.match(r"^\s*([A-Za-z0-9_-]+)\s*=", line)
        return match.group(1) if match else None

    pending = dict(values)
    out = []
    for line in head:
        key = key_of(line)
        if key in remove:
            continue
        if key in pending:
            out.append(render(key, pending.pop(key)))
        else:
            out.append(line)
    while out and not out[-1].strip():
        out.pop()
    out.extend(render(key, value) for key, value in pending.items())
    if tail:
        out.append("")
    return "\n".join(out + tail) + "\n"


def write_private(path: Path, text: str) -> None:
    """Atomic write, readable only by the owner."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def reset_credentials(path: Path, *, username: str, password: str) -> None:
    if len(password) < MINIMUM_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MINIMUM_PASSWORD_LENGTH} characters")
    if not re.fullmatch(r"[A-Za-z0-9._@-]{1,64}", username):
        raise ValueError("username may use letters, digits and . _ @ - (up to 64)")
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    updated = set_top_level_keys(
        existing,
        {
            "dashboard_auth_enabled": True,
            "dashboard_auth_username": username,
            "dashboard_auth_password_hash": hash_password(password),
            "dashboard_session_secret": secrets.token_urlsafe(32),
        },
        remove=("dashboard_auth_password",),
    )
    write_private(path, updated)
