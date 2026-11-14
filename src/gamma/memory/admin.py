"""Dashboard memory management: add, edit, list, clear and count (part of MemoryService)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable
from sqlmodel import Session, delete, select
from sqlalchemy import func
from ..config import settings
from .models import EpisodicMemory, KnownPerson, ProfileFact
from .attribution import manual_origin
from .fact_text import _canonicalize_profile_text, _normalize_identity, _normalize_subject_name, _normalize_whitespace
from .sqlite_setup import sqlite_path


class MemoryAdminMixin:
    """Dashboard memory management: add, edit, list, clear and count (part of MemoryService)."""

    def create_item(self, payload: dict[str, object]) -> dict[str, object]:
        kind = _normalize_identity(payload.get("kind"), limit=40).lower()
        summary = _normalize_whitespace(str(payload.get("summary") or ""))
        if not summary:
            raise ValueError("summary is required")
        subject_type = _normalize_identity(payload.get("subject_type"), limit=40) or "primary_user"
        if subject_type not in {"primary_user", "other_person", "assistant", "general"}:
            raise ValueError("unsupported subject type")
        subject_name = _normalize_subject_name(str(payload.get("subject_name") or ""))
        relationship = _normalize_subject_name(str(payload.get("relationship_to_user") or ""))

        with Session(self._engine) as session:
            if kind == "profile_fact":
                item = ProfileFact(
                    attribution='{"kind":"manual","source":"manual"}',
                    visibility="owner_only",
                    category=_normalize_identity(payload.get("category"), limit=80) or "manual",
                    fact_text=_canonicalize_profile_text(summary),
                    confidence=max(0.0, min(1.0, float(payload.get("confidence", 0.8)))),
                    subject_type=subject_type,
                    subject_name=subject_name,
                    relationship_to_user=relationship,
                )
            elif kind == "episodic":
                item = EpisodicMemory(
                    attribution='{"kind":"manual","source":"manual"}',
                    visibility="owner_only",
                    session_id=_normalize_identity(payload.get("session_id"), limit=120) or None,
                    summary=summary,
                    importance=max(0.0, min(1.0, float(payload.get("confidence", 0.5)))),
                    tags=_normalize_identity(payload.get("category"), limit=500),
                    subject_type=subject_type,
                    subject_name=subject_name,
                    relationship_to_user=relationship,
                )
            else:
                raise ValueError("unsupported memory kind")
            session.add(item)
            session.commit()
            session.refresh(item)
            item_id = item.id
        return next(row for row in self.recent_items(limit=1000) if row["kind"] == kind and row["id"] == item_id)

    def update_item(self, kind: str, item_id: int, payload: dict[str, object]) -> dict[str, object]:
        with Session(self._engine) as session:
            if kind == "profile_fact":
                item = session.get(ProfileFact, item_id)
                if item is None:
                    raise ValueError("profile fact not found")
                summary = _canonicalize_profile_text(str(payload.get("summary") or ""))
                if not summary:
                    raise ValueError("summary is required")
                item.fact_text = summary
                item.category = _normalize_identity(payload.get("category"), limit=80) or item.category
                item.confidence = max(0.0, min(1.0, float(payload.get("confidence", item.confidence))))
                item.subject_name = _normalize_subject_name(str(payload.get("subject_name") or ""))
                item.relationship_to_user = _normalize_subject_name(str(payload.get("relationship_to_user") or ""))
            elif kind == "episodic":
                item = session.get(EpisodicMemory, item_id)
                if item is None:
                    raise ValueError("episodic memory not found")
                summary = _normalize_whitespace(str(payload.get("summary") or ""))
                if not summary:
                    raise ValueError("summary is required")
                item.summary = summary
                item.tags = _normalize_identity(payload.get("category"), limit=500)
                item.importance = max(0.0, min(1.0, float(payload.get("confidence", item.importance))))
                item.subject_name = _normalize_subject_name(str(payload.get("subject_name") or ""))
                item.relationship_to_user = _normalize_subject_name(str(payload.get("relationship_to_user") or ""))
            else:
                raise ValueError("unsupported memory kind")
            item.attribution = manual_origin(item.attribution)
            session.add(item)
            session.commit()
        return next(row for row in self.recent_items(limit=1000) if row["kind"] == kind and row["id"] == item_id)

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
                    "replaced": fact.superseded_at is not None,
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
            removed = {fact.id for fact in recent_facts}
            # Undoing a recent change brings back what it replaced.
            if removed:
                for older in session.exec(select(ProfileFact).where(ProfileFact.superseded_by.in_(removed))):
                    if older.id not in removed:
                        older.superseded_at, older.superseded_by = None, None
                        session.add(older)
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
                profile_ids = self._with_history(session, profile_ids)
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
        db_path = sqlite_path(settings.database_url)
        with Session(self._engine) as session:
            self._materialize_people_from_facts(session)
            profile_count = int(session.exec(select(func.count()).select_from(ProfileFact)).one())
            episodic_count = int(session.exec(select(func.count()).select_from(EpisodicMemory)).one())
            scoped_episodic_count = int(
                session.exec(
                    select(func.count()).select_from(EpisodicMemory).where(EpisodicMemory.session_id.is_not(None))
                ).one()
            )
            known_people_count = int(session.exec(select(func.count()).select_from(KnownPerson)).one())
            session.commit()
        database_size_bytes = db_path.stat().st_size if db_path and db_path.exists() else 0
        return {
            "backend": "sqlite",
            "database": str(db_path or settings.database_url),
            "database_size_bytes": database_size_bytes,
            "journal_mode": "wal",
            "profile_count": profile_count,
            "episodic_count": episodic_count,
            "session_scoped_episodic_count": scoped_episodic_count,
            "known_people_count": known_people_count,
        }
