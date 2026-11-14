from __future__ import annotations

import weakref
from datetime import datetime, timezone
from typing import Iterable

from sqlmodel import Session, SQLModel, create_engine, select
from sqlalchemy import event, or_
from sqlalchemy.pool import NullPool

from ..config import settings
from ..schemas.response import MemoryCandidate
from . import embeddings, retrieval, triage
from .models import EpisodicMemory, ProfileFact
from .sqlite_setup import enable_wal, ensure_sqlite_parent
from .attribution import MemoryOrigin, prepare_candidate, source_key, merge_origin
from .people import PeopleMixin
from .admin import MemoryAdminMixin
from .fact_text import _canonicalize_profile_text, _episodic_signature, _fact_key, _normalize_subject_name, _normalize_whitespace


MEMORY_CANDIDATES = 2000  # newest rows considered per search

# Where a memory came from decides who may later be shown it.
_VISIBILITY_BY_SCOPE = {"local_private": "owner_only", "public_stream": "stream", "public_discord": "discord"}


def visibility_for(origin: MemoryOrigin | None) -> str:
    if origin is None:
        return "owner_only"  # internal callers (tools, CLI) write on the owner's behalf
    return _VISIBILITY_BY_SCOPE.get(origin.privacy_scope, "unknown")


def _visible(model, allowed: set[str]):
    """SQL filter for an audience. NULL rows predate the column and count as owner_only."""
    column = model.visibility
    if "owner_only" in allowed:
        return or_(column.in_(allowed), column.is_(None))
    return column.in_(allowed)


