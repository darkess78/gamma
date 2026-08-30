from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import event, func
from sqlalchemy.pool import NullPool
from sqlmodel import Field, Session, SQLModel, create_engine, delete, select

from ..config import settings
from ..llm.capabilities import estimate_text_tokens


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
        if url.startswith("sqlite"):
            event.listen(self._engine, "connect", self._configure_sqlite)
        SQLModel.metadata.create_all(self._engine)
        if url.startswith("sqlite"):
            with self._engine.begin() as connection:
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
        self.mark_pending_interrupted()

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

    def begin_exchange(
        self,
        *,
        session_id: str,
        text: str,
        speaker_name: str,
        input_source: str,
        privacy_scope: str,
    ) -> str:
        exchange_id = uuid4().hex
        with Session(self._engine) as session:
            session.add(
                ConversationJournalEntry(
                    exchange_id=exchange_id,
                    session_id=session_id[:120],
                    role="user",
                    speaker_name=speaker_name[:120],
                    input_source=input_source[:80],
                    text=text[:16_000],
                    privacy_scope=privacy_scope,
                    completion_status="pending",
                )
            )
            session.commit()
        return exchange_id

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
        now = _utc_now()
        with Session(self._engine) as session:
            user_entry = session.exec(
                select(ConversationJournalEntry).where(
                    ConversationJournalEntry.exchange_id == exchange_id,
                    ConversationJournalEntry.role == "user",
                )
            ).first()
            if user_entry is not None:
                user_entry.completion_status = "completed"
                session.add(user_entry)
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
                )
                session.add(assistant)
            session.commit()
            if assistant is not None:
                session.refresh(assistant)
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
            session.commit()
        self.prune()

    def fail_exchange(self, exchange_id: str, *, status: str = "failed") -> None:
        with Session(self._engine) as session:
            entries = list(
                session.exec(select(ConversationJournalEntry).where(ConversationJournalEntry.exchange_id == exchange_id))
            )
            for entry in entries:
                if entry.completion_status == "pending":
                    entry.completion_status = status
                    session.add(entry)
            session.commit()

    def mark_pending_interrupted(self) -> int:
        with Session(self._engine) as session:
            entries = list(
                session.exec(
                    select(ConversationJournalEntry).where(ConversationJournalEntry.completion_status == "pending")
                )
            )
            for entry in entries:
                entry.completion_status = "interrupted"
                session.add(entry)
            session.commit()
        return len(entries)

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
        snapshot = self.snapshot(session_id)
        parts: list[str] = []
        summary = snapshot.get("summary")
        if (
            isinstance(summary, dict)
            and summary.get("summary_text")
            and summary.get("privacy_scope") in privacy_scopes
        ):
            parts.append("# Rolling Session Summary\n" + str(summary["summary_text"]))
        working = snapshot.get("working_state")
        if (
            isinstance(working, dict)
            and working.get("privacy_scope") in privacy_scopes
            and any(working.get(key) for key in ("active_topic", "current_objective", "next_intended_action"))
        ):
            parts.append("# Active Working State\n" + json.dumps(working, ensure_ascii=False, sort_keys=True))
        turns = self.recent_turns(session_id, privacy_scopes=privacy_scopes, token_budget=token_budget)
        if turns:
            lines = [f"{entry.role}: {entry.text}" for entry in turns]
            parts.append("# Recent Conversation Turns\n" + "\n".join(lines))
        return "\n\n".join(parts)

    def snapshot(self, session_id: str) -> dict:
        with Session(self._engine) as session:
            summary = session.get(RollingSessionSummary, session_id)
            working = session.get(WorkingStateCheckpoint, session_id)
        return {
            "session_id": session_id,
            "summary": self._summary_payload(summary),
            "working_state": self._working_payload(working),
        }

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
        with Session(self._engine) as session:
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
            session.commit()
        return count

    def prune(self) -> int:
        cutoff = _utc_now() - timedelta(days=max(1, settings.conversation_journal_retention_days))
        removed = 0
        with Session(self._engine) as session:
            expired = list(
                session.exec(select(ConversationJournalEntry).where(ConversationJournalEntry.occurred_at < cutoff))
            )
            for entry in expired:
                session.delete(entry)
            removed += len(expired)
            session_ids = list(session.exec(select(ConversationJournalEntry.session_id).distinct()))
            max_exchanges = max(1, settings.conversation_journal_max_exchanges_per_session)
            for session_id in session_ids:
                exchange_ids = list(
                    session.exec(
                        select(ConversationJournalEntry.exchange_id)
                        .where(
                            ConversationJournalEntry.session_id == session_id,
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
            session.commit()
        return removed

    def _maybe_update_summary_locked(
        self,
        session: Session,
        *,
        session_id: str,
        through_entry_id: int | None,
        privacy_scope: str,
    ) -> None:
        summary = session.get(RollingSessionSummary, session_id)
        if summary is None:
            summary = RollingSessionSummary(session_id=session_id)
        completed = int(
            session.exec(
                select(func.count()).select_from(ConversationJournalEntry).where(
                    ConversationJournalEntry.session_id == session_id,
                    ConversationJournalEntry.role == "user",
                    ConversationJournalEntry.completion_status == "completed",
                )
            ).one()
        )
        summary.completed_exchanges = completed
        summary.privacy_scope = privacy_scope
        interval = max(1, settings.conversation_summary_interval_exchanges)
        if completed == 1 or completed % interval == 0:
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
            summary.through_entry_id = through_entry_id
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
        state = session.get(WorkingStateCheckpoint, session_id)
        if state is None:
            state = WorkingStateCheckpoint(session_id=session_id)
        state.active_topic = " ".join(str(state_updates.get("active_topic") or user_text).split())[:500]
        objective = state_updates.get("current_objective") or internal_summary
        if objective:
            state.current_objective = " ".join(str(objective).split())[:1000]
        state.pending_questions_json = json.dumps([user_text[:1000]] if user_text.rstrip().endswith("?") else [])
        state.last_meaningful_interaction = now
        state.last_assistant_action = " ".join(assistant_text.split())[:1000]
        state.next_intended_action = " ".join(
            str(state_updates.get("deferred_intention") or "Respond to the next turn using the current topic and unresolved questions.").split()
        )[:1000]
        relationship_signals = state_updates.get("relationship_signals")
        if isinstance(relationship_signals, list):
            state.relevant_people_json = json.dumps([" ".join(str(item).split())[:200] for item in relationship_signals[:8]])
        state.privacy_scope = privacy_scope
        state.updated_at = now
        session.add(state)

    @staticmethod
    def _summary_payload(summary: RollingSessionSummary | None) -> dict | None:
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
    def _working_payload(state: WorkingStateCheckpoint | None) -> dict | None:
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
