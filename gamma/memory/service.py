from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from sqlmodel import Session, SQLModel, create_engine, select

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
        if lowered.startswith("my favorite "):
            return "preference:favorite"
        if lowered.startswith("i prefer ") or lowered.startswith("remember that i prefer "):
            return "preference:prefer"
        if lowered.startswith("i like ") or lowered.startswith("remember that i like "):
            return "preference:like"
    return None


def _normalize_subject_name(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.strip().split())
    return normalized[:120] if normalized else None


class MemoryService:
    def __init__(self) -> None:
        connect_args = {}
        if settings.database_url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        self._engine = create_engine(settings.database_url, connect_args=connect_args)
        SQLModel.metadata.create_all(self._engine)
        self._ensure_compatible_schema()

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
                },
            )
            self._ensure_sqlite_columns(
                conn,
                "profilefact",
                {
                    "subject_type": "TEXT DEFAULT 'primary_user'",
                    "subject_name": "TEXT",
                    "relationship_to_user": "TEXT",
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
