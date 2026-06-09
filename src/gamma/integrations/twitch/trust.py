from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ...config import settings
from ...errors import ConfigurationError
from .models import TrustLevel


VALID_TRUST_LEVELS: set[str] = {"owner", "trusted", "regular", "normal", "new_viewer", "suspicious", "blocked"}


@dataclass(frozen=True, slots=True)
class ViewerTrustRecord:
    platform: str
    platform_user_id: str
    display_name: str | None
    trust_level: TrustLevel
    notes: str | None = None
    pronunciation_alias: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ViewerTrustStore:
    def __init__(self, *, database_url: str | None = None) -> None:
        self.database_url = database_url or settings.database_url
        self.path = _sqlite_path_from_url(self.database_url)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def get(self, *, platform: str, platform_user_id: str) -> ViewerTrustRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT platform, platform_user_id, display_name, trust_level, notes,
                       pronunciation_alias, created_at, updated_at
                FROM stream_viewer_trust
                WHERE platform = ? AND platform_user_id = ?
                """,
                (platform, platform_user_id),
            ).fetchone()
        if row is None:
            return None
        return _record_from_row(row)

    def trust_level_for(self, *, platform: str, platform_user_id: str | None, default: TrustLevel = "new_viewer") -> TrustLevel:
        if not platform_user_id:
            return default
        record = self.get(platform=platform, platform_user_id=platform_user_id)
        return record.trust_level if record else default

    def upsert(
        self,
        *,
        platform: str,
        platform_user_id: str,
        display_name: str | None = None,
        trust_level: TrustLevel = "normal",
        notes: str | None = None,
        pronunciation_alias: str | None = None,
    ) -> ViewerTrustRecord:
        _validate_trust_level(trust_level)
        now = _utc_now()
        existing = self.get(platform=platform, platform_user_id=platform_user_id)
        created_at = existing.created_at if existing and existing.created_at else now
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO stream_viewer_trust (
                    platform, platform_user_id, display_name, trust_level, notes,
                    pronunciation_alias, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, platform_user_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    trust_level = excluded.trust_level,
                    notes = excluded.notes,
                    pronunciation_alias = excluded.pronunciation_alias,
                    updated_at = excluded.updated_at
                """,
                (
                    platform,
                    platform_user_id,
                    display_name,
                    trust_level,
                    notes,
                    pronunciation_alias,
                    created_at,
                    now,
                ),
            )
            conn.commit()
        record = self.get(platform=platform, platform_user_id=platform_user_id)
        if record is None:
            raise ConfigurationError("viewer trust write failed")
        return record

    def list_records(self, *, platform: str | None = None, limit: int = 100) -> list[ViewerTrustRecord]:
        params: list[object] = []
        where = ""
        if platform:
            where = "WHERE platform = ?"
            params.append(platform)
        params.append(max(1, min(limit, 1000)))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT platform, platform_user_id, display_name, trust_level, notes,
                       pronunciation_alias, created_at, updated_at
                FROM stream_viewer_trust
                {where}
                ORDER BY updated_at DESC, platform_user_id ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [_record_from_row(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS stream_viewer_trust (
                    platform TEXT NOT NULL,
                    platform_user_id TEXT NOT NULL,
                    display_name TEXT,
                    trust_level TEXT NOT NULL DEFAULT 'normal',
                    notes TEXT,
                    pronunciation_alias TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (platform, platform_user_id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_stream_viewer_trust_trust_level ON stream_viewer_trust (trust_level)"
            )
            conn.commit()


def _sqlite_path_from_url(database_url: str) -> Path:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ConfigurationError("viewer trust store currently requires a sqlite database_url")
    raw = database_url[len(prefix):]
    path = Path(raw)
    if not path.is_absolute():
        path = settings.project_root / raw
    return path


def _validate_trust_level(trust_level: str) -> None:
    if trust_level not in VALID_TRUST_LEVELS:
        raise ValueError(f"unsupported viewer trust level: {trust_level}")


def _record_from_row(row: sqlite3.Row) -> ViewerTrustRecord:
    trust_level = str(row["trust_level"])
    _validate_trust_level(trust_level)
    return ViewerTrustRecord(
        platform=str(row["platform"]),
        platform_user_id=str(row["platform_user_id"]),
        display_name=row["display_name"],
        trust_level=trust_level,  # type: ignore[arg-type]
        notes=row["notes"],
        pronunciation_alias=row["pronunciation_alias"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

