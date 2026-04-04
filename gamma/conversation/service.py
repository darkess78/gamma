from __future__ import annotations

from ..errors import ConversationError, GammaError
from ..llm.factory import build_llm_adapter
from ..memory.service import MemoryService
from ..persona.loader import build_system_prompt
from ..schemas.response import AssistantResponse, MemoryCandidate
from ..voice.tts import TTSService


class ConversationService:
    def __init__(self) -> None:
        self._llm = build_llm_adapter()
        self._memory = MemoryService()
        self._tts = TTSService()

    def respond(
        self,
        user_text: str,
        session_id: str | None = None,
        synthesize_speech: bool = False,
    ) -> AssistantResponse:
        stripped = user_text.strip()
        if not stripped:
            raise ConversationError("user_text must not be empty.")

        try:
            system_prompt = build_system_prompt(
                memory_service=self._memory,
                user_text=stripped,
                session_id=session_id,
            )
            reply = self._llm.generate_reply(system_prompt=system_prompt, user_text=stripped)
            memory_candidates = self._build_memory_candidates(user_text=stripped, reply_text=reply.text)
            self._memory.persist_candidates(memory_candidates, session_id=session_id)

            response = AssistantResponse(
                spoken_text=reply.text,
                emotion="neutral",
                motions=[],
                tool_calls=[],
                memory_candidates=memory_candidates,
            )
            if synthesize_speech:
                tts_result = self._tts.synthesize(reply.text, emotion=response.emotion)
                response.audio_path = tts_result.audio_path
                response.audio_content_type = tts_result.content_type
            return response
        except GammaError:
            raise
        except Exception as exc:
            raise ConversationError(f"Conversation pipeline failed: {exc}") from exc

    def memory_stats(self) -> dict[str, str | int]:
        return self._memory.stats()

    def _build_memory_candidates(self, user_text: str, reply_text: str) -> list[MemoryCandidate]:
        candidates: list[MemoryCandidate] = []
        stripped = user_text.strip()
        lowered = stripped.lower()
        if "my name is " in lowered:
            candidates.append(
                MemoryCandidate(
                    type="profile",
                    text=stripped,
                    importance=0.9,
                    tags=["identity"],
                )
            )
        elif any(phrase in lowered for phrase in ["remember that ", "my favorite", "i like ", "i prefer "]):
            candidates.append(
                MemoryCandidate(
                    type="profile",
                    text=stripped,
                    importance=0.8,
                    tags=["preference"],
                )
            )
        if len(stripped.split()) >= 8:
            candidates.append(
                MemoryCandidate(
                    type="episodic",
                    text=f"User said: {stripped} | Assistant replied: {reply_text}",
                    importance=0.55,
                    tags=["conversation"],
                )
            )
        return candidates
