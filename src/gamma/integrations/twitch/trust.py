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
    """Immutable record of a viewer's trust level across platforms.
    
    Attributes:
        platform: Platform name (discord, twitch, etc.).
        platform_user_id: User ID on that platform.
        display_name: User display name.
        trust_level: Trust classification.
        notes: Optional notes about the viewer.
        pronunciation_alias: Pronunciation alias for the username.
        created_at: When record was created.
        updated_at: When record was last updated.
    """
    platform: str
    platform_user_id: str
    display_name: str | None
    trust_level: TrustLevel
    notes: str | None = None
    pronunciation_alias: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ViewerTrustStore:
    """DAO for viewer trust records across platforms."""

    def __init__(self, *, database_url: str | None = None) -> None:
        """Initialize trust store with optional database URL.
        
        Args:
            database_url: SQLite database URL; uses settings default if None.
        """
        self.database_url = database_url or settings.database_url
        self.path = _sqlite_path_from_url(self.database_url)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def get(self, *, platform: str, platform_user_id: str) -> ViewerTrustRecord | None:
        """Get trust record for a user on a platform.
        
        Args:
            platform: Platform name.
            platform_user_id: User ID on platform.
            
        Returns:
            ViewerTrustRecord or None if no record exists.
        """
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
        """Get trust level for a user, returning default if none.
        
        Args:
            platform: Platform name.
            platform_user_id: User ID or None.
            default: Default level when no record exists.
            
        Returns:
            TrustLevel.
        """
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
        """Insert or update a trust record.
        
        Args:
            platform: Platform name.
            platform_user_id: User ID on platform.
            display_name: User display name.
            trust_level: Trust level.
            notes: Optional notes.
            pronunciation_alias: Pronunciation alias.
            
        Returns:
            ViewerTrustRecord with updated timestamp.
        """
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
        """List trust records, optionally filtered by platform.
        
        Args:
            platform: Optional platform filter.
            limit: Maximum records to return.
            
        Returns:
            list[ViewerTrustRecord]: Recent records, limited.
        """
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
        """Get database connection with row factory."""
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        """Ensure database schema exists."""
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
    """Convert SQLite URL to Path, relative to project root if needed.
    
    Args:
        database_url: SQLite database URL.
        
    Returns:
        Path to the database file.
    """
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ConfigurationError("viewer trust store currently requires a sqlite database_url")
    raw = database_url[len(prefix):]
    path = Path(raw)
    if not path.is_absolute():
        path = settings.project_root / raw
    return path


def _validate_trust_level(trust_level: str) -> None:
    """Validate trust level string.
    
    Args:
        trust_level: Trust level to validate.
        
    Raises:
        ValueError: If trust level is not valid.
    """
    if trust_level not in VALID_TRUST_LEVELS:
        raise ValueError(f"unsupported viewer trust level: {trust_level}")


def _record_from_row(row: sqlite3.Row) -> ViewerTrustRecord:
    """Convert a database row into a ViewerTrustRecord.
    
    Args:
        row: SQLite row.
        
    Returns:
        ViewerTrustRecord.
    """
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
    """Get current UTC time in ISO 8601 format.
    
    Returns:
        str: UTC timestamp.
    """
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

