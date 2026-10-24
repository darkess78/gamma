from __future__ import annotations

import json
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import event, func, text as sql_text
from sqlalchemy.pool import NullPool
from sqlmodel import Field, Session, SQLModel, create_engine, delete, select

from ..config import settings
from ..llm.capabilities import estimate_text_tokens
from .state_authority import (StateAuthority, StateConflict, exchanges, generations, outbox, versions,
                              publications, decision_commits, decision_effects)
from .service import MemoryService
from ..schemas.response import MemoryCandidate
from ..persona.assistant_state import AssistantStateStore
from ..persona.emotion_service import EmotionMemoryService
from .core import CoreMemoryStore
from .delivery_history import history_line


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

    def __init__(self, *, database_url: str | None = None, generation_id: str | None = None) -> None:
        url = database_url or settings.database_url
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        engine_kwargs = {"poolclass": NullPool} if url.startswith("sqlite") else {}
        self._engine = create_engine(url, connect_args=connect_args, **engine_kwargs)
        if url.startswith("sqlite"):
            event.listen(self._engine, "connect", self._configure_sqlite)
        self._authority = StateAuthority(self._engine, generation_id=generation_id)
        SQLModel.metadata.create_all(self._engine)
        self._memory_writer = MemoryService(database_url=url)
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

    @property
    def generation_id(self) -> str:
        return self._authority.generation_id

    @contextmanager
    def _write_session(self):
        with self._authority.transaction() as session:
            yield session

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
        input_role: str = "user",
        parent_exchange_id: str | None = None,
        _session=None,
    ) -> str:
        if input_role not in {"user", "event"}:
            raise ValueError("invalid decision input role")
        exchange_id = uuid4().hex
        session_id = session_id[:120]
        with self._write_session() if _session is None else nullcontext(_session) as session:
            StateAuthority.require_transaction(session)
            if parent_exchange_id is not None:
                parent = self._authority.owner(session, parent_exchange_id)
                if (parent is None or parent["generation_id"] != self.generation_id or
                        parent["status"] != "pending" or
                        parent["base_version"] != self._authority.version(session, parent["session_id"])):
                    raise StateConflict("parent_decision_stale_or_not_owned")
            self._authority.begin(session, exchange_id, session_id, privacy_scope)
            session.add(
                ConversationJournalEntry(
                    exchange_id=exchange_id,
                    session_id=session_id[:120],
                    role=input_role,
                    speaker_name=speaker_name[:120],
                    input_source=input_source[:80],
                    text=text[:16_000],
                    privacy_scope=privacy_scope,
                    completion_status="pending",
                )
            )
            if _session is None:
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
        memory_candidates: list[MemoryCandidate] | None = None,
        persona_emotion: str | None = None,
        core_facts: list[str] | None = None,
        approved_internal_goal: dict | None = None,
        internal_result: dict | None = None,
    ) -> None:
        now = _utc_now()
        session_id = session_id[:120]
        with self._write_session() as session:
            if approved_internal_goal is not None:
                goal = session.execute(sql_text(
                    "SELECT id, title, description, updated_at, status FROM stream_self_goals WHERE id=:id"),
                    {"id": approved_internal_goal["id"]}).mappings().first()
                if (goal is None or goal["status"] != "approved" or any(
                        goal[key] != approved_internal_goal[key] for key in ("id", "title", "description", "updated_at"))):
                    raise StateConflict("silent_goal_approval_changed")
            self._authority.complete(session, exchange_id, session_id, privacy_scope,
                                     {"display_text": assistant_text[:16_000], "output_target": output_target,
                                      "trace_id": trace_id, "tool_metadata": tool_metadata or {},
                                      **({"internal_result": internal_result} if internal_result is not None else {})})
            if memory_candidates or core_facts or (persona_emotion is not None and settings.assistant_state_enabled):
                # These stores are shared across sessions. Other pending decisions
                # must not commit against a snapshot preceding this shared update.
                session.execute(versions.update().where(versions.c.session_id != session_id)
                                .values(version=versions.c.version + 1))
            if memory_candidates:
                self._memory_writer.persist_candidates_in_session(session, memory_candidates, session_id=session_id)
            for fact in core_facts or []:
                CoreMemoryStore().append(fact, session=session)
            user_entry = session.exec(
                select(ConversationJournalEntry).where(
                    ConversationJournalEntry.exchange_id == exchange_id,
                    ConversationJournalEntry.role.in_(["user", "event"]),
                )
            ).first()
            if user_entry is None:
                raise StateConflict("exchange_input_missing")
            if persona_emotion is not None and settings.assistant_state_enabled:
                AssistantStateStore().update(emotion=persona_emotion, user_text=user_entry.text,
                                             reply_text=assistant_text, session=session)
                EmotionMemoryService().update_from_turn(emotion=persona_emotion, user_text=user_entry.text,
                                                         reply_text=assistant_text, session_id=session_id, session=session)
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
                    tool_metadata_json=json.dumps(tool_metadata or {}, ensure_ascii=False),
                    occurred_at=now,
                )
                session.add(assistant)
            session.flush()
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
        if status not in {"failed", "interrupted", "cancelled"}:
            raise ValueError("invalid exchange failure status")
        with self._write_session() as session:
            if not self._authority.fail(session, exchange_id, status):
                return
            entries = list(
                session.exec(select(ConversationJournalEntry).where(ConversationJournalEntry.exchange_id == exchange_id))
            )
            for entry in entries:
                if entry.completion_status == "pending":
                    entry.completion_status = status
                    session.add(entry)
            session.commit()

    def retire_generation(self) -> None:
        with self._write_session() as session:
            self._authority.retire(session)
            session.commit()

    def mark_pending_interrupted(self, *, generation_id: str) -> int:
        """Explicitly reconcile only a retired or provably dead expired generation."""
        with self._write_session() as session:
            owned = self._authority.recover(session, generation_id)
            entries = list(
                session.exec(
                    select(ConversationJournalEntry).where(ConversationJournalEntry.completion_status == "pending",
                                                           ConversationJournalEntry.exchange_id.in_(owned))
                )
            )
            for entry in entries:
                entry.completion_status = "interrupted"
                session.add(entry)
            session.commit()
        return len(entries)

    def reconcile_expired_generations(self, *, limit: int = 100) -> int:
        """Called by the core lifecycle, never by a constructor or request reader."""
        import time
        with Session(self._engine) as session:
            candidates = list(session.execute(select(generations.c.id).where(
                generations.c.status.in_(("active", "retired")), generations.c.host == self._authority.host,
                generations.c.lease_until < time.time())
                .order_by(generations.c.lease_until).limit(max(1, min(limit, 100)))).scalars())
        return sum(self.mark_pending_interrupted(generation_id=item) for item in candidates)

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
                    ((ConversationJournalEntry.completion_status == "completed")
                     | ((ConversationJournalEntry.role == "user")
                        & ConversationJournalEntry.completion_status.in_(("cancelled", "interrupted", "failed")))),
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
        turns = self.recent_turns(session_id, privacy_scopes=privacy_scopes, token_budget=token_budget)
        parts = []
        # Stored summaries/checkpoints remain the decision audit. Rebuild the
        # prompt view against current receipts so late outcomes cannot leave
        # unqualified generated speech in a cached summary or last-action field.
        with Session(self._engine) as session:
            summary = snapshot.get("summary")
            if summary and summary.get("privacy_scope") in privacy_scopes and summary.get("through_entry_id"):
                older = list(session.exec(select(ConversationJournalEntry).where(
                    ConversationJournalEntry.session_id == session_id,
                    ConversationJournalEntry.privacy_scope.in_(privacy_scopes),
                    ConversationJournalEntry.completion_status == "completed",
                    ConversationJournalEntry.id <= summary["through_entry_id"],
                ).order_by(ConversationJournalEntry.id.desc()).limit(12)))
                if older:
                    parts.append("# Rolling Session Summary\n" + "\n".join(
                        history_line(session, entry)[:400] for entry in reversed(older)))
            working = snapshot.get("working_state")
            if working and working.get("privacy_scope") in privacy_scopes:
                working = dict(working)
                last = session.exec(select(ConversationJournalEntry).where(
                    ConversationJournalEntry.session_id == session_id,
                    ConversationJournalEntry.role == "assistant",
                    ConversationJournalEntry.privacy_scope.in_(privacy_scopes),
                    ConversationJournalEntry.completion_status == "completed",
                ).order_by(ConversationJournalEntry.id.desc()).limit(1)).first()
                working["last_assistant_action"] = history_line(session, last)[:1000] if last else ""
                parts.append("# Internal Working State (intentions are not delivery evidence)\n"
                             + json.dumps(working, ensure_ascii=False, sort_keys=True))
            if turns:
                parts.append("# Recent Conversation Turns\n" + "\n".join(
                    history_line(session, entry) for entry in turns))
        return "\n\n".join(parts)

    def snapshot(self, session_id: str) -> dict:
        with Session(self._engine) as session:
            if self._engine.dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN")
            summary = session.get(RollingSessionSummary, session_id)
            working = session.get(WorkingStateCheckpoint, session_id)
            version = self._authority.version(session, session_id)
        return {
            "session_id": session_id,
            "state_version": version,
            "summary": self._summary_payload(summary),
            "working_state": self._working_payload(working),
        }

    def record_output(self, *, target_policy: str, turn_id: str, text: str, status: str, spoken: bool,
                      decision_id: str, grant_id: str) -> None:
        with self._write_session() as session:
            self._authority.validate_publication(session, decision_id, grant_id, target_policy, text)
            if spoken:
                raise StateConflict("playback_receipt_required")
            # Producer completion proves prepared output, never receiver playback.
            production_status = "ready" if status in {"completed", "speaking", "synthesized"} else status
            session.execute(publications.update().where(publications.c.grant_id == grant_id)
                            .values(turn_id=turn_id, status=production_status))
            session.commit()

    def last_output(self, target_policy: str) -> dict | None:
        with Session(self._engine) as session:
            record = session.execute(select(publications).where(publications.c.target == target_policy)
                .order_by(publications.c.sequence.desc()).limit(1)).mappings().first()
            if record is not None:
                return dict(target_policy=target_policy, turn_id=record["turn_id"], text=record["text"],
                    status=record["status"], spoken=bool(record["spoken"]),
                    occurred_at=datetime.fromtimestamp(record["occurred_at"], timezone.utc).isoformat())
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
            self._authority.invalidate(session, session_id)
            count = int(
                session.exec(
                    select(func.count()).select_from(ConversationJournalEntry).where(
                        ConversationJournalEntry.session_id == session_id
                    )
                ).one()
            )
            session.exec(delete(ConversationJournalEntry).where(ConversationJournalEntry.session_id == session_id))
            owned = select(exchanges.c.exchange_id).where(exchanges.c.session_id == session_id)
            session.execute(decision_effects.delete().where(decision_effects.c.exchange_id.in_(owned)))
            session.execute(decision_commits.delete().where(decision_commits.c.exchange_id.in_(owned)))
            session.execute(publications.delete().where(publications.c.session_id == session_id))
            session.execute(outbox.delete().where(outbox.c.session_id == session_id))
            session.execute(exchanges.delete().where(exchanges.c.session_id == session_id))
            # Keep the version tombstone so an old snapshot can never become current again.
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
            session_ids = list(session.exec(select(ConversationJournalEntry.session_id).distinct()))
            max_exchanges = max(1, settings.conversation_journal_max_exchanges_per_session)
            for session_id in session_ids:
                exchange_ids = list(
                    session.exec(
                        select(ConversationJournalEntry.exchange_id)
                        .where(
                            ConversationJournalEntry.session_id == session_id,
                            ConversationJournalEntry.role.in_(["user", "event"]),
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
            retained = select(ConversationJournalEntry.exchange_id)
            # Only undispatched decisions are disposable here. Future external
            # action receipts must have their own reconciliation/retention policy.
            session.execute(outbox.delete().where(
                outbox.c.exchange_id.not_in(retained),
                outbox.c.status.in_(["awaiting_authorization", "cancelled_generation"]),
            ))
            session.execute(exchanges.delete().where(
                exchanges.c.exchange_id.not_in(retained), exchanges.c.status != "pending",
                exchanges.c.exchange_id.not_in(select(outbox.c.exchange_id)),
            ))
            for table in (decision_effects, decision_commits):
                session.execute(table.delete().where(table.c.exchange_id.not_in(select(exchanges.c.exchange_id))))
            transient = select(versions.c.session_id).where(
                versions.c.session_id.like("transient:%"),
                versions.c.session_id.not_in(select(ConversationJournalEntry.session_id)),
                versions.c.session_id.not_in(select(exchanges.c.session_id)),
                versions.c.session_id.not_in(select(outbox.c.session_id)),
            )
            for model in (RollingSessionSummary, WorkingStateCheckpoint):
                session.exec(delete(model).where(model.session_id.in_(transient)))
            session.execute(versions.delete().where(versions.c.session_id.in_(transient)))
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
                    ConversationJournalEntry.role.in_(["user", "event"]),
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
