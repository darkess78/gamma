from __future__ import annotations

import json

from ..memory.service import MemoryService
from ..schemas.response import MemoryCandidate
from ..system.status import SystemStatusService
from .base import Tool, ToolResult


class MemoryStatsTool(Tool):
    name = "memory_stats"
    description = "Return memory database counts and configuration."

    def __init__(self) -> None:
        self._memory = MemoryService()

    def run(self, **kwargs) -> ToolResult:
        _ = kwargs
        stats = self._memory.stats()
        return ToolResult(ok=True, output=json.dumps(stats, ensure_ascii=False, indent=2), metadata=stats)


class KnownPeopleTool(Tool):
    name = "known_people"
    description = "Return the currently stored known people list."

    def __init__(self) -> None:
        self._memory = MemoryService()

    def run(self, **kwargs) -> ToolResult:
        _ = kwargs
        people = self._memory.get_known_people()
        return ToolResult(ok=True, output=json.dumps(people, ensure_ascii=False, indent=2), metadata={"count": len(people)})


class ProviderStatusTool(Tool):
    name = "provider_status"
    description = "Return current LLM, STT, and TTS provider status and health."

    def __init__(self) -> None:
        self._system_status = SystemStatusService()

    def run(self, **kwargs) -> ToolResult:
        _ = kwargs
        providers = self._system_status.build_status()["providers"]
        return ToolResult(ok=True, output=json.dumps(providers, ensure_ascii=False, indent=2), metadata=providers)


class RecentArtifactsTool(Tool):
    name = "recent_artifacts"
    description = "Return recently generated audio and JSON artifacts."

    def __init__(self) -> None:
        self._system_status = SystemStatusService()

    def run(self, limit: int = 5, **kwargs) -> ToolResult:
        _ = kwargs
        safe_limit = max(1, min(int(limit), 12))
        artifacts = self._system_status.build_status()["recent_artifacts"][:safe_limit]
        return ToolResult(
            ok=True,
            output=json.dumps(artifacts, ensure_ascii=False, indent=2),
            metadata={"count": len(artifacts), "limit": safe_limit},
        )


class SearchMemoryTool(Tool):
    name = "search_memory"
    description = "Search profile facts and episodic memory. Args: query, optional session_id, subject_type, subject_name, limit."

    def __init__(self) -> None:
        self._memory = MemoryService()

    def run(
        self,
        query: str,
        session_id: str | None = None,
        subject_type: str | None = None,
        subject_name: str | None = None,
        limit: int = 5,
        **kwargs,
    ) -> ToolResult:
        _ = kwargs
        safe_query = " ".join(str(query).split())
        safe_limit = max(1, min(int(limit), 10))
        facts = self._memory.get_profile_facts(limit=safe_limit, subject_type=subject_type or "primary_user", subject_name=subject_name)
        memories = self._memory.search_memories(
            safe_query,
            session_id=session_id,
            subject_type=subject_type,
            subject_name=subject_name,
            limit=safe_limit,
        )
        payload = {
            "profile_facts": [
                {
                    "category": fact.category,
                    "fact_text": fact.fact_text,
                    "confidence": fact.confidence,
                    "subject_type": fact.subject_type,
                    "subject_name": fact.subject_name,
                    "relationship_to_user": fact.relationship_to_user,
                }
                for fact in facts
            ],
            "episodic_memories": [
                {
                    "summary": memory.summary,
                    "importance": memory.importance,
                    "tags": memory.tags,
                    "subject_type": memory.subject_type,
                    "subject_name": memory.subject_name,
                    "relationship_to_user": memory.relationship_to_user,
                    "session_id": memory.session_id,
                }
                for memory in memories
            ],
        }
        return ToolResult(
            ok=True,
            output=json.dumps(payload, ensure_ascii=False, indent=2),
            metadata={"profile_count": len(payload["profile_facts"]), "episodic_count": len(payload["episodic_memories"])},
        )


class SaveMemoryTool(Tool):
    name = "save_memory"
    description = (
        "Persist a scoped memory candidate. Args: type, text, optional importance, tags, subject_type, subject_name, "
        "relationship_to_user, session_id."
    )

    def __init__(self) -> None:
        self._memory = MemoryService()

    def run(
        self,
        type: str,
        text: str,
        importance: float = 0.7,
        tags: list[str] | None = None,
        subject_type: str = "primary_user",
        subject_name: str | None = None,
        relationship_to_user: str | None = None,
        session_id: str | None = None,
        **kwargs,
    ) -> ToolResult:
        _ = kwargs
        normalized_tags = [str(tag).strip() for tag in (tags or []) if str(tag).strip()]
        normalized_text = " ".join(str(text).split())
        normalized_type = self._normalize_type(str(type).strip() or "episodic", normalized_text, normalized_tags)
        candidate = MemoryCandidate(
            type=normalized_type,
            text=normalized_text,
            importance=max(0.1, min(1.0, float(importance))),
            tags=normalized_tags[:6],
            subject_type=subject_type if subject_type in {"primary_user", "other_person", "unknown"} else "primary_user",
            subject_name=subject_name,
            relationship_to_user=relationship_to_user,
        )
        saved = self._memory.persist_candidates([candidate], session_id=session_id)
        payload = {
            "saved": saved,
            "candidate": candidate.model_dump(),
            "session_id": session_id,
        }
        return ToolResult(ok=True, output=json.dumps(payload, ensure_ascii=False, indent=2), metadata=payload)

    def _normalize_type(self, candidate_type: str, text: str, tags: list[str]) -> str:
        lowered = text.lower()
        tag_set = {tag.lower() for tag in tags}
        if candidate_type in {"profile", "fact", "preference", "project", "boundary", "episodic"}:
            normalized = candidate_type
        else:
            normalized = "episodic"
        if normalized == "episodic":
            if any(tag in {"allergy", "identity", "preference", "user_fact"} for tag in tag_set):
                return "profile"
            if lowered.startswith(("i am ", "i'm ", "my ", "remember this: i ", "remember that i ")):
                return "profile"
        return normalized
