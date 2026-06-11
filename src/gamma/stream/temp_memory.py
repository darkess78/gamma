from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import settings
from ..errors import ConfigurationError
from .models import StreamTurnResult


STREAM_TEMP_MEMORY_BUCKETS = {"chat_mood", "event_history", "owner_directives"}


@dataclass(frozen=True, slots=True)
class StreamTempMemoryRecord:
    """Stream temp memory record.
    
    Attributes:
        id: Record ID.
        bucket: Bucket name (chat_mood|event_history|owner_directives).
        key: Record key.
        value: Record value.
        metadata: Record metadata.
        created_at: Creation timestamp.
        updated_at: Update timestamp.
    
    Methods:
        as_payload: Convert to payload dict.
    """
    id: int
    bucket: str
    key: str
    value: str
    metadata: dict[str, Any]
    created_at: str
    updated_at: str

    def as_payload(self) -> dict[str, Any]:
        """Convert to payload dict.
        
        Returns:
            dict[str, Any]: Payload dict.
        """
        return {
            "id": self.id,
            "bucket": self.bucket,
            "key": self.key,
            "value": self.value,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class StreamTempMemoryStore:
    """Stream temp memory store.
    
    Attributes:
        database_url: Database URL.
        path: SQLite path.
    
    Methods:
        __init__: Initialize temp memory store.
        record_turn: Record turn result.
        upsert: Upsert memory record.
        add: Add memory record.
        get: Get record by bucket and key.
        get_by_id: Get record by ID.
        list_records: List records.
        clear: Clear bucket.
    """

    def __init__(self, *, database_url: str | None = None) -> None:
        """Initialize temp memory store.
        
        Args:
            database_url: Database URL (default from settings).
        """
        self.database_url = database_url or settings.database_url
        self.path = _sqlite_path_from_url(self.database_url)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def record_turn(self, result: StreamTurnResult) -> None:
        """Record turn result.
        
        Args:
            result: StreamTurnResult to record.
        """
        if not _is_public_stream_result(result):
            return
        input_event = result.input_event
        metadata = input_event.metadata or {}
        safety = result.safety_decision or metadata.get("input_safety") or {}
        safe_text = _safe_stream_text(result)
        event_value = f"{input_event.kind}: {safe_text}" if safe_text else input_event.kind
        event_metadata = {
            "event_id": input_event.event_id,
            "trace_id": result.trace_id,
            "kind": input_event.kind,
            "decision": result.decision.decision,
            "reason": result.decision.reason,
            "actor": input_event.actor.model_dump(),
            "safety_action": safety.get("action") if isinstance(safety, dict) else None,
            "safety_category": safety.get("category") if isinstance(safety, dict) else None,
        }
        self.add(bucket="event_history", key=result.trace_id, value=event_value, metadata=event_metadata)
        self.upsert(
            bucket="chat_mood",
            key="recent_activity",
            value=_recent_activity_summary(result, safe_text),
            metadata={
                "last_event_id": input_event.event_id,
                "last_trace_id": result.trace_id,
                "last_kind": input_event.kind,
                "last_decision": result.decision.decision,
                "last_reason": result.decision.reason,
            },
        )

    def upsert(self, *, bucket: str, key: str, value: str, metadata: dict[str, Any] | None = None) -> StreamTempMemoryRecord:
        """Upsert memory record.
        
        Args:
            bucket: Bucket name.
            key: Record key.
            value: Record value.
            metadata: Optional metadata.
        
        Returns:
            StreamTempMemoryRecord: Upserted record.
        
        Raises:
            ConfigurationError: If write failed.
        """
        _validate_bucket(bucket)
        normalized_key = " ".join((key or "").split())[:120] or "default"
        now = _utc_now()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM stream_temp_memory WHERE bucket = ? AND key = ?",
                (bucket, normalized_key),
            ).fetchone()
            created_at = str(existing["created_at"]) if existing else now
            conn.execute(
                """
                INSERT INTO stream_temp_memory (bucket, key, value, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(bucket, key) DO UPDATE SET
                    value = excluded.value,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (bucket, normalized_key, value, json.dumps(metadata or {}, ensure_ascii=False), created_at, now),
            )
            conn.commit()
        record = self.get(bucket=bucket, key=normalized_key)
        if record is None:
            raise ConfigurationError("stream temp memory write failed")
        return record

    def add(self, *, bucket: str, key: str, value: str, metadata: dict[str, Any] | None = None) -> StreamTempMemoryRecord:
        """Add memory record.
        
        Args:
            bucket: Bucket name.
            key: Record key.
            value: Record value.
            metadata: Optional metadata.
        
        Returns:
            StreamTempMemoryRecord: Added record.
        
        Raises:
            ConfigurationError: If write failed.
        """
        _validate_bucket(bucket)
        now = _utc_now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO stream_temp_memory (bucket, key, value, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (bucket, key[:120] or "event", value, json.dumps(metadata or {}, ensure_ascii=False), now, now),
            )
            conn.commit()
            record_id = int(cursor.lastrowid)
        record = self.get_by_id(record_id)
        if record is None:
            raise ConfigurationError("stream temp memory write failed")
        return record

    def get(self, *, bucket: str, key: str) -> StreamTempMemoryRecord | None:
        """Get record by bucket and key.
        
        Args:
            bucket: Bucket name.
            key: Record key.
        
        Returns:
            StreamTempMemoryRecord | None: Record or None.
        """
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, bucket, key, value, metadata_json, created_at, updated_at
                FROM stream_temp_memory
                WHERE bucket = ? AND key = ?
                """,
                (bucket, key),
            ).fetchone()
        return _record_from_row(row) if row else None

    def get_by_id(self, record_id: int) -> StreamTempMemoryRecord | None:
        """Get record by ID.
        
        Args:
            record_id: Record ID.
        
        Returns:
            StreamTempMemoryRecord | None: Record or None.
        """
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, bucket, key, value, metadata_json, created_at, updated_at
                FROM stream_temp_memory
                WHERE id = ?
                """,
                (record_id,),
            ).fetchone()
        return _record_from_row(row) if row else None

    def list_records(self, *, bucket: str | None = None, limit: int = 100) -> dict[str, Any]:
        """List records.
        
        Args:
            bucket: Optional bucket filter.
            limit: Optional max limit (default 100).
        
        Returns:
            dict[str, Any]: Records data.
        """
        params: list[object] = []
        where = ""
        if bucket:
            _validate_bucket(bucket)
            where = "WHERE bucket = ?"
            params.append(bucket)
        params.append(max(1, min(limit, 1000)))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id, bucket, key, value, metadata_json, created_at, updated_at
                FROM stream_temp_memory
                {where}
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return {
            "buckets": sorted(STREAM_TEMP_MEMORY_BUCKETS),
            "items": [_record_from_row(row).as_payload() for row in rows],
        }

    def clear(self, *, bucket: str | None = None) -> dict[str, Any]:
        """Clear records.
        
        Args:
            bucket: Optional bucket to clear (default all).
        
        Returns:
            dict[str, Any]: Clear confirmation.
        """
        if bucket:
            _validate_bucket(bucket)
        with self._connect() as conn:
            if bucket:
                cursor = conn.execute("DELETE FROM stream_temp_memory WHERE bucket = ?", (bucket,))
            else:
                cursor = conn.execute("DELETE FROM stream_temp_memory")
            conn.commit()
            deleted = int(cursor.rowcount or 0)
        return {"ok": True, "deleted": deleted, "bucket": bucket}

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS stream_temp_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bucket TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(bucket, key)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS ix_stream_temp_memory_bucket ON stream_temp_memory (bucket)")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_stream_temp_memory_updated_at ON stream_temp_memory (updated_at)")
            conn.commit()


def _is_public_stream_result(result: StreamTurnResult) -> bool:
    return result.input_event.actor.source == "twitch" or result.input_event.kind in {
        "chat_message",
        "follow",
        "raid",
        "donation",
        "bits",
        "subscription",
        "redeem",
    }


def _safe_stream_text(result: StreamTurnResult) -> str:
    metadata = result.input_event.metadata or {}
    input_safety = metadata.get("input_safety") if isinstance(metadata.get("input_safety"), dict) else {}
    safe_prompt_text = str(input_safety.get("safe_prompt_text") or metadata.get("safe_prompt_text") or "").strip()
    text = safe_prompt_text or str(result.input_event.text or "").strip()
    return " ".join(text.split())[:240]


def _recent_activity_summary(result: StreamTurnResult, safe_text: str) -> str:
    actor = result.input_event.actor.display_name or result.input_event.actor.platform_id or result.input_event.actor.source
    if safe_text:
        return f"Recent {result.input_event.kind} from {actor}: {safe_text}"
    return f"Recent {result.input_event.kind} from {actor}; decision {result.decision.decision}/{result.decision.reason}."


def _sqlite_path_from_url(database_url: str) -> Path:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ConfigurationError("stream temp memory currently requires a sqlite database_url")
    raw = database_url[len(prefix):]
    path = Path(raw)
    if not path.is_absolute():
        path = settings.project_root / raw
    return path


def _validate_bucket(bucket: str) -> None:
    if bucket not in STREAM_TEMP_MEMORY_BUCKETS:
        raise ValueError(f"unsupported stream temp memory bucket: {bucket}")


def _record_from_row(row: sqlite3.Row) -> StreamTempMemoryRecord:
    try:
        metadata = json.loads(str(row["metadata_json"] or "{}"))
    except json.JSONDecodeError:
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    return StreamTempMemoryRecord(
        id=int(row["id"]),
        bucket=str(row["bucket"]),
        key=str(row["key"]),
        value=str(row["value"]),
        metadata=metadata,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
