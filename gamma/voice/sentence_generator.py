from __future__ import annotations

import time
from typing import Any

from ..conversation.service import ConversationService
from ..persona.loader import build_system_prompt


class SentenceGenerator:
    def __init__(self, conversation: ConversationService) -> None:
        self._conversation = conversation

    def generate_next_sentence(
        self,
        *,
        user_text: str,
        session_id: str | None,
        planner_state: dict[str, Any],
        assistant_reply_so_far: str,
        sentence_index: int,
    ) -> dict[str, Any]:
        started_at = time.perf_counter()
        system_prompt = build_system_prompt(
            memory_service=self._conversation._memory,
            user_text=user_text,
            session_id=session_id,
            speaker=None,
        )
        generator_prompt = (
            "You are generating the next sentence for a spoken assistant reply.\n"
            "Return one JSON object only with keys:\n"
            "sentence_text: exactly one natural spoken sentence.\n"
            "is_final: boolean.\n"
            "Rules:\n"
            "- continue from assistant_reply_so_far\n"
            "- do not repeat prior text\n"
            "- do not restart the answer\n"
            "- keep the wording natural for speech\n"
            "- you may optionally prefix the sentence with one hidden tone tag like [happy] or [concerned] to shape delivery\n"
            "- if the answer is already complete, emit an empty sentence_text and is_final=true\n"
        )
        generator_input = (
            f"{system_prompt}\n\n"
            f"User message:\n{user_text}\n\n"
            f"Planner state:\n{planner_state}\n\n"
            f"Assistant reply so far:\n{assistant_reply_so_far or '(nothing spoken yet)'}\n\n"
            f"Sentence index: {sentence_index}\n"
        )
        try:
            raw = self._conversation._llm_adapter().generate_reply(
                system_prompt=generator_prompt,
                user_text=generator_input,
            ).text
            payload = self._conversation._parse_json_object(raw)
            if not isinstance(payload, dict):
                raise ValueError("sentence generator returned non-object")
        except Exception:
            fallback_text = ""
            if sentence_index == 1:
                fallback_text = str(planner_state.get("key_points", [""]))[:160]
            payload = {
                "sentence_text": fallback_text,
                "is_final": True,
            }
        sentence_text = str(payload.get("sentence_text", "") or "").strip()
        payload["sentence_text"] = sentence_text
        payload["is_final"] = bool(payload.get("is_final", False) or not sentence_text)
        payload["generation_ms"] = round((time.perf_counter() - started_at) * 1000, 1)
        return payload
