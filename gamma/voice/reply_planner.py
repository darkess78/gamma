from __future__ import annotations

import json
import time
from typing import Any

from ..conversation.service import ConversationService
from ..persona.loader import build_system_prompt


class ReplyPlanner:
    def __init__(self, conversation: ConversationService) -> None:
        self._conversation = conversation

    def plan(self, *, user_text: str, session_id: str | None) -> dict[str, Any]:
        started_at = time.perf_counter()
        system_prompt = build_system_prompt(
            memory_service=self._conversation._memory,
            user_text=user_text,
            session_id=session_id,
            speaker=None,
        )
        planner_prompt = (
            "You are a reply planner for a spoken assistant.\n"
            "Return one JSON object only with keys:\n"
            "intent: short string.\n"
            "tone: short string.\n"
            "key_points: array of short strings.\n"
            "estimated_sentence_count: integer from 1 to 4.\n"
            "stop_condition: short string.\n"
            "Do not write user-facing prose."
        )
        planner_input = f"{system_prompt}\n\nUser message:\n{user_text}\n"
        try:
            raw = self._conversation._llm_adapter().generate_reply(
                system_prompt=planner_prompt,
                user_text=planner_input,
            ).text
            payload = self._conversation._parse_json_object(raw)
            if not isinstance(payload, dict):
                raise ValueError("planner returned non-object")
        except Exception:
            payload = {
                "intent": "answer the user directly",
                "tone": "concise spoken response",
                "key_points": [user_text[:120]],
                "estimated_sentence_count": 2,
                "stop_condition": "stop when the answer is complete",
            }
        payload["planner_ms"] = round((time.perf_counter() - started_at) * 1000, 1)
        return payload
