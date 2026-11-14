"""Known people and their platform identities (part of MemoryService)."""
from __future__ import annotations

from datetime import datetime, timezone
from sqlmodel import Session, select
from .models import KnownPerson, PersonIdentity, ProfileFact
from .fact_text import _normalize_identity, _normalize_subject_name


class PeopleMixin:
    """Known people and their platform identities (part of MemoryService)."""

    def get_known_people(self, limit: int = 20) -> list[dict[str, object]]:
        with Session(self._engine) as session:
            self._materialize_people_from_facts(session)
            stored_people = list(session.exec(select(KnownPerson).order_by(KnownPerson.name.asc(), KnownPerson.id.asc())))[:limit]
            identities = list(session.exec(select(PersonIdentity).order_by(PersonIdentity.platform.asc())))
            identities_by_person: dict[int, list[dict[str, object]]] = {}
            for identity in identities:
                identities_by_person.setdefault(identity.person_id, []).append(
                    {
                        "id": identity.id,
                        "platform": identity.platform,
                        "platform_user_id": identity.platform_user_id,
                        "display_name": identity.display_name or "",
                    }
                )
            session.commit()
            return [
                {
                    "id": person.id,
                    "name": person.name,
                    "relationship_to_user": person.relationship_to_user or "",
                    "trust": person.trust,
                    "notes": person.notes,
                    "accounts": identities_by_person.get(int(person.id or 0), []),
                }
                for person in stored_people
            ]

    def get_known_person(self, person_id: int) -> dict[str, object] | None:
        return next(
            (person for person in self.get_known_people(limit=1000) if int(person.get("id") or 0) == person_id),
            None,
        )

    def _materialize_people_from_facts(self, session: Session) -> None:
        existing = {person.name.casefold() for person in session.exec(select(KnownPerson))}
        statement = (
            select(ProfileFact)
            .where(ProfileFact.subject_type == "other_person", ProfileFact.subject_name.is_not(None))
            .order_by(ProfileFact.subject_name.asc(), ProfileFact.id.desc())
        )
        for fact in session.exec(statement):
            name = _normalize_subject_name(fact.subject_name)
            if not name or name.casefold() in existing:
                continue
            session.add(
                KnownPerson(
                    name=name,
                    relationship_to_user=fact.relationship_to_user,
                    trust="guest",
                    notes="Created from an existing memory record.",
                )
            )
            existing.add(name.casefold())
        session.flush()

    def save_known_person(self, payload: dict[str, object]) -> dict[str, object]:
        name = _normalize_identity(payload.get("name"), limit=120)
        if not name:
            raise ValueError("name is required")
        trust = _normalize_identity(payload.get("trust"), limit=20).lower() or "guest"
        if trust not in {"owner", "trusted", "guest", "public"}:
            raise ValueError("trust must be owner, trusted, guest, or public")
        accounts = payload.get("accounts", [])
        if not isinstance(accounts, list):
            raise ValueError("accounts must be a list")
        with Session(self._engine) as session:
            person_id = int(payload.get("id") or 0)
            person = session.get(KnownPerson, person_id) if person_id else None
            if person is None:
                person = KnownPerson(name=name)
            person.name = name
            person.relationship_to_user = _normalize_subject_name(str(payload.get("relationship_to_user") or ""))
            person.trust = trust
            person.notes = _normalize_identity(payload.get("notes"), limit=2000)
            person.updated_at = datetime.now(timezone.utc)
            session.add(person)
            session.flush()
            assert person.id is not None
            for existing in session.exec(select(PersonIdentity).where(PersonIdentity.person_id == person.id)):
                session.delete(existing)
            seen: set[tuple[str, str]] = set()
            for account in accounts:
                if not isinstance(account, dict):
                    continue
                platform = _normalize_identity(account.get("platform"), limit=40).lower()
                platform_user_id = _normalize_identity(account.get("platform_user_id"), limit=200)
                if not platform or not platform_user_id or (platform, platform_user_id.casefold()) in seen:
                    continue
                seen.add((platform, platform_user_id.casefold()))
                session.add(
                    PersonIdentity(
                        person_id=person.id,
                        platform=platform,
                        platform_user_id=platform_user_id,
                        display_name=_normalize_subject_name(str(account.get("display_name") or "")),
                    )
                )
            session.commit()
            saved_id = person.id
        return next(person for person in self.get_known_people(limit=1000) if person["id"] == saved_id)

    def delete_known_person(self, person_id: int) -> bool:
        with Session(self._engine) as session:
            person = session.get(KnownPerson, person_id)
            if person is None:
                return False
            for identity in session.exec(select(PersonIdentity).where(PersonIdentity.person_id == person_id)):
                session.delete(identity)
            session.delete(person)
            session.commit()
        return True

    def resolve_person_identity(self, platform: str, platform_user_id: str) -> dict[str, object] | None:
        normalized_platform = _normalize_identity(platform, limit=40).lower()
        normalized_id = _normalize_identity(platform_user_id)
        if not normalized_platform or not normalized_id:
            return None
        with Session(self._engine) as session:
            statement = (
                select(PersonIdentity)
                .where(
                    PersonIdentity.platform == normalized_platform,
                    PersonIdentity.platform_user_id == normalized_id,
                )
            )
            identity = session.exec(statement).first()
            if identity is None:
                return None
            person = session.get(KnownPerson, identity.person_id)
            if person is None:
                return None
            return {
                "id": person.id,
                "name": person.name,
                "trust": person.trust,
                "notes": person.notes,
                "relationship_to_user": person.relationship_to_user or "",
                "platform": identity.platform,
                "platform_user_id": identity.platform_user_id,
            }
