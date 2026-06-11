from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from ..config import settings
from ..errors import ConfigurationError


SelfGoalStatus = Literal["proposed", "approved", "rejected", "cleared"]
VALID_SELF_GOAL_STATUSES = {"proposed", "approved", "rejected", "cleared"}


@dataclass(frozen=True, slots=True)
class StreamSelfGoal:
    id: int
    title: str
    description: str
    status: SelfGoalStatus
    source: str
    metadata: dict[str, Any]
    created_at: str
    updated_at: str

    def as_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "source": self.source,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class StreamSelfGoalStore:
    def __init__(self, *, database_url: str | None = None) -> None:
        self.database_url = database_url or settings.database_url
        self.path = _sqlite_path_from_url(self.database_url)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def propose(self, *, title: str, description: str, source: str = "stream_brain", metadata: dict[str, Any] | None = None) -> StreamSelfGoal:
        normalized_title = " ".join(title.split())[:160]
        normalized_description = " ".join(description.split())[:500]
        if not normalized_title or not normalized_description:
            raise ValueError("self-goal title and description are required")
        now = _utc_now()
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT id FROM stream_self_goals
                WHERE title = ? AND status IN ('proposed', 'approved')
                """,
                (normalized_title,),
            ).fetchone()
            if existing:
                record_id = int(existing["id"])
                conn.execute(
                    """
                    UPDATE stream_self_goals
                    SET description = ?, metadata_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (normalized_description, json.dumps(metadata or {}, ensure_ascii=False), now, record_id),
                )
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO stream_self_goals (title, description, status, source, metadata_json, created_at, updated_at)
                    VALUES (?, ?, 'proposed', ?, ?, ?, ?)
                    """,
                    (normalized_title, normalized_description, source, json.dumps(metadata or {}, ensure_ascii=False), now, now),
                )
                record_id = int(cursor.lastrowid)
            conn.commit()
        record = self.get(record_id)
        if record is None:
            raise ConfigurationError("stream self-goal write failed")
        return record

    def set_status(self, goal_id: int, *, status: str) -> StreamSelfGoal:
        _validate_status(status)
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE stream_self_goals SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, goal_id),
            )
            conn.commit()
        record = self.get(goal_id)
        if record is None:
            raise KeyError(goal_id)
        return record

    def clear(self) -> dict[str, Any]:
        now = _utc_now()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE stream_self_goals SET status = 'cleared', updated_at = ? WHERE status IN ('proposed', 'approved')",
                (now,),
            )
            conn.commit()
        return {"ok": True, "cleared": int(cursor.rowcount or 0)}

    def get(self, goal_id: int) -> StreamSelfGoal | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, title, description, status, source, metadata_json, created_at, updated_at
                FROM stream_self_goals
                WHERE id = ?
                """,
                (goal_id,),
            ).fetchone()
        return _record_from_row(row) if row else None

    def list_goals(self, *, status: str | None = None, limit: int = 100) -> dict[str, Any]:
        params: list[object] = []
        where = ""
        if status:
            _validate_status(status)
            where = "WHERE status = ?"
            params.append(status)
        params.append(max(1, min(limit, 1000)))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id, title, description, status, source, metadata_json, created_at, updated_at
                FROM stream_self_goals
                {where}
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return {
            "statuses": sorted(VALID_SELF_GOAL_STATUSES),
            "items": [_record_from_row(row).as_payload() for row in rows],
        }

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS stream_self_goals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS ix_stream_self_goals_status ON stream_self_goals (status)")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_stream_self_goals_updated_at ON stream_self_goals (updated_at)")
            conn.commit()


def _sqlite_path_from_url(database_url: str) -> Path:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ConfigurationError("stream self-goals currently require a sqlite database_url")
    raw = database_url[len(prefix):]
    path = Path(raw)
    if not path.is_absolute():
        path = settings.project_root / raw
    return path


def _validate_status(status: str) -> None:
    if status not in VALID_SELF_GOAL_STATUSES:
        raise ValueError(f"unsupported self-goal status: {status}")


def _record_from_row(row: sqlite3.Row) -> StreamSelfGoal:
    try:
        metadata = json.loads(str(row["metadata_json"] or "{}"))
    except json.JSONDecodeError:
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    status = str(row["status"])
    _validate_status(status)
    return StreamSelfGoal(
        id=int(row["id"]),
        title=str(row["title"]),
        description=str(row["description"]),
        status=status,  # type: ignore[arg-type]
        source=str(row["source"]),
        metadata=metadata,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
