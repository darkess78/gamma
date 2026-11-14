"""Deciding what from a turn is worth remembering, and saving it after the reply."""
from __future__ import annotations

import re

from ..config import settings
from ..memory.attribution import MemoryOrigin
from ..schemas.response import MemoryCandidate


class MemoryCandidatesMixin:
    """Deciding what from a turn is worth remembering, and saving it after the reply."""

    def _background_memory_save(self, user_text: str, reply_text: str, session_id: str | None, memory_origin: MemoryOrigin | None = None) -> None:
        """Persist memory candidates in a background thread (fast_mode only)."""
        try:
            candidates = self._build_memory_candidates(user_text=user_text, reply_text=reply_text)
            if candidates:
                self._memory.persist_candidates(candidates, session_id=session_id, origin=memory_origin)
        except Exception:
            pass  # background thread â€” never propagate

    def _remember_assistant_state(self, *, user_text: str, reply_text: str, emotion: str, session_id: str | None = None) -> None:
        if not settings.assistant_state_enabled:
            return
        try:
            self._assistant_state.update(emotion=emotion, user_text=user_text, reply_text=reply_text)
        except Exception:
            pass
        try:
            self._emotion_memory.update_from_turn(
                emotion=emotion,
                user_text=user_text,
                reply_text=reply_text,
                session_id=session_id,
            )
        except Exception:
            pass

    def _build_memory_candidates(self, user_text: str, reply_text: str) -> list[MemoryCandidate]:
        candidates: list[MemoryCandidate] = []
        stripped = user_text.strip()
        lowered = stripped.lower()
        has_structured_candidate = False
        if "my name is " in lowered:
            candidates.append(
                MemoryCandidate(
                    type="profile",
                    text=stripped,
                    importance=0.9,
                    tags=["identity"],
                )
            )
            has_structured_candidate = True
        elif any(phrase in lowered for phrase in ["remember that ", "my favorite", "i like ", "i prefer ", "i dislike ", "i do not like ", "i hate "]):
            candidates.append(
                MemoryCandidate(
                    type="profile",
                    text=stripped,
                    importance=0.84,
                    tags=["preference"],
                )
            )
            has_structured_candidate = True
        elif any(phrase in lowered for phrase in ["i am working on ", "i'm working on ", "the main project is "]):
            candidates.append(
                MemoryCandidate(
                    type="project",
                    text=stripped,
                    importance=0.86,
                    tags=["project_state"],
                )
            )
            has_structured_candidate = True
        elif lowered.startswith("this is ") and len(stripped.split()) >= 4:
            candidates.append(
                MemoryCandidate(
                    type="profile",
                    text=stripped,
                    importance=0.8,
                    tags=["other_person"],
                    subject_type="other_person",
                    subject_name=self._extract_named_person(stripped),
                )
            )
            has_structured_candidate = True
        elif any(phrase in lowered for phrase in ["my friend ", "my brother ", "my sister ", "my coworker ", "my manager ", "my wife ", "my husband ", "my partner "]):
            candidates.append(
                MemoryCandidate(
                    type="profile",
                    text=stripped,
                    importance=0.82,
                    tags=["other_person"],
                    subject_type="other_person",
                    subject_name=self._extract_named_person(stripped),
                    relationship_to_user=self._extract_relationship_label(lowered),
                )
            )
            has_structured_candidate = True
        if len(stripped.split()) >= 8 and self._should_store_episodic_memory(lowered, has_structured_candidate=has_structured_candidate):
            candidates.append(
                MemoryCandidate(
                    type="episodic",
                    text=f"User said: {stripped} | Assistant replied: {reply_text}",
                    importance=self._episodic_importance(lowered),
                    tags=self._episodic_tags(lowered),
                )
            )
        return candidates[:3]

    def _should_store_episodic_memory(self, lowered_user_text: str, *, has_structured_candidate: bool = False) -> bool:
        if self._memory_personality() == "assistant":
            return self._assistant_should_store_episodic_memory(
                lowered_user_text,
                has_structured_candidate=has_structured_candidate,
            )
        return self._entertainer_should_store_episodic_memory(
            lowered_user_text,
            has_structured_candidate=has_structured_candidate,
        )

    def _entertainer_should_store_episodic_memory(
        self,
        lowered_user_text: str,
        *,
        has_structured_candidate: bool = False,
    ) -> bool:
        word_count = len(lowered_user_text.split())
        if word_count < 12:
            return False
        low_signal_markers = {"hi", "hello", "thanks", "thank you", "ok", "okay", "cool", "nice", "good morning", "good night"}
        if lowered_user_text.strip() in low_signal_markers:
            return False
        memorable_markers = (
            "remember",
            "deadline",
            "anniversary",
            "birthday",
            "graduated",
            "promotion",
            "promoted",
            "engaged",
            "married",
            "broke up",
            "passed away",
            "died",
            "won",
            "lost",
            "celebrate",
            "celebrating",
            "proud of",
            "upset",
            "frustrated",
            "sad about",
            "excited about",
            "nervous about",
            "afraid",
            "diagnosed",
            "moving to",
            "moving into",
            "starting a new job",
            "quit my job",
            "apologize",
            "sorry about",
            "never forget",
            "please don't forget",
            "important to me",
            "means a lot to me",
            "big day",
        )
        if any(marker in lowered_user_text for marker in memorable_markers):
            return True
        if has_structured_candidate:
            return False
        routine_work_markers = (
            "project",
            "working on",
            "plan",
            "issue",
            "bug",
            "error",
            "task",
            "todo",
            "fixing",
        )
        if any(marker in lowered_user_text for marker in routine_work_markers):
            return False
        return word_count >= 18 and any(marker in lowered_user_text for marker in ["always", "never", "every time", "keeps happening"])

    def _assistant_should_store_episodic_memory(
        self,
        lowered_user_text: str,
        *,
        has_structured_candidate: bool = False,
    ) -> bool:
        if len(lowered_user_text.split()) < 10:
            return False
        low_signal_markers = {"hi", "hello", "thanks", "thank you", "ok", "okay", "cool", "nice", "good morning", "good night"}
        if lowered_user_text.strip() in low_signal_markers:
            return False
        strong_markers = (
            "remember",
            "project",
            "working on",
            "plan",
            "deadline",
            "prefer",
            "favorite",
            "friend",
            "manager",
            "brother",
            "sister",
            "issue",
            "bug",
            "error",
        )
        if any(marker in lowered_user_text for marker in strong_markers):
            return True
        if has_structured_candidate:
            return False
        return False

    def _episodic_importance(self, lowered_user_text: str) -> float:
        if self._memory_personality() == "assistant":
            return self._assistant_episodic_importance(lowered_user_text)
        return self._entertainer_episodic_importance(lowered_user_text)

    def _entertainer_episodic_importance(self, lowered_user_text: str) -> float:
        if any(marker in lowered_user_text for marker in ["never forget", "please don't forget", "important to me", "means a lot to me"]):
            return 0.86
        if any(marker in lowered_user_text for marker in ["birthday", "anniversary", "graduated", "promotion", "engaged", "married", "passed away", "died"]):
            return 0.82
        if any(marker in lowered_user_text for marker in ["won", "lost", "celebrate", "proud of", "upset", "frustrated", "sad about", "excited about", "nervous about"]):
            return 0.76
        return 0.7

    def _assistant_episodic_importance(self, lowered_user_text: str) -> float:
        if any(marker in lowered_user_text for marker in ["deadline", "urgent", "important", "remember"]):
            return 0.72
        if any(marker in lowered_user_text for marker in ["project", "working on", "plan", "issue", "bug", "error"]):
            return 0.64
        return 0.56

    def _episodic_tags(self, lowered_user_text: str) -> list[str]:
        if self._memory_personality() == "assistant":
            return self._assistant_episodic_tags(lowered_user_text)
        return self._entertainer_episodic_tags(lowered_user_text)

    def _entertainer_episodic_tags(self, lowered_user_text: str) -> list[str]:
        tags = ["conversation"]
        if any(marker in lowered_user_text for marker in ["birthday", "anniversary", "graduated", "promotion", "engaged", "married", "moving to", "starting a new job"]):
            tags.append("milestone")
        if any(marker in lowered_user_text for marker in ["won", "lost", "celebrate", "proud of"]):
            tags.append("event")
        if any(marker in lowered_user_text for marker in ["upset", "frustrated", "sad about", "excited about", "nervous about", "afraid"]):
            tags.append("emotion")
        if any(marker in lowered_user_text for marker in ["remember", "never forget", "please don't forget", "important to me", "means a lot to me"]):
            tags.append("durable")
        return tags[:4]

    def _assistant_episodic_tags(self, lowered_user_text: str) -> list[str]:
        tags = ["conversation"]
        if any(marker in lowered_user_text for marker in ["project", "working on", "plan"]):
            tags.append("project")
        if any(marker in lowered_user_text for marker in ["issue", "bug", "error"]):
            tags.append("problem")
        if "remember" in lowered_user_text:
            tags.append("durable")
        return tags[:4]

    def _memory_personality(self) -> str:
        personality = (settings.memory_personality or "").strip().lower()
        if personality in {"assistant", "entertainer"}:
            return personality
        return "entertainer"

    def _extract_named_person(self, text: str) -> str | None:
        relation_match = re.search(
            r"(?i:\bmy\s+(?:friend|brother|sister|coworker|manager|wife|husband|partner)\s+)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b",
            text,
        )
        if relation_match:
            return relation_match.group(1)
        intro_match = re.search(r"(?i:\bthis is\s+)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b", text)
        if intro_match:
            return intro_match.group(1)
        match = re.search(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b", text)
        if not match:
            return None
        return match.group(1)

    def _extract_relationship_label(self, lowered: str) -> str | None:
        for label in ["friend", "brother", "sister", "coworker", "manager", "wife", "husband", "partner"]:
            if f"my {label} " in lowered:
                return label
        return None

    def _infer_subject_type(self, *, text: str, tags: list[str]) -> str:
        lowered = text.lower()
        if any(tag.lower() in {"friend", "family", "coworker", "partner", "other_person"} for tag in tags):
            return "other_person"
        if lowered.startswith(("my friend ", "my brother ", "my sister ", "my mom ", "my mother ", "my dad ", "my father ", "my coworker ", "my manager ", "this is ")):
            return "other_person"
        return "primary_user"

    def _normalize_subject_name(self, value: object) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = " ".join(value.strip().split())
        return normalized[:120] if normalized else None
