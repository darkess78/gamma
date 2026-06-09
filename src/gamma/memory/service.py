from __future__ import annotations

import re
import weakref
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from sqlmodel import Session, SQLModel, create_engine, delete, select
from sqlalchemy.pool import NullPool

from ..config import settings
from ..schemas.response import MemoryCandidate
from .models import EpisodicMemory, ProfileFact


def _sqlite_path_from_url(database_url: str) -> Path | None:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return None
    raw = database_url[len(prefix):]
    path = Path(raw)
    if not path.is_absolute():
        path = settings.project_root / raw
    return path


def _normalize_whitespace(text: str) -> str:
    return " ".join(text.strip().split())


def _canonicalize_profile_text(text: str) -> str:
    normalized = _normalize_whitespace(text)
    normalized = re.sub(r"[.!?]+$", "", normalized)
    return normalized.strip()


def _profile_slot(category: str, fact_text: str) -> str | None:
    lowered = fact_text.lower().strip()
    if category == "identity" and lowered.startswith("my name is "):
        return "identity:name"
    if category in {"preference", "user_preference"}:
        if lowered.startswith("i do not like ") or lowered.startswith("remember that i do not like "):
            return "preference:dislike"
        if lowered.startswith("i dislike ") or lowered.startswith("remember that i dislike "):
            return "preference:dislike"
        if lowered.startswith("i hate ") or lowered.startswith("remember that i hate "):
            return "preference:dislike"
        if lowered.startswith("my favorite "):
            return "preference:favorite"
        if lowered.startswith("i prefer ") or lowered.startswith("remember that i prefer "):
            return "preference:prefer"
        if lowered.startswith("i like ") or lowered.startswith("remember that i like "):
            return "preference:like"
    if category in {"project", "project_state"}:
        if lowered.startswith("i am working on ") or lowered.startswith("i'm working on "):
            return "project:active"
        if lowered.startswith("the main project is "):
            return "project:active"
    return None


def _memory_text_signature(text: str) -> str:
    normalized = _normalize_whitespace(text).lower()
    normalized = re.sub(r"[.!?]+$", "", normalized)
    return normalized.strip()


