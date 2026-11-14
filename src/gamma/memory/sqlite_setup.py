"""Shared SQLite file and connection setup for the memory stores."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

_SQLITE_PREFIX = "sqlite:///"
_WAL_SWITCH_DEADLINE_SECONDS = 5.0


def sqlite_path(database_url: str) -> Path | None:
    """The database file of a ``sqlite:///`` URL; relative paths are under the project root."""
    if not database_url.startswith(_SQLITE_PREFIX):
        return None
    raw = database_url[len(_SQLITE_PREFIX):]
    path = Path(raw)
    if not path.is_absolute():
        from ..config import settings
        path = settings.project_root / raw
    return path


def ensure_sqlite_parent(database_url: str) -> None:
    """Create the parent directory of a file-backed SQLite URL.

    SQLite creates the database file on first connect but not its directory,
    so a fresh checkout without ``data/memory`` failed with "unable to open
    database file". Relative paths resolve against the working directory,
    matching how SQLAlchemy opens them.
    """
    if not database_url.startswith(_SQLITE_PREFIX):
        return
    raw = database_url[len(_SQLITE_PREFIX):].split("?", 1)[0]
    if not raw or raw == ":memory:" or raw.startswith("file:"):
        return
    Path(raw).parent.mkdir(parents=True, exist_ok=True)


def enable_wal(cursor) -> None:
    """Switch the connection to WAL, waiting out a concurrent switch.

    Converting a fresh database to WAL needs an exclusive lock. When several
    connections open a new database at once, the losers can get "database is
    locked" from this pragma even with a busy timeout set. Retry until the
    deadline instead of failing whichever initializer lost the race.
    """
    deadline = time.monotonic() + _WAL_SWITCH_DEADLINE_SECONDS
    delay = 0.005
    while True:
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.1)
