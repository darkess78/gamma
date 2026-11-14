"""Memory retrieval check: does the right memory reach Shana for a question?

Seeds a temporary memory database from memory_cases.json, asks each question
through MemoryService (the same calls the prompt builder makes), and reports:

- episode recall@5 and fact recall@5: share of expected memories returned;
- update accuracy: for questions with an older contradicted memory ("avoid"),
  whether the newer one ranks above it.

Also scores the previous search (substring match within the current session,
facts by importance only) on the same cases for comparison. No model or GPU.

    python -m gamma.evals.memory_retrieval
"""
from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlmodel import Session, select

CASES = Path(__file__).with_name("memory_cases.json")


@dataclass
class Result:
    episode_recall: float
    fact_recall: float
    update_accuracy: float
    paraphrase_recall: float
    misses: list[str]


def _seed(service, cases: dict, now: datetime) -> tuple[dict[str, int], dict[str, int]]:
    from ..memory.models import EpisodicMemory, ProfileFact
    episode_ids, fact_ids = {}, {}
    with Session(service._engine) as db:
        for e in cases["episodes"]:
            row = EpisodicMemory(summary=e["summary"], tags=e["tags"], session_id=e["session"],
                                 importance=e["importance"], created_at=now - timedelta(days=e["age"]))
            db.add(row); db.flush(); episode_ids[e["id"]] = row.id
        for f in cases["facts"]:
            row = ProfileFact(category=f["category"], fact_text=f["text"], importance=f["importance"],
                              created_at=now - timedelta(days=f["age"]))
            db.add(row); db.flush(); fact_ids[f["id"]] = row.id
        db.commit()
    return episode_ids, fact_ids


def _legacy_episodes(service, query: str, session_id: str, limit: int):
    """The search before 2026-11-14: any 3+ letter word as a substring, current session only."""
    from ..memory.models import EpisodicMemory
    words = [w.lower() for w in query.split() if len(w) >= 3]
    with Session(service._engine) as db:
        rows = list(db.exec(select(EpisodicMemory).where(EpisodicMemory.session_id == session_id)))
    scored = [(sum(w in f"{r.summary} {r.tags}".lower() for w in words), r) for r in rows]
    scored = [(s, r) for s, r in scored if s]
    scored.sort(key=lambda x: (x[0], x[1].importance, x[1].id), reverse=True)
    return [r for _, r in scored[:limit]]


def _legacy_facts(service, limit: int):
    return service.get_profile_facts(limit=limit)


def evaluate(*, legacy: bool = False, k: int = 5) -> Result:
    from ..config import settings
    from ..memory.service import MemoryService
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory() as tmp:
        old_url = settings.database_url
        settings.database_url = "sqlite:///" + str(Path(tmp) / "eval.db")
        try:
            service = MemoryService()
            episode_ids, fact_ids = _seed(service, cases, now)
            hits = total = fact_hits = fact_total = update_ok = update_total = 0
            para_hits = para_total = 0
            misses: list[str] = []
            for case in cases["questions"]:
                q, session_id = case["q"], case.get("session")
                if legacy:
                    episodes = _legacy_episodes(service, q, session_id, k)
                    facts = _legacy_facts(service, k)
                else:
                    episodes = service.search_memories(q, session_id=session_id, limit=k, subject_type="primary_user",
                                                       cross_session=True)
                    facts = service.get_relevant_facts(q, limit=k, subject_type="primary_user")
                got_episodes = [m.id for m in episodes]
                got_facts = [f.id for f in facts]
                if case.get("paraphrase"):
                    for name in case.get("episodes", []):
                        para_total += 1; para_hits += episode_ids[name] in got_episodes
                    for name in case.get("facts", []):
                        para_total += 1; para_hits += fact_ids[name] in got_facts
                    continue
                for name in case.get("episodes", []):
                    total += 1
                    if episode_ids[name] in got_episodes:
                        hits += 1
                    else:
                        misses.append(f"{q!r}: missing episode {name}")
                for name in case.get("facts", []):
                    fact_total += 1
                    if fact_ids[name] in got_facts:
                        fact_hits += 1
                    else:
                        misses.append(f"{q!r}: missing fact {name}")
                for old in case.get("avoid", []):
                    update_total += 1
                    new = episode_ids[case["episodes"][0]]
                    if new in got_episodes and (episode_ids[old] not in got_episodes
                                                or got_episodes.index(new) < got_episodes.index(episode_ids[old])):
                        update_ok += 1
                    else:
                        misses.append(f"{q!r}: older {old} not ranked below the update")
        finally:
            settings.database_url = old_url
    return Result(hits / total, fact_hits / fact_total, update_ok / update_total if update_total else 1.0,
                  para_hits / para_total if para_total else 0.0, misses)


def main() -> int:
    for label, legacy in (("previous search", True), ("current search", False)):
        r = evaluate(legacy=legacy)
        print(f"{label:16} episode recall@5 {r.episode_recall:.0%}  fact recall@5 {r.fact_recall:.0%}  "
              f"updates {r.update_accuracy:.0%}  paraphrases {r.paraphrase_recall:.0%}")
        for miss in r.misses:
            print(f"    {miss}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
