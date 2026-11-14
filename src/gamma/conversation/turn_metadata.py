"""Parsing and normalizing the model reply: structured turns, fallbacks, metadata and spoken-text cleanup."""
from __future__ import annotations

import json
import re

from ..config import settings
from ..identity.profile import SpeakerProfile
from ..llm.base import LLMCallContext
from ..schemas.conversation import RecipientContext
from ..schemas.response import ConversationTurnDraft, DeliveryMode, EmotionTag, MemoryCandidate, ToolCall, ToolExecutionResult
from ..tools.disclosure import disclose_tool_result
from ..vision.service import VisionImage
from .shared import ALLOWED_EMOTIONS, _CODE_FENCE_RE, _INLINE_TICK_RE, _recipient_context
from .turn_contract import ParsedTurn, TurnContractError


class TurnMetadataMixin:
    """Parsing and normalizing the model reply: structured turns, fallbacks, metadata and spoken-text cleanup."""

    def _parse_turn_result(
        self,
        *,
        raw: str,
        system_prompt: str,
        user_text: str,
        legacy_delivery: DeliveryMode,
        delivery_context: str,
        image: VisionImage | None,
        recipient: RecipientContext | None = None,
    ) -> ParsedTurn:
        _recipient_context(recipient, delivery_context=delivery_context)
        try:
            return self._turn_parser.parse(raw, legacy_delivery=legacy_delivery)
        except TurnContractError:
            pass

        # One bounded regeneration is permitted. The rejected payload is not
        # echoed into the repair prompt, logs, continuity, or output metadata.
        try:
            repaired = self._llm_adapter().generate_reply(
                system_prompt=system_prompt,
                user_text=(
                    user_text
                    + "\n\nYour previous result failed the turn contract. Regenerate the decision once as exact JSON. "
                    "Do not quote, explain, or repeat the rejected result."
                ),
                image_inputs=[image.to_llm_input()] if image is not None else None,
                call_context=LLMCallContext(
                    purpose="conversation_structured_repair",
                    persona_sensitive=True,
                    interaction_mode=delivery_context,
                    reasoning_depth="normal",
                    cost_sensitive=True,
                ),
            )
            return self._turn_parser.parse(repaired.text, legacy_delivery=legacy_delivery)
        except Exception:
            return ParsedTurn(
                draft=self._fallback_turn_draft(delivery_context=delivery_context, recipient=recipient),
                structured=True,
            )

    @staticmethod
    def _fallback_turn_draft(*, delivery_context: str, recipient: RecipientContext | None = None) -> ConversationTurnDraft:
        _recipient_context(recipient, delivery_context=delivery_context)
        if delivery_context in {"public_stream", "ambient", "audio_observation", "presence_wake"}:
            return ConversationTurnDraft(
                action="stay_silent",
                requested_delivery="silent",
                internal_summary="The turn result failed the communication contract and was closed safely.",
                reason_code="contract_fallback_silent",
            )
        return ConversationTurnDraft(
            action="reply",
            requested_delivery="text_only",
            final_text="Iâ€™m having trouble forming a safe response right now. Please try again.",
            internal_summary="The turn result failed the communication contract and used a safe direct fallback.",
            reason_code="contract_fallback_direct",
        )

    def _guard_finalized_text(
        self,
        text: str,
        *,
        tool_results: list[ToolExecutionResult],
        delivery_context: str,
        recipient: RecipientContext | None = None,
    ) -> str:
        value = (text or "").strip()
        recipient = _recipient_context(recipient, delivery_context=delivery_context)
        try:
            self._turn_parser.validate_communicable_text(value)
            raw_outputs = {item.output.strip() for item in tool_results if item.output.strip()}
            public_acknowledgments = {
                disclose_tool_result(item, recipient=recipient).output for item in tool_results
            } if not recipient.allows_private_memory else set()
            if value and value in raw_outputs and value not in public_acknowledgments:
                raise TurnContractError("raw_tool_output_selected")
            return value
        except TurnContractError:
            return self._fallback_turn_draft(delivery_context=delivery_context, recipient=recipient).final_text

    def _cleanup_spoken_text(self, text: str) -> str:
        cleaned = _CODE_FENCE_RE.sub("", text or "")
        cleaned = _INLINE_TICK_RE.sub(r"\1", cleaned)
        cleaned = cleaned.replace("*", " ").replace("#", " ")
        cleaned = re.sub(r"^\s*[-â€¢]\s+", "", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
        return cleaned

    def _extract_turn_metadata(self, user_text: str, reply_text: str, speaker: SpeakerProfile | None = None) -> dict:
        # save_core_memory is owner-only â€” hide it from the tool list for everyone else
        visible_tools = self._tools.tool_summaries()
        if speaker is None or speaker.is_owner:
            pass  # all tools visible
        else:
            visible_tools = [t for t in visible_tools if not t.startswith("save_core_memory")]
        tool_help = "\n".join(f"- {item}" for item in visible_tools)
        extraction_prompt = (
            "You are a strict JSON metadata extractor for an assistant conversation.\n"
            "Return only one JSON object with these keys:\n"
            "internal_summary: short non-user-facing summary string or null.\n"
            "emotion: one of neutral, happy, teasing, concerned, excited, embarrassed, annoyed.\n"
            "motions: array of short motion ids, usually empty.\n"
            "tool_calls: array of {tool, args} objects, usually empty.\n"
            "memory_candidates: array of up to 3 objects with keys type, text, importance, tags, subject_type, subject_name, relationship_to_user.\n"
            "Available safe tools:\n"
            f"{tool_help}\n"
            "Only emit tool_calls for an explicit request these tools can fulfill. coding_goal is for an owner concrete repository coding or improvement instruction, never brainstorming. coding_status and coding_control read or control that work.\n"
            "Use search_memory when the user asks what you remember, asks you to recall prior context, or asks about a known person/project.\n"
            "Use save_memory only for durable facts, reminders, recurring preferences, named people, important plans, or corrections the assistant should intentionally keep.\n"
            "Do not use save_memory for casual chatter, acknowledgements, or one-off small talk.\n"
            "Do not invent tools. Prefer zero tool calls unless one of the listed tools is clearly relevant.\n"
            "subject_type must be one of primary_user, other_person, unknown.\n"
            "Use primary_user for facts about the user speaking as 'I' or 'my'.\n"
            "Use other_person for introduced people like friends, family members, coworkers, or named third parties.\n"
            "Only emit memory candidates for durable facts, meaningful preferences, recurring projects, or important moments.\n"
            "Do not store trivial chatter. Return valid JSON only."
        )
        extraction_input = (
            f"User message:\n{user_text}\n\n"
            f"Assistant reply:\n{reply_text}\n"
        )
        try:
            raw = self._llm_adapter().generate_reply(
                system_prompt=extraction_prompt,
                user_text=extraction_input,
                call_context=LLMCallContext(
                    purpose="metadata_extraction",
                    reasoning_depth="light",
                    interaction_mode="memory",
                    cost_sensitive=True,
                ),
            ).text
            payload = self._parse_json_object(raw)
            return {
                "internal_summary": self._normalize_summary(payload.get("internal_summary")),
                "emotion": self._normalize_emotion(payload.get("emotion")),
                "motions": self._normalize_motions(payload.get("motions")),
                "tool_calls": self._normalize_tool_calls(payload.get("tool_calls")),
                "memory_candidates": self._normalize_memory_candidates(payload.get("memory_candidates")),
            }
        except Exception:
            return self._default_metadata()

    def _default_metadata(self) -> dict:
        return {
            "internal_summary": None,
            "emotion": "neutral",
            "motions": [],
            "tool_calls": [],
            "memory_candidates": [],
        }

    def _needs_metadata_pass(self, user_text: str, inferred_tool_calls: list[ToolCall]) -> bool:
        if inferred_tool_calls:
            return False
        lowered = user_text.lower()
        if any(
            phrase in lowered
            for phrase in [
                "my name is ",
                "remember that ",
                "remember this",
                "i like ",
                "i prefer ",
                "my favorite ",
                "this is ",
                "my friend ",
                "my brother ",
                "my sister ",
                "my coworker ",
                "my manager ",
            ]
        ):
            return True
        return len(user_text.split()) >= 12

    def _looks_like_heavy_reasoning_turn(self, user_text: str) -> bool:
        lowered = (user_text or "").lower()
        if len(lowered.split()) > settings.llm_router_complex_max_input_words:
            return True
        markers = (
            "write code",
            "debug",
            "traceback",
            "stack trace",
            "refactor",
            "architecture",
            "design a system",
            "compare",
            "tradeoff",
            "step by step",
            "plan",
            "analyze",
        )
        return any(marker in lowered for marker in markers)

    def _parse_json_object(self, raw: str) -> dict:
        stripped = raw.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            if len(lines) >= 3:
                stripped = "\n".join(lines[1:-1]).strip()
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise ValueError("no JSON object found")
        return json.loads(stripped[start : end + 1])

    def _normalize_summary(self, value: object) -> str | None:
        if not isinstance(value, str):
            return None
        summary = " ".join(value.strip().split())
        return summary[:400] if summary else None

    def _normalize_emotion(self, value: object) -> EmotionTag:
        if isinstance(value, str) and value.strip().lower() in ALLOWED_EMOTIONS:
            return value.strip().lower()  # type: ignore[return-value]
        return "neutral"

    def _normalize_motions(self, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        motions = [str(item).strip() for item in value if str(item).strip()]
        return motions[:5]

    def _normalize_tool_calls(self, value: object) -> list[ToolCall]:
        if not isinstance(value, list):
            return []
        tool_calls: list[ToolCall] = []
        for item in value[:3]:
            if not isinstance(item, dict):
                continue
            tool = str(item.get("tool", "")).strip()
            args = item.get("args", {})
            if not tool or not isinstance(args, dict):
                continue
            tool_calls.append(ToolCall(tool=tool, args=args))
        return tool_calls

    def _normalize_memory_candidates(self, value: object) -> list[MemoryCandidate]:
        if not isinstance(value, list):
            return []
        candidates: list[MemoryCandidate] = []
        for item in value[:3]:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "")).strip()
            candidate_type = str(item.get("type", "")).strip() or "episodic"
            if not text:
                continue
            importance_raw = item.get("importance", 0.5)
            try:
                importance = float(importance_raw)
            except (TypeError, ValueError):
                importance = 0.5
            tags_raw = item.get("tags", [])
            tags = [str(tag).strip() for tag in tags_raw if str(tag).strip()] if isinstance(tags_raw, list) else []
            subject_type = str(item.get("subject_type", "")).strip().lower()
            if subject_type not in {"primary_user", "other_person", "unknown"}:
                subject_type = self._infer_subject_type(text=text, tags=tags)
            subject_name = self._normalize_subject_name(item.get("subject_name"))
            relationship_to_user = self._normalize_subject_name(item.get("relationship_to_user"))
            candidates.append(
                MemoryCandidate(
                    type=candidate_type,
                    text=text[:500],
                    importance=max(0.1, min(1.0, importance)),
                    tags=tags[:6],
                    subject_type=subject_type,  # type: ignore[arg-type]
                    subject_name=subject_name,
                    relationship_to_user=relationship_to_user,
                )
            )
        return candidates
