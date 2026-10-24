from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import event, func, update
from sqlalchemy.pool import NullPool
from sqlmodel import Field, Session, SQLModel, create_engine, delete, select

from ..config import settings
from ..llm.capabilities import estimate_text_tokens
from .journal_identity import capture_identity, owner_is_dead


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ConversationJournalEntry(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    exchange_id: str = Field(index=True)
    session_id: str = Field(index=True)
    role: str = Field(index=True)
    speaker_name: str | None = None
    input_source: str = "unknown"
    text: str
    occurred_at: datetime = Field(default_factory=_utc_now, index=True)
    trace_id: str | None = Field(default=None, index=True)
    output_target: str | None = None
    privacy_scope: str = Field(default="local_generic", index=True)
    completion_status: str = Field(default="pending", index=True)
    tool_metadata_json: str = "{}"
    owner_id: str | None = Field(default=None, index=True)
    input_sequence: int | None = Field(default=None, exclude=True)


class ConversationInputCursor(SQLModel, table=True):
    """Private receive high-water mark, retained while its scope has work/context."""

    session_id: str = Field(primary_key=True)
    privacy_scope: str = Field(primary_key=True)
    latest_sequence: int = Field(default=0, exclude=True)
    updated_at: datetime = Field(default_factory=_utc_now, exclude=True)


class ConversationJournalOwner(SQLModel, table=True):
    """Internal process identity. Never include these fields in API payloads."""

    owner_id: str = Field(primary_key=True)
    host: str
    pid: int
    process_started: float
    checked_at: datetime = Field(default_factory=_utc_now, index=True)


class JournalOwnershipError(RuntimeError):
    """The pending exchange is no longer owned by this service/process."""


class RollingSessionSummary(SQLModel, table=True):
    session_id: str = Field(primary_key=True)
    summary_text: str = ""
    through_entry_id: int | None = None
    completed_exchanges: int = 0
    privacy_scope: str = "local_generic"
    updated_at: datetime = Field(default_factory=_utc_now)


class WorkingStateCheckpoint(SQLModel, table=True):
    session_id: str = Field(primary_key=True)
    active_topic: str = ""
    current_objective: str = ""
    pending_questions_json: str = "[]"
    commitments_json: str = "[]"
    relevant_people_json: str = "[]"
    last_meaningful_interaction: datetime | None = None
    last_assistant_action: str = ""
    next_intended_action: str = ""
    privacy_scope: str = "local_generic"
    updated_at: datetime = Field(default_factory=_utc_now)


class ScopedSessionSummary(SQLModel, table=True):
    """New summaries are isolated by session and exact privacy scope."""

    session_id: str = Field(primary_key=True)
    privacy_scope: str = Field(primary_key=True)
    summary_text: str = ""
    through_entry_id: int | None = None
    completed_exchanges: int = 0
    updated_at: datetime = Field(default_factory=_utc_now)


class ScopedWorkingStateCheckpoint(SQLModel, table=True):
    session_id: str = Field(primary_key=True)
    privacy_scope: str = Field(primary_key=True)
    active_topic: str = ""
    current_objective: str = ""
    pending_questions_json: str = "[]"
    commitments_json: str = "[]"
    relevant_people_json: str = "[]"
    last_meaningful_interaction: datetime | None = None
    last_assistant_action: str = ""
    next_intended_action: str = ""
    updated_at: datetime = Field(default_factory=_utc_now)


class DurableOutputState(SQLModel, table=True):
    target_policy: str = Field(primary_key=True)
    turn_id: str
    text: str
    status: str
    spoken: bool = False
    occurred_at: datetime = Field(default_factory=_utc_now)


class ContinuityService:
    """Durable recent-turn, rolling-summary, and working-state storage."""

    def __init__(self, *, database_url: str | None = None) -> None:
        url = database_url or settings.database_url
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        engine_kwargs = {"poolclass": NullPool} if url.startswith("sqlite") else {}
        self._engine = create_engine(url, connect_args=connect_args, **engine_kwargs)
        self._owner_id = uuid4().hex
        self._host, self._pid, self._process_started = capture_identity()
        if url.startswith("sqlite"):
            event.listen(self._engine, "connect", self._configure_sqlite)
        if url.startswith("sqlite"):
            # Serialize schema checks as well as DDL across simultaneous instances.
            with self._engine.connect() as connection:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                SQLModel.metadata.create_all(connection)
                self._ensure_sqlite_column(connection, "conversationjournalentry", "owner_id", "TEXT")
                self._ensure_sqlite_column(connection, "conversationjournalentry", "input_sequence", "INTEGER")
                connection.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS ix_conversationjournalentry_owner_id "
                    "ON conversationjournalentry (owner_id)"
                )
                self._ensure_sqlite_column(
                    connection,
                    "rollingsessionsummary",
                    "privacy_scope",
                    "TEXT DEFAULT 'local_generic'",
                )
                self._ensure_sqlite_column(
                    connection,
                    "workingstatecheckpoint",
                    "privacy_scope",
                    "TEXT DEFAULT 'local_generic'",
                )
                connection.commit()
        else:
            SQLModel.metadata.create_all(self._engine)

    @contextmanager
    def _write_session(self):
        with Session(self._engine) as session:
            if self._engine.dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            try:
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise

    def _require_current_process(self) -> None:
        try:
            current = capture_identity()
        except Exception as exc:
            raise JournalOwnershipError("journal_owner_unverifiable") from exc
        if current != (self._host, self._pid, self._process_started):
            raise JournalOwnershipError("journal_owner_process_changed")

    def _registered_owner(self, session: Session) -> ConversationJournalOwner:
        owner = session.get(ConversationJournalOwner, self._owner_id)
        if owner is None or (owner.host, owner.pid, owner.process_started) != (
            self._host, self._pid, self._process_started
        ):
            raise JournalOwnershipError("journal_owner_missing_or_changed")
        return owner

    @staticmethod
    def _configure_sqlite(connection, _record) -> None:
        cursor = connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=5000")
        finally:
            cursor.close()

    @staticmethod
    def _ensure_sqlite_column(connection, table: str, column: str, definition: str) -> None:
        existing = {row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _allocate_input_sequence_locked(
        self, session: Session, *, session_id: str, privacy_scope: str, now: datetime,
    ) -> int:
        # Allocate and publish the receive mark in the same serialized input transaction.
        key = (session_id, privacy_scope)
        row = session.get(ConversationInputCursor, key, with_for_update=True)
        if row is None:
            row = ConversationInputCursor(session_id=session_id, privacy_scope=privacy_scope)
        else:
            seq = row.latest_sequence
            if type(seq) is not int or seq < 0 or seq >= 2**63 - 1:
                raise RuntimeError("cursor state is invalid")
        row.latest_sequence += 1
        row.updated_at = now
        session.add(row)
        session.flush()
        return row.latest_sequence

    @staticmethod
    def _input_is_current_locked(
        session: Session, entry: ConversationJournalEntry | None, *, session_id: str,
    ) -> bool:
        # Pre-change null markers have unknown order, especially after old retention.
        if entry is None or type(entry.input_sequence) is not int or entry.input_sequence <= 0:
            return False
        cursor = session.get(ConversationInputCursor, (session_id, entry.privacy_scope), with_for_update=True)
        return (cursor is not None and type(cursor.latest_sequence) is int
                and entry.input_sequence == cursor.latest_sequence)

    def begin_exchange(
        self,
        *,
        session_id: str,
        text: str,
        speaker_name: str,
        input_source: str,
        privacy_scope: str,
    ) -> str:
        self._require_current_process()
        exchange_id = uuid4().hex
        with self._write_session() as session:
            owner = session.get(ConversationJournalOwner, self._owner_id)
            if owner is None:
                owner = ConversationJournalOwner(owner_id=self._owner_id, host=self._host,
                    pid=self._pid, process_started=self._process_started)
                session.add(owner)
            else:
                self._registered_owner(session)
            sequence = self._allocate_input_sequence_locked(
                session, session_id=session_id, privacy_scope=privacy_scope, now=_utc_now(),
            )
            session.add(
                ConversationJournalEntry(
                    input_sequence=sequence,
                    exchange_id=exchange_id,
                    session_id=session_id[:120],
                    role="user",
                    speaker_name=speaker_name[:120],
                    input_source=input_source[:80],
                    text=text[:16_000],
                    privacy_scope=privacy_scope,
                    completion_status="pending",
                    owner_id=self._owner_id,
                )
            )
        return exchange_id

    def require_owned_pending_exchange(
        self, exchange_id: str, *, session_id: str, privacy_scope: str,
    ) -> None:
        """Cooperative admission check. The read closes before any tool runs."""
        with Session(self._engine) as session:
            entries = list(session.exec(select(ConversationJournalEntry.id).where(
                ConversationJournalEntry.exchange_id == exchange_id,
                ConversationJournalEntry.role == "user",
                ConversationJournalEntry.owner_id == self._owner_id,
                ConversationJournalEntry.completion_status == "pending",
                ConversationJournalEntry.session_id == session_id[:120],
                ConversationJournalEntry.privacy_scope == privacy_scope,
            ).limit(2)))
        if len(entries) != 1:
            raise JournalOwnershipError("journal_exchange_not_pending_or_not_owned")

    def complete_exchange(
        self,
        *,
        exchange_id: str,
        session_id: str,
        assistant_text: str,
        trace_id: str | None,
        output_target: str,
        privacy_scope: str,
        internal_summary: str | None = None,
        tool_metadata: dict | None = None,
        state_updates: dict | None = None,
    ) -> None:
        self._require_current_process()
        now = _utc_now()
        with self._write_session() as session:
            self._registered_owner(session)
            changed = session.exec(
                update(ConversationJournalEntry).where(
                    ConversationJournalEntry.exchange_id == exchange_id,
                    ConversationJournalEntry.role == "user",
                    ConversationJournalEntry.owner_id == self._owner_id,
                    ConversationJournalEntry.completion_status == "pending",
                    ConversationJournalEntry.session_id == session_id[:120],
                    ConversationJournalEntry.privacy_scope == privacy_scope,
                ).values(completion_status="completed").execution_options(synchronize_session=False)
            )
            if changed.rowcount != 1:
                raise JournalOwnershipError("journal_exchange_not_pending_or_not_owned")
            user_entry = session.exec(
                select(ConversationJournalEntry).where(
                    ConversationJournalEntry.exchange_id == exchange_id,
                    ConversationJournalEntry.role == "user",
                )
            ).first()
            assistant = None
            if assistant_text.strip():
                assistant = ConversationJournalEntry(
                    exchange_id=exchange_id,
                    session_id=session_id[:120],
                    role="assistant",
                    speaker_name="Shana",
                    input_source="assistant",
                    text=assistant_text[:16_000],
                    trace_id=trace_id,
                    output_target=output_target,
                    privacy_scope=privacy_scope,
                    completion_status="completed",
                    tool_metadata_json=json.dumps(tool_metadata or {}, ensure_ascii=False)[:8000],
                    occurred_at=now,
                    owner_id=self._owner_id,
                    input_sequence=user_entry.input_sequence if user_entry is not None else None,
                )
                session.add(assistant)
            session.flush()
            if self._input_is_current_locked(session, user_entry, session_id=session_id):
                self._update_working_state_locked(
                    session,
                    session_id=session_id,
                    user_text=user_entry.text if user_entry is not None else "",
                    assistant_text=assistant_text,
                    internal_summary=internal_summary,
                    state_updates=state_updates or {},
                    now=now,
                    privacy_scope=privacy_scope,
                )
            self._maybe_update_summary_locked(
                session,
                session_id=session_id,
                through_entry_id=assistant.id if assistant is not None else (user_entry.id if user_entry is not None else None),
                privacy_scope=privacy_scope,
            )
        # Retention is post-commit maintenance, not a second terminal transaction.
        try:
            self.prune()
        except Exception as exc:
            logging.getLogger(__name__).warning("Journal retention deferred (%s)", type(exc).__name__)

    def fail_exchange(self, exchange_id: str, *, status: str = "failed") -> bool:
        if status not in {"failed", "cancelled", "interrupted"}:
            raise ValueError("Invalid failed exchange status")
        self._require_current_process()
        with self._write_session() as session:
            owner = session.get(ConversationJournalOwner, self._owner_id)
            if owner is None:
                return False
            self._registered_owner(session)
            changed = session.exec(update(ConversationJournalEntry).where(
                ConversationJournalEntry.exchange_id == exchange_id,
                ConversationJournalEntry.role == "user",
                ConversationJournalEntry.owner_id == self._owner_id,
                ConversationJournalEntry.completion_status == "pending",
            ).values(completion_status=status).execution_options(synchronize_session=False))
            return changed.rowcount == 1

    def reconcile_interrupted(self, *, limit: int = 100) -> int:
        """Recover a bounded, fair batch of verifiably dead owners' pending rows."""
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise ValueError("Invalid journal recovery batch size")
        count = 0
        with self._write_session() as session:
            entries = list(session.exec(select(ConversationJournalEntry).join(
                ConversationJournalOwner,
                ConversationJournalEntry.owner_id == ConversationJournalOwner.owner_id,
            ).where(
                ConversationJournalEntry.role == "user",
                ConversationJournalEntry.completion_status == "pending",
            ).order_by(ConversationJournalOwner.checked_at, ConversationJournalEntry.id).limit(limit)))
            checked = {}
            now = _utc_now()
            for entry in entries:
                if entry.owner_id not in checked:
                    owner = session.get(ConversationJournalOwner, entry.owner_id)
                    checked[entry.owner_id] = owner_is_dead(owner.host, owner.pid, owner.process_started)
                    owner.checked_at = now
                    session.add(owner)
                if checked[entry.owner_id]:
                    entry.completion_status = "interrupted"
                    session.add(entry)
                    count += 1
            session.flush()
            self._prune_owner_registry_locked(session, limit=limit)
        return count

    def mark_pending_interrupted(self, *, limit: int = 100) -> int:
        """Compatibility entry point; construction never invokes recovery."""
        return self.reconcile_interrupted(limit=limit)

    @staticmethod
    def _prune_owner_registry_locked(session: Session, *, limit: int = 100) -> None:
        pending_owner = select(ConversationJournalEntry.id).where(
            ConversationJournalEntry.owner_id == ConversationJournalOwner.owner_id,
            ConversationJournalEntry.completion_status == "pending",
        ).exists()
        orphans = list(session.exec(select(ConversationJournalOwner).where(
            ~pending_owner
        ).order_by(ConversationJournalOwner.checked_at).limit(limit)))
        for owner in orphans:
            session.delete(owner)

    def recent_turns(
        self,
        session_id: str,
        *,
        privacy_scopes: set[str],
        token_budget: int | None = None,
        limit: int = 24,
    ) -> list[ConversationJournalEntry]:
        budget = max(128, token_budget or settings.conversation_recent_turn_tokens)
        with Session(self._engine) as session:
            statement = (
                select(ConversationJournalEntry)
                .where(
                    ConversationJournalEntry.session_id == session_id,
                    ConversationJournalEntry.completion_status == "completed",
                    ConversationJournalEntry.privacy_scope.in_(privacy_scopes),
                )
                .order_by(ConversationJournalEntry.id.desc())
                .limit(max(1, limit))
            )
            newest = list(session.exec(statement))
        selected: list[ConversationJournalEntry] = []
        used = 0
        for entry in newest:
            cost = estimate_text_tokens(entry.text) + 8
            if selected and used + cost > budget:
                break
            if cost > budget:
                continue
            selected.append(entry)
            used += cost
        return list(reversed(selected))

    def prompt_context(self, session_id: str, *, privacy_scopes: set[str], token_budget: int | None = None) -> str:
        parts: list[str] = []
        for privacy_scope in sorted(privacy_scopes):
            snapshot = self.snapshot(session_id, privacy_scope=privacy_scope)
            summary = snapshot.get("summary")
            if isinstance(summary, dict) and summary.get("summary_text"):
                parts.append("# Rolling Session Summary\n" + str(summary["summary_text"]))
            working = snapshot.get("working_state")
            if isinstance(working, dict) and any(
                working.get(key) for key in ("active_topic", "current_objective", "next_intended_action")
            ):
                parts.append("# Active Working State\n" + json.dumps(working, ensure_ascii=False, sort_keys=True))
        turns = self.recent_turns(session_id, privacy_scopes=privacy_scopes, token_budget=token_budget)
        if turns:
            lines = [f"{entry.role}: {entry.text}" for entry in turns]
            parts.append("# Recent Conversation Turns\n" + "\n".join(lines))
        return "\n\n".join(parts)

    def snapshot(self, session_id: str, *, privacy_scope: str = "local_private") -> dict:
        """Select one exact scope; the compatibility default is a private-owner view.

        Public/unknown callers must explicitly select their authorized scope.
        Legacy scope labels cannot authorize public reads of old state.
        """
        with Session(self._engine) as session:
            summary = session.get(ScopedSessionSummary, (session_id, privacy_scope))
            working = session.get(ScopedWorkingStateCheckpoint, (session_id, privacy_scope))
            if privacy_scope == "local_private":
                if summary is None:
                    summary = session.get(RollingSessionSummary, session_id)
                if working is None:
                    working = session.get(WorkingStateCheckpoint, session_id)
        payload = {
            "session_id": session_id,
            "summary": self._summary_payload(summary),
            "working_state": self._working_payload(working),
        }
        # A legacy row may already be mislabeled public. Do not propagate that label.
        for key in ("summary", "working_state"):
            if payload[key] is not None:
                payload[key]["privacy_scope"] = privacy_scope
        return payload

    def record_output(self, *, target_policy: str, turn_id: str, text: str, status: str, spoken: bool) -> None:
        with Session(self._engine) as session:
            current = session.get(DurableOutputState, target_policy)
            if current is None:
                current = DurableOutputState(target_policy=target_policy, turn_id=turn_id, text=text, status=status)
            current.turn_id = turn_id
            current.text = text[:16_000]
            current.status = status
            current.spoken = spoken
            current.occurred_at = _utc_now()
            session.add(current)
            session.commit()

    def last_output(self, target_policy: str) -> dict | None:
        with Session(self._engine) as session:
            output = session.get(DurableOutputState, target_policy)
        if output is None:
            return None
        return {
            "target_policy": output.target_policy,
            "turn_id": output.turn_id,
            "text": output.text,
            "status": output.status,
            "spoken": output.spoken,
            "occurred_at": output.occurred_at.isoformat(),
        }

    def delete_session(self, session_id: str) -> int:
        with self._write_session() as session:
            count = int(
                session.exec(
                    select(func.count()).select_from(ConversationJournalEntry).where(
                        ConversationJournalEntry.session_id == session_id
                    )
                ).one()
            )
            session.exec(delete(ConversationJournalEntry).where(ConversationJournalEntry.session_id == session_id))
            summary = session.get(RollingSessionSummary, session_id)
            working = session.get(WorkingStateCheckpoint, session_id)
            if summary is not None:
                session.delete(summary)
            if working is not None:
                session.delete(working)
            for model in (ScopedSessionSummary, ScopedWorkingStateCheckpoint):
                session.exec(delete(model).where(model.session_id == session_id))
            # Keep a marker if inherited journal normalization left outstanding work.
            cursor_has_entries = select(ConversationJournalEntry.id).where(
                ConversationJournalEntry.session_id == func.substr(ConversationInputCursor.session_id, 1, 120),
                ConversationJournalEntry.privacy_scope == ConversationInputCursor.privacy_scope,
            ).exists()
            session.exec(delete(ConversationInputCursor).where(
                ConversationInputCursor.session_id == session_id, ~cursor_has_entries,
            ))
            session.flush()
            self._prune_owner_registry_locked(session)
        return count

    def prune(self) -> int:
        cutoff = _utc_now() - timedelta(days=max(1, settings.conversation_journal_retention_days))
        removed = 0
        with self._write_session() as session:
            expired = list(
                session.exec(select(ConversationJournalEntry).where(
                    ConversationJournalEntry.occurred_at < cutoff,
                    ConversationJournalEntry.completion_status != "pending",
                ))
            )
            for entry in expired:
                session.delete(entry)
            removed += len(expired)
            scope_keys = list(session.exec(select(
                ConversationJournalEntry.session_id, ConversationJournalEntry.privacy_scope
            ).distinct()))
            max_exchanges = max(1, settings.conversation_journal_max_exchanges_per_session)
            for session_id, privacy_scope in scope_keys:
                exchange_ids = list(
                    session.exec(
                        select(ConversationJournalEntry.exchange_id)
                        .where(
                            ConversationJournalEntry.session_id == session_id,
                            ConversationJournalEntry.privacy_scope == privacy_scope,
                            ConversationJournalEntry.role == "user",
                            ConversationJournalEntry.completion_status == "completed",
                        )
                        .order_by(ConversationJournalEntry.id.desc())
                    )
                )
                stale_ids = exchange_ids[max_exchanges:]
                if stale_ids:
                    stale_entries = list(
                        session.exec(
                            select(ConversationJournalEntry).where(
                                ConversationJournalEntry.exchange_id.in_(stale_ids)
                            )
                        )
                    )
                    for entry in stale_entries:
                        session.delete(entry)
                    removed += len(stale_entries)
            session.flush()
            # Keep state while that scope has any journal rows, including pending work.
            for model in (ScopedSessionSummary, ScopedWorkingStateCheckpoint):
                scope_has_entries = select(ConversationJournalEntry.id).where(
                    ConversationJournalEntry.session_id == model.session_id,
                    ConversationJournalEntry.privacy_scope == model.privacy_scope,
                ).exists()
                session.exec(delete(model).where(model.updated_at < cutoff, ~scope_has_entries))
            # Reset ordering only after no old exchange or retained checkpoint can use it.
            cursor_has_entries = select(ConversationJournalEntry.id).where(
                ConversationJournalEntry.session_id == func.substr(ConversationInputCursor.session_id, 1, 120),
                ConversationJournalEntry.privacy_scope == ConversationInputCursor.privacy_scope,
            ).exists()
            cursor_has_state = select(ScopedWorkingStateCheckpoint.session_id).where(
                ScopedWorkingStateCheckpoint.session_id == ConversationInputCursor.session_id,
                ScopedWorkingStateCheckpoint.privacy_scope == ConversationInputCursor.privacy_scope,
            ).exists()
            session.exec(delete(ConversationInputCursor).where(
                ConversationInputCursor.updated_at < cutoff, ~cursor_has_entries, ~cursor_has_state,
            ))
            # Legacy state is private compatibility data regardless of its old label.
            # Pending work keeps it available until explicit deletion or a later prune.
            for model in (RollingSessionSummary, WorkingStateCheckpoint):
                session_has_pending = select(ConversationJournalEntry.id).where(
                    ConversationJournalEntry.session_id == model.session_id,
                    ConversationJournalEntry.completion_status == "pending",
                ).exists()
                session.exec(delete(model).where(model.updated_at < cutoff, ~session_has_pending))
            self._prune_owner_registry_locked(session)
        return removed

    def _maybe_update_summary_locked(
        self,
        session: Session,
        *,
        session_id: str,
        through_entry_id: int | None,
        privacy_scope: str,
    ) -> None:
        summary = session.get(ScopedSessionSummary, (session_id, privacy_scope))
        new_summary = summary is None
        if summary is None:
            summary = ScopedSessionSummary(session_id=session_id, privacy_scope=privacy_scope)
            completed = int(session.exec(
                select(func.count()).select_from(ConversationJournalEntry).where(
                    ConversationJournalEntry.session_id == session_id,
                    ConversationJournalEntry.privacy_scope == privacy_scope,
                    ConversationJournalEntry.role == "user",
                    ConversationJournalEntry.completion_status == "completed",
                )
            ).one())
        else:
            # Count accepted completions in this scope even after old turns expire.
            completed = summary.completed_exchanges + 1
        summary.completed_exchanges = completed
        interval = max(1, settings.conversation_summary_interval_exchanges)
        if new_summary or completed % interval == 0:
            recent = list(
                session.exec(
                    select(ConversationJournalEntry)
                    .where(
                        ConversationJournalEntry.session_id == session_id,
                        ConversationJournalEntry.completion_status == "completed",
                        ConversationJournalEntry.privacy_scope == privacy_scope,
                    )
                    .order_by(ConversationJournalEntry.id.desc())
                    .limit(12)
                )
            )
            recent.reverse()
            summary.summary_text = "\n".join(f"{item.role}: {item.text[:500]}" for item in recent)[-5000:]
            # Coverage describes the selected rows, not the triggering completion.
            summary.through_entry_id = recent[-1].id if recent else None
        summary.updated_at = _utc_now()
        session.add(summary)

    def _update_working_state_locked(
        self,
        session: Session,
        *,
        session_id: str,
        user_text: str,
        assistant_text: str,
        internal_summary: str | None,
        state_updates: dict,
        now: datetime,
        privacy_scope: str,
    ) -> None:
        state = session.get(ScopedWorkingStateCheckpoint, (session_id, privacy_scope))
        if state is None:
            state = ScopedWorkingStateCheckpoint(session_id=session_id, privacy_scope=privacy_scope)
            if privacy_scope == "local_private":
                legacy = session.get(WorkingStateCheckpoint, session_id)
                if legacy is not None:
                    for field in ScopedWorkingStateCheckpoint.model_fields:
                        if field not in {"session_id", "privacy_scope"}:
                            setattr(state, field, getattr(legacy, field))
        state.active_topic = " ".join(str(state_updates.get("active_topic") or user_text).split())[:500]
        objective = state_updates.get("current_objective") or internal_summary
        if objective:
            state.current_objective = " ".join(str(objective).split())[:1000]
        state.pending_questions_json = json.dumps([user_text[:1000]] if user_text.rstrip().endswith("?") else [])
        state.last_meaningful_interaction = now
        state.last_assistant_action = " ".join(assistant_text.split())[:1000]
        state.next_intended_action = " ".join(
            str(state_updates.get("deferred_intention") or state.next_intended_action
                or "Respond to the next turn using the current topic and unresolved questions.").split()
        )[:1000]
        relationship_signals = state_updates.get("relationship_signals")
        if isinstance(relationship_signals, list):
            state.relevant_people_json = json.dumps([" ".join(str(item).split())[:200] for item in relationship_signals[:8]])
        state.updated_at = now
        session.add(state)

    @staticmethod
    def _summary_payload(summary: RollingSessionSummary | ScopedSessionSummary | None) -> dict | None:
        if summary is None:
            return None
        return {
            "summary_text": summary.summary_text,
            "through_entry_id": summary.through_entry_id,
            "completed_exchanges": summary.completed_exchanges,
            "updated_at": summary.updated_at.isoformat(),
            "privacy_scope": summary.privacy_scope,
        }

    @staticmethod
    def _working_payload(state: WorkingStateCheckpoint | ScopedWorkingStateCheckpoint | None) -> dict | None:
        if state is None:
            return None
        return {
            "active_topic": state.active_topic,
            "current_objective": state.current_objective,
            "pending_questions": json.loads(state.pending_questions_json or "[]"),
            "commitments": json.loads(state.commitments_json or "[]"),
            "relevant_people": json.loads(state.relevant_people_json or "[]"),
            "last_meaningful_interaction": state.last_meaningful_interaction.isoformat() if state.last_meaningful_interaction else None,
            "last_assistant_action": state.last_assistant_action,
            "next_intended_action": state.next_intended_action,
            "updated_at": state.updated_at.isoformat(),
            "session_id": state.session_id,
            "privacy_scope": state.privacy_scope,
        }
