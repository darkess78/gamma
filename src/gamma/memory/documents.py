"""Transactional persona documents with preserved, read-only legacy sources."""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.pool import NullPool
from sqlmodel import Session, create_engine

from ..config import settings
from .state_authority import StateAuthority, StateConflict, documents

MAX_DOCUMENT_BYTES = 1_048_576


class StateDocuments:
    def __init__(self, engine=None):
        self.engine = engine

    @contextmanager
    def session(self, *, write=False):
        engine = self.engine or create_engine(settings.database_url, poolclass=NullPool)
        try:
            authority = StateAuthority(engine)
            with authority.transaction(operator=True) if write else Session(engine) as session:
                yield session
        finally:
            if self.engine is None:
                engine.dispose()

    @staticmethod
    def _legacy(path: Path) -> dict:
        if not path.exists():
            return {}
        with path.open("rb") as source:
            raw = source.read(MAX_DOCUMENT_BYTES + 1)
        if len(raw) > MAX_DOCUMENT_BYTES:
            raise ValueError("legacy state document exceeds size limit")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("legacy state document must be an object")
        return payload

    def load(self, key: str, legacy_path: Path, *, session=None, legacy_loader=None) -> dict:
        if session is None:
            with self.session() as owned:
                return self.load(key, legacy_path, session=owned, legacy_loader=legacy_loader)
        raw = session.execute(select(documents.c.payload).where(documents.c.key == key)).scalar_one_or_none()
        return json.loads(raw) if raw is not None else (legacy_loader or self._legacy)(legacy_path)

    def mutate(self, key: str, legacy_path: Path, transform, *, session=None, legacy_loader=None) -> dict:
        if session is None:
            with self.session(write=True) as owned:
                result = self.mutate(key, legacy_path, transform, session=owned, legacy_loader=legacy_loader)
                owned.commit()
                return result
        StateAuthority.require_transaction(session)
        if self.engine is not None and session.get_bind().url != self.engine.url:
            raise StateConflict("state_document_database_mismatch")
        row = session.execute(select(documents).where(documents.c.key == key)).mappings().first()
        original = json.loads(row["payload"]) if row is not None else (legacy_loader or self._legacy)(legacy_path)
        legacy = json.dumps(original, ensure_ascii=False) if row is None else None
        result = transform(original)
        if not isinstance(result, dict):
            raise ValueError("state document must be an object")
        encoded = json.dumps(result, ensure_ascii=False)
        if len(encoded.encode("utf-8")) > MAX_DOCUMENT_BYTES:
            raise ValueError("state document exceeds size limit")
        if row is None:
            session.execute(documents.insert().values(key=key, payload=encoded, legacy_payload=legacy, version=1))
        else:
            session.execute(documents.update().where(documents.c.key == key)
                            .values(payload=encoded, version=row["version"] + 1))
        return result
