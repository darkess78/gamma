"""How well Shana knows each person, built from real interactions.

Rule-based, no model:

- Each meaningful turn raises familiarity by ``STEP`` of the distance left to
  1.0, so gains shrink as she gets to know someone. At most ``STEPS_PER_DAY``
  turns a day count, so one long stream doesn't make a stranger "close".
- Trivial turns ("lol", one-word reactions, very short lines) count for nothing.
- Time apart pulls familiarity back toward neutral with a half-life of
  ``HALF_LIFE_DAYS``. This is worked out when the row is read; no background job.
- Suspicious and blocked viewers get nothing stored.
- One row per person and audience. Keys are ``owner``, ``person:<id>`` for a
  linked person record, or a platform key like ``twitch:12345``. A row built on
  stream is only read on stream; the owner's row lives only in ``owner_only``
  and is only read for owner-private turns.

The owner's static ``persona/relationship_state.json`` stays the baseline: its
``familiarity`` is the owner's neutral point, and the live numbers are added
next to it in the owner-private prompt.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlmodel import Session, delete, select

from .models import RelationshipState
from .triage import obvious_skip

STEP = 0.05
STEPS_PER_DAY = 5
HALF_LIFE_DAYS = 30.0
MIN_WORDS = 3
NO_ACCRUAL_LEVELS = {"suspicious", "blocked"}
OWNER_KEY = "owner"
OWNER_AUDIENCE = "owner_only"
SHARED_AUDIENCES = {"stream", "discord"}
# Lowest familiarity for each label, highest first.
LABELS = ((0.7, "close"), (0.4, "regular"), (0.15, "familiar face"), (0.0, "new"))
_PHRASES = {"close": "you're close", "regular": "they're a regular",
            "familiar face": "they're a familiar face", "new": "they're still new"}
_KEY = re.compile(r"owner|person:\d{1,12}|(?:twitch|discord):[\w-]{1,64}")


def relationship_key(platform: str, platform_id: str | None, *, person_id: int | None = None) -> str | None:
    """The linked person when known, otherwise the platform account."""
    if person_id is not None:
        return f"person:{int(person_id)}"
    platform = (platform or "").strip().lower()
    platform_id = (platform_id or "").strip()
    if platform not in {"twitch", "discord"} or not re.fullmatch(r"[\w-]{1,64}", platform_id):
        return None
    return f"{platform}:{platform_id}"


def valid_key(key: str) -> bool:
    return bool(_KEY.fullmatch(key or ""))


def meaningful(text: str) -> bool:
    text = " ".join((text or "").split())
    return len(re.findall(r"[A-Za-z0-9']+", text)) >= MIN_WORDS and obvious_skip(text) is None


def label(familiarity: float) -> str:
    return next(name for floor, name in LABELS if familiarity >= floor)


def decayed(familiarity: float, last_seen: datetime, now: datetime, *, neutral: float = 0.0) -> float:
    days = max(0.0, (now - _aware(last_seen)).total_seconds() / 86400)
    return neutral + (familiarity - neutral) * 0.5 ** (days / HALF_LIFE_DAYS)


@dataclass(frozen=True)
class Relationship:
    key: str
    audience: str
    familiarity: float  # already decayed to the time it was read
    interaction_count: int
    first_seen: datetime
    last_seen: datetime

    @property
    def label(self) -> str:
        return label(self.familiarity)


@dataclass
class RelationshipStore:
    engine: object

    def get(self, key: str, audience: str, *, now: datetime | None = None, neutral: float = 0.0) -> Relationship | None:
        now = now or datetime.now(timezone.utc)
        with Session(self.engine) as db:
            row = self._row(db, key, audience)
            if row is None:
                return None
            return Relationship(row.key, row.audience, decayed(row.familiarity, row.last_seen, now, neutral=neutral),
                                row.interaction_count, _aware(row.first_seen), _aware(row.last_seen))

    def record(self, key: str, text: str, *, audience: str, trust_level: str = "new_viewer",
               now: datetime | None = None, neutral: float = 0.0) -> bool:
        """Count one finished turn. Returns True when something was stored."""
        if trust_level in NO_ACCRUAL_LEVELS or not meaningful(text) or not _allowed(key, audience):
            return False
        now = now or datetime.now(timezone.utc)
        today = now.date().isoformat()
        with Session(self.engine) as db:
            row = self._row(db, key, audience)
            if row is None:
                row = RelationshipState(key=key, audience=audience, familiarity=neutral, first_seen=now, last_seen=now)
            familiarity = decayed(row.familiarity, row.last_seen, now, neutral=neutral)
            if row.steps_day != today:
                row.steps_day, row.steps_today = today, 0
            if row.steps_today < STEPS_PER_DAY:
                familiarity += STEP * (1.0 - familiarity)
                row.steps_today += 1
            row.familiarity = min(1.0, max(0.0, familiarity))
            row.interaction_count += 1
            row.last_seen = row.updated_at = now
            db.add(row)
            db.commit()
        return True

    def all_for(self, key: str, *, now: datetime | None = None, neutral: float = 0.0) -> list[dict]:
        """Every audience's row for one key, for the owner's memory API."""
        now = now or datetime.now(timezone.utc)
        with Session(self.engine) as db:
            rows = list(db.exec(select(RelationshipState).where(RelationshipState.key == key)))
        items = []
        for r in rows:
            current = decayed(r.familiarity, r.last_seen, now, neutral=neutral)
            items.append({"audience": r.audience, "familiarity": round(current, 3), "label": label(current),
                          "interaction_count": r.interaction_count, "first_seen": _aware(r.first_seen).isoformat(),
                          "last_seen": _aware(r.last_seen).isoformat(), "updated_at": _aware(r.updated_at).isoformat()})
        return items

    def forget(self, key: str) -> int:
        with Session(self.engine) as db:
            result = db.exec(delete(RelationshipState).where(RelationshipState.key == key))
            db.commit()
            return result.rowcount or 0

    @staticmethod
    def _row(db: Session, key: str, audience: str) -> RelationshipState | None:
        return db.exec(select(RelationshipState).where(
            RelationshipState.key == key, RelationshipState.audience == audience)).first()


def shared_line(rel: Relationship | None, name: str, *, now: datetime | None = None) -> str:
    """One short line about the current speaker for a shared prompt."""
    if rel is None or rel.interaction_count <= 0:
        return f"This is the first time you've really talked with {name}."
    times = "once" if rel.interaction_count == 1 else f"about {rel.interaction_count} times"
    return f"You've talked with {name} {times}; {_PHRASES[rel.label]}. Last talked {ago(rel.last_seen, now)}."


def owner_view(rel: Relationship, *, baseline: float, now: datetime | None = None) -> dict:
    """The owner's live numbers, shown next to the static relationship file."""
    return {"familiarity": round(rel.familiarity, 2), "baseline_familiarity": round(baseline, 2),
            "interactions": rel.interaction_count, "last_talked": ago(rel.last_seen, now)}


def ago(when: datetime, now: datetime | None = None) -> str:
    days = int(((now or datetime.now(timezone.utc)) - _aware(when)).total_seconds() // 86400)
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 14:
        return f"{days} days ago"
    if days < 60:
        return f"{days // 7} weeks ago"
    return f"{days // 30} months ago"


def _allowed(key: str, audience: str) -> bool:
    """The owner's row is only ever owner_only, and nobody else can write there."""
    if not valid_key(key):
        return False
    if key == OWNER_KEY:
        return audience == OWNER_AUDIENCE
    return audience in SHARED_AUDIENCES


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