class MemoryService(PeopleMixin, MemoryAdminMixin):
    """Persistent episodic/profile memory storage.
    
    Attributes:
        _engine: SQLAlchemy engine.
        _engine_finalizer: Weakref to engine disposal callback.
    
    Methods:
        __init__: Initialize memory service.
        close: Close memory engine.
        _configure_sqlite_connection: Configure SQLite connection.
        _ensure_compatible_schema: Ensure schema is compatible.
        _ensure_sqlite_columns: Add missing SQLite columns.
        get_profile_facts: Get profile facts.
        get_profile_facts_for_subject: Get profile facts for subject.
        get_profile_facts_for_slot: Get profile facts for slot.
        save_profile_fact: Save profile fact.
        save_personal_memory: Save episodic memory.
        clear_personal_memory: Clearepisodic memory.
        _memory_text_signature: Get memory signature.
        get_recent_episodic_memories: Get recent episodic memories.
        get_all_episodic_memories: Get all episodic memories.
        clear_episodic_memories: Clear episodic memories.
        resolve_person_identity: Resolve person identity.
    """

    def __init__(self) -> None:
        """Initialize MemoryService.
        
        Creates SQLite engine if configured. Configures SQLite connection
        to use WAL mode and synchronous=NORMAL for better performance.
        Creates all tables and ensures schema compatibility.
        """
        connect_args = {}
        engine_kwargs = {}
        if settings.database_url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
            engine_kwargs["poolclass"] = NullPool
        ensure_sqlite_parent(settings.database_url)
        self._engine = create_engine(settings.database_url, connect_args=connect_args, **engine_kwargs)
        if settings.database_url.startswith("sqlite"):
            event.listen(self._engine, "connect", self._configure_sqlite_connection)
        self._engine_finalizer = weakref.finalize(self, self._engine.dispose)
        SQLModel.metadata.create_all(self._engine)
        self._ensure_compatible_schema()

    def close(self) -> None:
        """Close memory engine.
        
        Disposes the SQLite engine if not already disposed.
        """
        if self._engine_finalizer.alive:
            self._engine_finalizer()

    @staticmethod
    def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
        """Configure SQLite connection.
        
        Args:
            dbapi_connection: SQLite connection.
            _connection_record: SQLAlchemy connection record.
        """
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA busy_timeout=5000")
            enable_wal(cursor)
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    def _ensure_compatible_schema(self) -> None:
        """Ensure schema is compatible.
        
        Adds required columns and indexes for SQLite databases.
        No-op for other database types.
        """
        if not settings.database_url.startswith("sqlite"):
            return
        with self._engine.begin() as conn:
            conn.exec_driver_sql("BEGIN IMMEDIATE")
            self._ensure_sqlite_columns(
                conn,
                "episodicmemory",
                {
                    "session_id": "TEXT",
                    "subject_type": "TEXT DEFAULT 'primary_user'",
                    "subject_name": "TEXT",
                    "relationship_to_user": "TEXT",
                    "attribution": "TEXT",
                    "visibility": "TEXT",
                    "created_at": "TEXT",
                },
            )
            self._ensure_sqlite_columns(
                conn,
                "profilefact",
                {
                    "importance": "FLOAT DEFAULT 0.5",
                    "subject_type": "TEXT DEFAULT 'primary_user'",
                    "subject_name": "TEXT",
                    "relationship_to_user": "TEXT",
                    "attribution": "TEXT",
                    "visibility": "TEXT",
                    "superseded_at": "TEXT",
                    "superseded_by": "INTEGER",
                    "created_at": "TEXT",
                },
            )
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_episodicmemory_session_id ON episodicmemory (session_id)")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_episodicmemory_subject_type ON episodicmemory (subject_type)")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_episodicmemory_subject_name ON episodicmemory (subject_name)")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_profilefact_subject_type ON profilefact (subject_type)")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_profilefact_subject_name ON profilefact (subject_name)")
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_profilefact_subject_created "
                "ON profilefact (subject_type, subject_name, created_at)"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_episodicmemory_session_created "
                "ON episodicmemory (session_id, created_at)"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_personidentity_platform_user "
                "ON personidentity (platform, platform_user_id)"
            )

    def _ensure_sqlite_columns(self, conn, table_name: str, columns_to_add: dict[str, str]) -> None:
        """Add missing SQLite columns.
        
        Args:
            conn: Database connection.
            table_name: Table name.
            columns_to_add: Column definitions to add.
        """
        result = conn.exec_driver_sql(f"PRAGMA table_info({table_name})")
        existing = {row[1] for row in result.fetchall()}
        for name, definition in columns_to_add.items():
            if name not in existing:
                conn.exec_driver_sql(f"ALTER TABLE {table_name} ADD COLUMN {name} {definition}")

    def get_profile_facts(
        self,
        limit: int | None = None,
        *,
        subject_type: str | None = "primary_user",
        subject_name: str | None = None,
    ) -> list[ProfileFact]:
        """Get profile facts.
        
        Args:
            limit: Optional max facts (default from settings.memory_top_k).
            subject_type: Subject type filter.
            subject_name: Subject name filter.
        
        Returns:
            list[ProfileFact]: Profile facts ranked by importance, then confidence.
        """
        with Session(self._engine) as session:
            statement = (select(ProfileFact).where(ProfileFact.superseded_at.is_(None))
                         .order_by(ProfileFact.importance.desc(), ProfileFact.confidence.desc(), ProfileFact.id.desc()))
            if subject_type:
                statement = statement.where(ProfileFact.subject_type == subject_type)
            if subject_name:
                statement = statement.where(ProfileFact.subject_name == _normalize_subject_name(subject_name))
            facts = list(session.exec(statement.limit(limit or settings.memory_top_k)))
        return facts

    def search_memories(
        self,
        query: str,
        session_id: str | None = None,
        limit: int | None = None,
        *,
        subject_type: str | None = None,
        subject_name: str | None = None,
        cross_session: bool = False,
        visibility: set[str] | None = None,
    ) -> list[EpisodicMemory]:
        """Episodes most relevant to ``query`` (see memory/retrieval.py).

        Without ``cross_session`` only ``session_id``'s episodes are searched;
        with it, all sessions are searched and the current one gets a small boost.
        An empty query returns the most important recent episodes.
        """
        limit = limit or settings.memory_top_k
        with Session(self._engine) as session:
            statement = select(EpisodicMemory)
            if session_id and not cross_session:
                statement = statement.where(EpisodicMemory.session_id == session_id)
            if subject_type:
                statement = statement.where(EpisodicMemory.subject_type == subject_type)
            if subject_name:
                statement = statement.where(EpisodicMemory.subject_name == _normalize_subject_name(subject_name))
            if visibility is not None:
                statement = statement.where(_visible(EpisodicMemory, visibility))
            candidates = list(session.exec(statement.order_by(EpisodicMemory.id.desc()).limit(MEMORY_CANDIDATES)))
        semantic = self._semantic(query, "episode", [(m.id, f"{m.summary} {m.tags}") for m in candidates])
        if not retrieval.terms(query) and semantic is None:
            candidates.sort(key=lambda m: (m.importance, m.id or 0), reverse=True)
            return candidates[:limit]
        ranked = retrieval.rank(
            query, candidates,
            text=lambda m: f"{m.summary} {m.tags}",
            importance=lambda m: m.importance,
            created_at=lambda m: m.created_at,
            session=lambda m: m.session_id,
            session_id=session_id,
            limit=limit,
            semantic=(lambda m: semantic.get(m.id)) if semantic is not None else None,
        )
        return [s.item for s in ranked]

    def get_relevant_facts(
        self,
        query: str | None,
        limit: int | None = None,
        *,
        subject_type: str | None = "primary_user",
        subject_name: str | None = None,
        pinned: int = 2,
        visibility: set[str] | None = None,
    ) -> list[ProfileFact]:
        """The most important fact(s), then the facts most relevant to ``query``."""
        limit = limit or settings.memory_top_k
        with Session(self._engine) as session:
            statement = select(ProfileFact).where(ProfileFact.superseded_at.is_(None))
            if subject_type:
                statement = statement.where(ProfileFact.subject_type == subject_type)
            if subject_name:
                statement = statement.where(ProfileFact.subject_name == _normalize_subject_name(subject_name))
            if visibility is not None:
                statement = statement.where(_visible(ProfileFact, visibility))
            facts = list(session.exec(statement.order_by(ProfileFact.id.desc()).limit(MEMORY_CANDIDATES)))
        by_importance = sorted(facts, key=lambda f: (f.importance, f.confidence, f.id or 0), reverse=True)
        semantic = self._semantic(query or "", "fact", [(f.id, f"{f.category} {f.fact_text}") for f in facts])
        ranked = retrieval.rank(
            query or "", facts,
            text=lambda f: f"{f.category} {f.fact_text} {f.relationship_to_user or ''}",
            importance=lambda f: f.importance,
            created_at=lambda f: f.created_at,
            limit=limit,
            semantic=(lambda f: semantic.get(f.id)) if semantic is not None else None,
        )
        if not ranked:
            return by_importance[:limit]
        return retrieval.merge_pinned(by_importance[:pinned], [s.item for s in ranked], key=lambda f: f.id, limit=limit)

    def previous_values(self, fact_ids: Iterable[int], *, limit: int = 2) -> dict[int, list[str]]:
        """Older texts each current fact replaced, newest first ("you used to...")."""
        wanted = [i for i in fact_ids if i]
        if not wanted:
            return {}
        with Session(self._engine) as session:
            history = list(session.exec(select(ProfileFact).where(ProfileFact.superseded_at.is_not(None))))
        by_successor: dict[int, list[ProfileFact]] = {}
        for fact in history:
            if fact.superseded_by:
                by_successor.setdefault(fact.superseded_by, []).append(fact)
        out: dict[int, list[str]] = {}
        for fact_id in wanted:
            texts, frontier, seen = [], [fact_id], {fact_id}
            while frontier and len(texts) < limit:
                older = sorted((f for n in frontier for f in by_successor.get(n, [])),
                               key=lambda f: f.superseded_at, reverse=True)
                frontier = [f.id for f in older if f.id not in seen]
                seen.update(frontier)
                texts.extend(f.fact_text for f in older)
            if texts:
                out[fact_id] = texts[:limit]
        return out

    def _with_history(self, session: Session, fact_ids: set[int]) -> set[int]:
        """The facts plus every older fact they replaced, so forgetting one forgets its past too."""
        ids, frontier = set(fact_ids), set(fact_ids)
        while frontier:
            older = {f.id for f in session.exec(select(ProfileFact).where(ProfileFact.superseded_by.in_(frontier)))}
            frontier = older - ids
            ids |= older
        return ids

    def persist_candidates(self, candidates: Iterable[MemoryCandidate], session_id: str | None = None, *, origin: MemoryOrigin | None = None) -> int:
        """Persist memory candidates.
        
        Args:
            candidates: Iterable of MemoryCandidate objects.
            session_id: Optional session ID for episodic memories.
        
        Returns:
            int: Number of items saved.
        """
        if not settings.memory_enabled or settings.memory_write_mode == "off":
            return 0
        if origin is not None and type(origin) is not MemoryOrigin:
            raise TypeError("Memory origin must be internal ingress metadata")
        if origin is not None and not origin.write_allowed:
            return 0
        saved = 0
        with Session(self._engine) as session:
            for candidate in candidates:
                if not self._worth_keeping(candidate, origin):
                    continue
                candidate, attribution = prepare_candidate(candidate, origin)
                visibility = visibility_for(origin)
                if candidate.type in {"profile", "fact", "preference", "project", "boundary"}:
                    saved += self._upsert_profile_fact(session, candidate, attribution=attribution, visibility=visibility)
                elif candidate.type == "episodic":
                    saved += self._upsert_episodic_memory(session, candidate, session_id=session_id,
                                                          attribution=attribution, visibility=visibility)
                session.flush()
            session.commit()  # Exact repeats may update rank/provenance while saved stays zero.
        return saved

    _judge = None

    def _semantic(self, query: str, kind: str, rows: list[tuple[int, str]]) -> dict[int, float] | None:
        """Cosine similarity per row id, or None when no embedding model is installed."""
        if not query.strip() or not rows:
            return None
        embedder = embeddings.get_embedder()
        if embedder is None:
            return None
        try:
            return embeddings.similarities(self._engine, embedder, query, kind, rows)
        except Exception:
            return None  # meaning search is a bonus; keyword search still works

    def _worth_keeping(self, candidate: MemoryCandidate, origin: MemoryOrigin | None) -> bool:
        """Triage automatic candidates (memory/triage.py); explicit and manual saves always pass."""
        if origin is None or origin.source in {"tool", "manual"}:
            return True
        if MemoryService._judge is None:
            MemoryService._judge = triage.KeepJudge()
            if settings.memory_triage_judge == "model":
                try:
                    from ..llm.factory import build_llm_adapter
                    MemoryService._judge = triage.ModelJudge(build_llm_adapter())
                except Exception:
                    pass
        try:
            return triage.triage(candidate.text, MemoryService._judge).keep
        except Exception:
            return True  # a broken judge must not lose memories

    def _upsert_profile_fact(self, session: Session, candidate: MemoryCandidate, *, attribution: str | None = None,
                             visibility: str = "owner_only") -> int:
        normalized_text = _canonicalize_profile_text(candidate.text)
        category = candidate.tags[0] if candidate.tags else "general"
        confidence = 0.0  # Selection strength is not evidence of truth.
        importance = max(0.1, min(1.0, candidate.importance))
        subject_type = candidate.subject_type or "primary_user"
        subject_name = _normalize_subject_name(candidate.subject_name)
        relationship_to_user = _normalize_subject_name(candidate.relationship_to_user)

        exact_statement = select(ProfileFact).where(
            ProfileFact.category == category,
            ProfileFact.fact_text == normalized_text,
            ProfileFact.subject_type == subject_type,
        )
        exact_statement = exact_statement.where(ProfileFact.superseded_at.is_(None))
        if subject_name:
            exact_statement = exact_statement.where(ProfileFact.subject_name == subject_name)
        else:
            exact_statement = exact_statement.where(ProfileFact.subject_name.is_(None))
        existing_exact = next((fact for fact in session.exec(exact_statement)
                               if source_key(fact.attribution) == source_key(attribution)), None)
        if existing_exact:
            existing_exact.importance = max(existing_exact.importance, importance)
            existing_exact.attribution = merge_origin(existing_exact.attribution, attribution)
            existing_exact.relationship_to_user = relationship_to_user or existing_exact.relationship_to_user
            session.add(existing_exact)
            return 0

        replaced: list[ProfileFact] = []
        key = _fact_key(category, normalized_text)
        if key:
            current = select(ProfileFact).where(
                ProfileFact.subject_type == subject_type, ProfileFact.superseded_at.is_(None))
            if subject_name:
                current = current.where(ProfileFact.subject_name == subject_name)
            else:
                current = current.where(ProfileFact.subject_name.is_(None))
            replaced = [fact for fact in session.exec(current)
                        if source_key(fact.attribution) == source_key(attribution)
                        and _fact_key(fact.category, fact.fact_text) == key]

        session.add(
            ProfileFact(
                category=category,
                fact_text=normalized_text,
                confidence=confidence,
                importance=importance,
                attribution=attribution,
                visibility=visibility,
                subject_type=subject_type,
                subject_name=subject_name,
                relationship_to_user=relationship_to_user,
            )
        )
        if replaced:
            session.flush()
            newest = session.exec(select(ProfileFact).where(ProfileFact.fact_text == normalized_text,
                                                            ProfileFact.superseded_at.is_(None))
                                  .order_by(ProfileFact.id.desc())).first()
            now = datetime.now(timezone.utc)
            for fact in replaced:
                fact.superseded_at, fact.superseded_by = now, newest.id if newest else None
                fact.relationship_to_user = fact.relationship_to_user or relationship_to_user
                session.add(fact)
        return 1

    def _upsert_episodic_memory(self, session: Session, candidate: MemoryCandidate, session_id: str | None = None, *,
                                attribution: str | None = None, visibility: str = "owner_only") -> int:
        normalized_summary = _normalize_whitespace(candidate.text.strip())
        normalized_tags = ",".join(candidate.tags)
        subject_type = candidate.subject_type or "primary_user"
        subject_name = _normalize_subject_name(candidate.subject_name)
        relationship_to_user = _normalize_subject_name(candidate.relationship_to_user)
        statement = select(EpisodicMemory).where(
            EpisodicMemory.summary == normalized_summary,
            EpisodicMemory.subject_type == subject_type,
        )
        if session_id:
            statement = statement.where(EpisodicMemory.session_id == session_id)
        else:
            statement = statement.where(EpisodicMemory.session_id.is_(None))
        if subject_name:
            statement = statement.where(EpisodicMemory.subject_name == subject_name)
        else:
            statement = statement.where(EpisodicMemory.subject_name.is_(None))
        existing = next((memory for memory in session.exec(statement)
                         if source_key(memory.attribution) == source_key(attribution)), None)
        importance = max(0.1, min(1.0, candidate.importance))
        if existing:
            existing.importance = max(existing.importance, importance)
            existing.attribution = merge_origin(existing.attribution, attribution)
            if normalized_tags:
                merged_tags = sorted({tag for tag in (existing.tags.split(",") + candidate.tags) if tag})
                existing.tags = ",".join(merged_tags)
            existing.relationship_to_user = relationship_to_user or existing.relationship_to_user
            session.add(existing)
            return 0

        scope_statement = select(EpisodicMemory).where(EpisodicMemory.subject_type == subject_type)
        if session_id:
            scope_statement = scope_statement.where(EpisodicMemory.session_id == session_id)
        else:
            scope_statement = scope_statement.where(EpisodicMemory.session_id.is_(None))
        if subject_name:
            scope_statement = scope_statement.where(EpisodicMemory.subject_name == subject_name)
        else:
            scope_statement = scope_statement.where(EpisodicMemory.subject_name.is_(None))
        signature = _episodic_signature(normalized_summary)
        for memory in list(session.exec(scope_statement.order_by(EpisodicMemory.id.desc()))):
            if source_key(memory.attribution) != source_key(attribution):
                continue
            if _episodic_signature(memory.summary) != signature:
                continue
            memory.importance = max(memory.importance, importance)
            memory.attribution = merge_origin(memory.attribution, attribution)
            if normalized_tags:
                merged_tags = sorted({tag for tag in (memory.tags.split(",") + candidate.tags) if tag})
                memory.tags = ",".join(merged_tags)
            memory.relationship_to_user = relationship_to_user or memory.relationship_to_user
            session.add(memory)
            return 0

        session.add(
            EpisodicMemory(
                session_id=session_id,
                summary=normalized_summary,
                attribution=attribution,
                visibility=visibility,
                importance=importance,
                tags=normalized_tags,
                subject_type=subject_type,
                subject_name=subject_name,
                relationship_to_user=relationship_to_user,
            )
        )
        return 1

