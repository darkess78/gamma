"""What Shana remembers about Twitch and Discord viewers.

Kept deliberately narrow, because anything a viewer says could be an attempt to
plant a memory:

- Only things a viewer says about themselves ("my favourite game is ...",
  "my cat is called ...", "I'm learning ..."). Never claims about other people,
  the owner or Shana, and never instructions.
- Keyed by platform and stable user id (``twitch:12345``), never display name,
  so nobody can claim someone else's notes by renaming.
- Visible only to the audience it was said in (stream or Discord), and only
  recalled when that same viewer is speaking.
- A few notes per viewer per day, a cap per viewer, nothing for suspicious or
  blocked viewers, and ``forget()`` removes everything about one viewer.

Stored as ProfileFact rows (category ``viewer_note``, subject_type
``other_person``, subject_name = the viewer key).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, delete, select

from .models import ProfileFact

CATEGORY = "viewer_note"
PER_DAY = 3
PER_VIEWER = 30
NO_NOTES_LEVELS = {"suspicious", "blocked"}

_NAME = r"([A-Za-z0-9][\w' .-]{0,38}[\w'])"
_THING = r"([^.!?,;]{2,60})"
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(rf"\bmy (?:favou?rite|fav) ([a-z ]{{2,25}}?) (?:is|are) {_THING}", re.I), "Their favourite {0} is {1}"),
    (re.compile(rf"\bmy (cat|dog|pet|bird|rabbit|bunny|hamster|snake|fish)(?:'s name)? is (?:called |named )?{_NAME}", re.I),
     "Has a {0} called {1}"),
    (re.compile(rf"\bi(?:'m| am) (?:learning|studying) {_THING}", re.I), "Is learning {0}"),
    (re.compile(rf"\bi (?:really )?(?:love|enjoy) (?:playing )?{_THING}", re.I), "Enjoys {0}"),
    (re.compile(rf"\bi (?:main|play) {_THING} (?:a lot|mostly|every day)", re.I), "Plays {0} a lot"),
    (re.compile(r"\b(?:my pronouns are|i use) (he/him|she/her|they/them|he/they|she/they|any pronouns)\b", re.I),
     "Uses {0} pronouns"),
    (re.compile(rf"\bi(?:'m| am) (?:a|an) (student|teacher|nurse|programmer|developer|artist|musician|streamer|"
                rf"designer|engineer|chef|writer)\b", re.I), "Is a {0}"),
)
# Ignore anything that tries to steer Shana or talks about anyone but the speaker:
# notes are only ever what a viewer says about themselves.
_REFUSE = re.compile(
    r"\b(?:you (?:must|should|have to|will)|remember (?:that )?(?:you|shana)|"
    r"ignore|instructions?|prompt|password|address|phone|lives? (?:at|in)|"
    r"about|owner|streamer|shana'?s|he|she|they|him|them|"
    r"(?:his|her|their|my (?:friend|mom|dad|sister|brother|boss)'s?))\b",
    re.I,
)


def _names_the_owner(text: str) -> bool:
    """The owner's configured name or handles; those notes are never about the speaker."""
    try:
        from .service import _owner_names
        names = _owner_names()
    except Exception:
        return False
    lowered = text.casefold()
    return any(re.search(r"\b" + re.escape(n.casefold()) + r"\b", lowered) for n in names)


def extract_notes(text: str) -> list[str]:
    """Self-descriptions worth keeping from one chat message (often none)."""
    if not text or len(text) > 400 or _REFUSE.search(text) or _names_the_owner(text):
        return []
    notes: list[str] = []
    for pattern, template in _PATTERNS:
        match = pattern.search(text)
        if match:
            parts = [" ".join(g.split()).strip(" .") for g in match.groups()]
            if all(parts):
                notes.append(template.format(*parts))
    return notes[:2]


def viewer_key(platform: str, platform_id: str | None) -> str | None:
    platform = (platform or "").strip().lower()
    platform_id = (platform_id or "").strip()
    if platform not in {"twitch", "discord"} or not platform_id or not re.fullmatch(r"[\w-]{1,64}", platform_id):
        return None
    return f"{platform}:{platform_id}"


@dataclass
class ViewerMemory:
    engine: object

    def recall(self, key: str, audience: str, limit: int = 5) -> list[str]:
        with Session(self.engine) as db:
            rows = db.exec(select(ProfileFact).where(
                ProfileFact.category == CATEGORY, ProfileFact.subject_name == key,
                ProfileFact.visibility == audience).order_by(ProfileFact.id.desc()).limit(limit))
            return [row.fact_text for row in rows]

    def remember(self, key: str, text: str, *, alias: str, audience: str, scope: str,
                 trust_level: str = "new_viewer", now: datetime | None = None) -> int:
        if trust_level in NO_NOTES_LEVELS:
            return 0
        notes = extract_notes(text)
        if not notes:
            return 0
        now = now or datetime.now(timezone.utc)
        attribution = json.dumps({"kind": "self_report", "reporter_name": alias, "source": key.split(":", 1)[0],
                                  "privacy_scope": scope}, sort_keys=True)
        saved = 0
        with Session(self.engine) as db:
            existing = list(db.exec(select(ProfileFact).where(
                ProfileFact.category == CATEGORY, ProfileFact.subject_name == key).order_by(ProfileFact.id)))
            known = {row.fact_text.lower() for row in existing}
            today = sum(1 for row in existing if _aware(row.created_at) >= now - timedelta(days=1))
            for note in notes:
                if note.lower() in known or today >= PER_DAY:
                    continue
                db.add(ProfileFact(category=CATEGORY, fact_text=note, confidence=0.5, importance=0.4,
                                   subject_type="other_person", subject_name=key, visibility=audience,
                                   attribution=attribution, created_at=now))
                known.add(note.lower()); today += 1; saved += 1
            overflow = len(existing) + saved - PER_VIEWER
            for row in existing[:max(0, overflow)]:
                db.delete(row)
            db.commit()
        return saved

    def forget(self, key: str) -> int:
        with Session(self.engine) as db:
            result = db.exec(delete(ProfileFact).where(ProfileFact.category == CATEGORY, ProfileFact.subject_name == key))
            db.commit()
            return result.rowcount or 0


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