def _episodic_signature(text: str) -> str:
    normalized = _memory_text_signature(text)
    normalized = re.sub(r"\|\s*assistant replied:.*$", "", normalized)
    normalized = re.sub(r"^user said:\s*", "", normalized)
    normalized = re.sub(r"\b(user said|assistant replied)\b", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = normalized.strip()
    normalized = re.sub(r"[.!?]+$", "", normalized)
    return normalized.strip()


def _extract_preference_subject(slot: str | None, fact_text: str) -> str | None:
    if not slot or not slot.startswith("preference:"):
        return None
    lowered = _memory_text_signature(fact_text)
    prefixes = (
        "remember that i do not like ",
        "i do not like ",
        "remember that i dislike ",
        "i dislike ",
        "remember that i hate ",
        "i hate ",
        "my favorite ",
        "remember that i prefer ",
        "i prefer ",
        "remember that i like ",
        "i like ",
    )
    for prefix in prefixes:
        if lowered.startswith(prefix):
            return lowered[len(prefix):].strip() or None
    return None


def _normalize_subject_name(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.strip().split())
    return normalized[:120] if normalized else None


class MemoryService:
    def __init__(self) -> None:
        connect_args = {}
        engine_kwargs = {}
        if settings.database_url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
            # SQLite connections should close immediately after use; pooled file handles
            # trigger noisy ResourceWarnings in short-lived test/service instances.
            engine_kwargs["poolclass"] = NullPool
        self._engine = create_engine(settings.database_url, connect_args=connect_args, **engine_kwargs)
        self._engine_finalizer = weakref.finalize(self, self._engine.dispose)
        SQLModel.metadata.create_all(self._engine)
        self._ensure_compatible_schema()

    def close(self) -> None:
        if self._engine_finalizer.alive:
            self._engine_finalizer()

    def _ensure_compatible_schema(self) -> None:
        if not settings.database_url.startswith("sqlite"):
            return
        with self._engine.begin() as conn:
            self._ensure_sqlite_columns(
                conn,
                "episodicmemory",
                {
                    "session_id": "TEXT",
                    "subject_type": "TEXT DEFAULT 'primary_user'",
                    "subject_name": "TEXT",
                    "relationship_to_user": "TEXT",
                    "created_at": "TEXT",
                },
            )
            self._ensure_sqlite_columns(
                conn,
                "profilefact",
                {
                    "subject_type": "TEXT DEFAULT 'primary_user'",
                    "subject_name": "TEXT",
                    "relationship_to_user": "TEXT",
                    "created_at": "TEXT",
                },
            )
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_episodicmemory_session_id ON episodicmemory (session_id)")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_episodicmemory_subject_type ON episodicmemory (subject_type)")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_episodicmemory_subject_name ON episodicmemory (subject_name)")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_profilefact_subject_type ON profilefact (subject_type)")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_profilefact_subject_name ON profilefact (subject_name)")

    def _ensure_sqlite_columns(self, conn, table_name: str, columns_to_add: dict[str, str]) -> None:
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
        with Session(self._engine) as session:
            statement = select(ProfileFact).order_by(ProfileFact.confidence.desc(), ProfileFact.id.desc())
            if subject_type:
                statement = statement.where(ProfileFact.subject_type == subject_type)
            if subject_name:
                statement = statement.where(ProfileFact.subject_name == _normalize_subject_name(subject_name))
            facts = list(session.exec(statement))
        return facts[: limit or settings.memory_top_k]

    def search_memories(
        self,
        query: str,
        session_id: str | None = None,
        limit: int | None = None,
        *,
        subject_type: str | None = None,
        subject_name: str | None = None,
    ) -> list[EpisodicMemory]:
        terms = [term.strip().lower() for term in query.split() if len(term.strip()) >= 3]
        with Session(self._engine) as session:
            statement = select(EpisodicMemory)
            if session_id:
                statement = statement.where(EpisodicMemory.session_id == session_id)
            if subject_type:
                statement = statement.where(EpisodicMemory.subject_type == subject_type)
            if subject_name:
                statement = statement.where(EpisodicMemory.subject_name == _normalize_subject_name(subject_name))
            memories = list(session.exec(statement.order_by(EpisodicMemory.importance.desc(), EpisodicMemory.id.desc())))
        if not terms:
            return memories[: limit or settings.memory_top_k]

        scored: list[tuple[int, EpisodicMemory]] = []
        for memory in memories:
            haystack = f"{memory.summary} {memory.tags}".lower()
            score = sum(1 for term in terms if term in haystack)
            if score:
                scored.append((score, memory))
        scored.sort(key=lambda item: (item[0], item[1].importance, item[1].id or 0), reverse=True)
        return [memory for _score, memory in scored[: limit or settings.memory_top_k]]

    def persist_candidates(self, candidates: Iterable[MemoryCandidate], session_id: str | None = None) -> int:
        if not settings.memory_enabled or settings.memory_write_mode == "off":
            return 0
        saved = 0
        with Session(self._engine) as session:
            for candidate in candidates:
                if candidate.type in {"profile", "fact", "preference", "project", "boundary"}:
                    saved += self._upsert_profile_fact(session, candidate)
                elif candidate.type == "episodic":
                    saved += self._upsert_episodic_memory(session, candidate, session_id=session_id)
                session.flush()
            if saved:
                session.commit()
        return saved

    def _upsert_profile_fact(self, session: Session, candidate: MemoryCandidate) -> int:
        normalized_text = _canonicalize_profile_text(candidate.text)
        category = candidate.tags[0] if candidate.tags else "general"
        confidence = max(0.1, min(1.0, candidate.importance))
        subject_type = candidate.subject_type or "primary_user"
        subject_name = _normalize_subject_name(candidate.subject_name)
        relationship_to_user = _normalize_subject_name(candidate.relationship_to_user)

        exact_statement = select(ProfileFact).where(
            ProfileFact.category == category,
            ProfileFact.fact_text == normalized_text,
            ProfileFact.subject_type == subject_type,
        )
        if subject_name:
            exact_statement = exact_statement.where(ProfileFact.subject_name == subject_name)
        else:
            exact_statement = exact_statement.where(ProfileFact.subject_name.is_(None))
        existing_exact = session.exec(exact_statement).first()
        if existing_exact:
            existing_exact.confidence = max(existing_exact.confidence, confidence)
            existing_exact.relationship_to_user = relationship_to_user or existing_exact.relationship_to_user
            session.add(existing_exact)
            return 0

        slot = _profile_slot(category, normalized_text)
        preference_subject = _extract_preference_subject(slot, normalized_text)
        if preference_subject and slot in {"preference:like", "preference:dislike"}:
            opposite_slot = "preference:dislike" if slot == "preference:like" else "preference:like"
            opposite_statement = select(ProfileFact).where(
                ProfileFact.category.in_(["preference", "user_preference"]),
                ProfileFact.subject_type == subject_type,
            )
            if subject_name:
                opposite_statement = opposite_statement.where(ProfileFact.subject_name == subject_name)
            else:
                opposite_statement = opposite_statement.where(ProfileFact.subject_name.is_(None))
            for fact in list(session.exec(opposite_statement)):
                if _profile_slot(fact.category, fact.fact_text) != opposite_slot:
                    continue
                if _extract_preference_subject(opposite_slot, fact.fact_text) == preference_subject:
                    session.delete(fact)

        if slot:
            candidate_categories = [category]
            if category == "preference":
                candidate_categories.append("user_preference")
            elif category == "user_preference":
                candidate_categories.append("preference")
            slot_statement = select(ProfileFact).where(
                ProfileFact.category.in_(candidate_categories),
                ProfileFact.subject_type == subject_type,
            )
            if subject_name:
                slot_statement = slot_statement.where(ProfileFact.subject_name == subject_name)
            else:
                slot_statement = slot_statement.where(ProfileFact.subject_name.is_(None))
            existing_facts = list(session.exec(slot_statement))
            for fact in existing_facts:
                if _profile_slot(fact.category, fact.fact_text) == slot:
                    fact.fact_text = normalized_text
                    fact.confidence = confidence
                    fact.relationship_to_user = relationship_to_user or fact.relationship_to_user
                    session.add(fact)
                    return 0

        session.add(
            ProfileFact(
                category=category,
                fact_text=normalized_text,
                confidence=confidence,
                subject_type=subject_type,
                subject_name=subject_name,
                relationship_to_user=relationship_to_user,
            )
        )
        return 1

    def _upsert_episodic_memory(self, session: Session, candidate: MemoryCandidate, session_id: str | None = None) -> int:
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
        existing = session.exec(statement).first()
        importance = max(0.1, min(1.0, candidate.importance))
        if existing:
            existing.importance = max(existing.importance, importance)
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
            if _episodic_signature(memory.summary) != signature:
                continue
            memory.importance = max(memory.importance, importance)
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
                importance=importance,
                tags=normalized_tags,
                subject_type=subject_type,
                subject_name=subject_name,
                relationship_to_user=relationship_to_user,
            )
        )
        return 1

    def get_known_people(self, limit: int = 20) -> list[dict[str, str]]:
        with Session(self._engine) as session:
            statement = (
                select(ProfileFact)
                .where(ProfileFact.subject_type == "other_person", ProfileFact.subject_name.is_not(None))
                .order_by(ProfileFact.subject_name.asc(), ProfileFact.id.desc())
            )
            facts = list(session.exec(statement))
        seen: set[str] = set()
        people: list[dict[str, str]] = []
        for fact in facts:
            if not fact.subject_name or fact.subject_name in seen:
                continue
            seen.add(fact.subject_name)
            people.append(
                {
                    "name": fact.subject_name,
                    "relationship_to_user": fact.relationship_to_user or "",
                }
            )
            if len(people) >= limit:
                break
        return people

    def recent_items(self, limit: int = 12) -> list[dict[str, str | int | float | None]]:
        with Session(self._engine) as session:
            profile_facts = list(session.exec(select(ProfileFact).order_by(ProfileFact.created_at.desc(), ProfileFact.id.desc())))[:limit]
            episodic_items = list(session.exec(select(EpisodicMemory).order_by(EpisodicMemory.created_at.desc(), EpisodicMemory.id.desc())))[:limit]
        items: list[dict[str, str | int | float | None]] = []
        for fact in profile_facts:
            items.append(
                {
                    "kind": "profile_fact",
                    "id": fact.id,
                    "subject_type": fact.subject_type,
                    "subject_name": fact.subject_name,
                    "relationship_to_user": fact.relationship_to_user,
                    "summary": fact.fact_text,
                    "category": fact.category,
                    "confidence": fact.confidence,
                    "session_id": None,
                    "created_at": fact.created_at.isoformat() if fact.created_at else None,
                }
            )
        for memory in episodic_items:
            items.append(
                {
                    "kind": "episodic",
                    "id": memory.id,
                    "subject_type": memory.subject_type,
                    "subject_name": memory.subject_name,
                    "relationship_to_user": memory.relationship_to_user,
                    "summary": memory.summary,
                    "category": memory.tags,
                    "confidence": memory.importance,
                    "session_id": memory.session_id,
                    "created_at": memory.created_at.isoformat() if memory.created_at else None,
                }
            )
        items.sort(key=lambda item: (str(item.get("created_at") or ""), int(item.get("id") or 0)), reverse=True)
        return items[:limit]

    def clear_all(self) -> dict[str, int]:
        with Session(self._engine) as session:
            profile_count = len(list(session.exec(select(ProfileFact))))
            episodic_count = len(list(session.exec(select(EpisodicMemory))))
            session.exec(delete(ProfileFact))
            session.exec(delete(EpisodicMemory))
            session.commit()
        return {
            "profile_count": profile_count,
            "episodic_count": episodic_count,
            "cleared_total": profile_count + episodic_count,
        }

    def clear_recent(self, minutes: int = 10) -> dict[str, int]:
        safe_minutes = max(1, minutes)
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=safe_minutes)
        with Session(self._engine) as session:
            recent_facts = list(session.exec(select(ProfileFact).where(ProfileFact.created_at >= cutoff)))
            recent_episodic = list(session.exec(select(EpisodicMemory).where(EpisodicMemory.created_at >= cutoff)))
            for fact in recent_facts:
                session.delete(fact)
            for memory in recent_episodic:
                session.delete(memory)
            session.commit()
        return {
            "profile_count": len(recent_facts),
            "episodic_count": len(recent_episodic),
            "cleared_total": len(recent_facts) + len(recent_episodic),
            "minutes": safe_minutes,
        }

    def clear_selected(self, selections: Iterable[dict[str, object]]) -> dict[str, int]:
        profile_ids: set[int] = set()
        episodic_ids: set[int] = set()
        for item in selections:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind", "")).strip().lower()
            try:
                raw_id = int(item.get("id"))
            except (TypeError, ValueError):
                continue
            if raw_id <= 0:
                continue
            if kind == "profile_fact":
                profile_ids.add(raw_id)
            elif kind == "episodic":
                episodic_ids.add(raw_id)
        with Session(self._engine) as session:
            removed_profile = 0
            removed_episodic = 0
            if profile_ids:
                for fact in session.exec(select(ProfileFact).where(ProfileFact.id.in_(profile_ids))):
                    session.delete(fact)
                    removed_profile += 1
            if episodic_ids:
                for memory in session.exec(select(EpisodicMemory).where(EpisodicMemory.id.in_(episodic_ids))):
                    session.delete(memory)
                    removed_episodic += 1
            session.commit()
        return {
            "profile_count": removed_profile,
            "episodic_count": removed_episodic,
            "cleared_total": removed_profile + removed_episodic,
        }

    def stats(self) -> dict[str, str | int]:
        db_path = _sqlite_path_from_url(settings.database_url)
        with Session(self._engine) as session:
            profile_count = len(list(session.exec(select(ProfileFact))))
            episodic_count = len(list(session.exec(select(EpisodicMemory))))
            scoped_episodic_count = len(
                list(session.exec(select(EpisodicMemory).where(EpisodicMemory.session_id.is_not(None))))
            )
            known_people_count = len(
                {
                    fact.subject_name
                    for fact in session.exec(
                        select(ProfileFact).where(ProfileFact.subject_type == "other_person", ProfileFact.subject_name.is_not(None))
                    )
                    if fact.subject_name
                }
            )
        return {
            "backend": "sqlite",
            "database": str(db_path or settings.database_url),
            "profile_count": profile_count,
            "episodic_count": episodic_count,
            "session_scoped_episodic_count": scoped_episodic_count,
            "known_people_count": known_people_count,
        }
