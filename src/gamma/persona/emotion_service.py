from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..config import settings
from .emotion_extractor import extract_emotion_turn
from .emotion_models import AssistantEmotionState, EmotionalEpisode, EmotionalPattern


_DEFAULT_PATH = settings.data_dir / "assistant_emotion_memory.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class EmotionMemoryService:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _DEFAULT_PATH

    def load_bundle(self) -> dict[str, object]:
        if not self._path.exists():
            return {
                "state": AssistantEmotionState(),
                "episodes": [],
                "patterns": [],
            }
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return {"state": AssistantEmotionState(), "episodes": [], "patterns": []}
        state_payload = payload.get("state", {}) if isinstance(payload, dict) else {}
        episodes_payload = payload.get("episodes", []) if isinstance(payload, dict) else []
        patterns_payload = payload.get("patterns", []) if isinstance(payload, dict) else []
        state = AssistantEmotionState(**state_payload) if isinstance(state_payload, dict) else AssistantEmotionState()
        episodes = [EmotionalEpisode(**item) for item in episodes_payload if isinstance(item, dict)]
        patterns = [EmotionalPattern(**item) for item in patterns_payload if isinstance(item, dict)]
        return {"state": state, "episodes": episodes, "patterns": patterns}

    def load_state(self) -> AssistantEmotionState:
        return self.load_bundle()["state"]  # type: ignore[return-value]

    def relevant_context(self, *, user_text: str, limit: int = 3) -> dict[str, object]:
        bundle = self.load_bundle()
        state: AssistantEmotionState = bundle["state"]  # type: ignore[assignment]
        episodes: list[EmotionalEpisode] = bundle["episodes"]  # type: ignore[assignment]
        patterns: list[EmotionalPattern] = bundle["patterns"]  # type: ignore[assignment]
        lowered = user_text.lower()
        relevant_episodes = [
            item for item in episodes
            if item.emotion in lowered
            or item.trigger_type in lowered
            or any(term in lowered for term in item.event_summary.lower().split()[:8])
        ]
        relevant_patterns = [
            item for item in patterns
            if item.emotion_family in lowered
            or any(term in lowered for term in item.pattern_text.lower().split()[:8])
        ]
        return {
            "state": state,
            "episodes": relevant_episodes[-limit:],
            "patterns": relevant_patterns[-limit:],
        }

    def update_from_turn(self, *, emotion: str, user_text: str, reply_text: str, session_id: str | None = None) -> None:
        bundle = self.load_bundle()
        state: AssistantEmotionState = bundle["state"]  # type: ignore[assignment]
        episodes: list[EmotionalEpisode] = bundle["episodes"]  # type: ignore[assignment]
        patterns: list[EmotionalPattern] = bundle["patterns"]  # type: ignore[assignment]
        extracted = extract_emotion_turn(emotion=emotion, user_text=user_text, reply_text=reply_text)

        state.current_emotion = extracted.emotion
        state.intensity = extracted.intensity
        state.emotional_target = extracted.emotional_target
        state.cause_summary = extracted.cause_summary
        state.updated_at = _utc_now()
        state.decay_turns_remaining = max(0, settings.assistant_emotion_decay_turns)
        state.recent_emotions.append(extracted.emotion)
        state.recent_emotions = state.recent_emotions[-8:]
        state.notes.append(f"{extracted.emotion}: user='{extracted.cause_summary}'")
        state.notes = state.notes[-8:]

        if extracted.intensity >= settings.assistant_emotion_episode_threshold and extracted.emotion != "neutral":
            episodes.append(
                EmotionalEpisode(
                    event_summary=f"{extracted.trigger_type}: {' '.join(reply_text.split())[:120]}",
                    emotion=extracted.emotion,
                    intensity=extracted.intensity,
                    trigger_type=extracted.trigger_type,
                    relationship_effect=extracted.relationship_effect,
                    importance=max(extracted.intensity, 0.5),
                    created_at=_utc_now(),
                    session_id=session_id,
                )
            )
            episodes[:] = episodes[-40:]

        if extracted.pattern_text:
            matched = next((item for item in patterns if item.pattern_text == extracted.pattern_text), None)
            if matched is None:
                patterns.append(
                    EmotionalPattern(
                        pattern_text=extracted.pattern_text,
                        emotion_family=extracted.emotion,
                        confidence=0.55,
                        evidence_count=1,
                        subject_scope="user",
                        last_reinforced_at=_utc_now(),
                    )
                )
            else:
                matched.evidence_count += 1
                matched.confidence = min(0.95, matched.confidence + 0.08)
                matched.last_reinforced_at = _utc_now()
            patterns[:] = [
                item for item in patterns
                if item.evidence_count >= 1
            ][-20:]

        self._save(state=state, episodes=episodes, patterns=patterns)

    def dashboard_payload(self) -> dict[str, object]:
        bundle = self.load_bundle()
        state: AssistantEmotionState = bundle["state"]  # type: ignore[assignment]
        episodes: list[EmotionalEpisode] = bundle["episodes"]  # type: ignore[assignment]
        patterns: list[EmotionalPattern] = bundle["patterns"]  # type: ignore[assignment]
        return {
            "state": state.as_dict(),
            "episodes": [item.as_dict() for item in episodes[-8:]],
            "patterns": [item.as_dict() for item in patterns[-8:]],
        }

    def _save(self, *, state: AssistantEmotionState, episodes: list[EmotionalEpisode], patterns: list[EmotionalPattern]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "state": state.as_dict(),
            "episodes": [item.as_dict() for item in episodes],
            "patterns": [item.as_dict() for item in patterns if item.evidence_count >= settings.assistant_emotion_pattern_threshold or item.evidence_count >= 1],
        }
        self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
