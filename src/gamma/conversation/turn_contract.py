from __future__ import annotations

import json
import re
from dataclasses import dataclass

from pydantic import ValidationError

from ..schemas.response import ConversationTurnDraft, DeliveryMode, ResponseAction


_PRIVATE_MARKERS = re.compile(
    r"<\s*/?\s*(?:think|analysis|reasoning|chain[-_ ]?of[-_ ]?thought)\b|"
    r"\b(?:private_reasoning|hidden_reasoning|scratchpad)\b",
    re.IGNORECASE,
)
_PLANNER_KEYS = re.compile(
    r"[\"']?(?:tool_calls|memory_candidates|state_updates|internal_summary|requested_delivery)[\"']?\s*:",
    re.IGNORECASE,
)
_JSON_FENCE = re.compile(r"\A\s*```(?:json)?\s*(\{.*\})\s*```\s*\Z", re.IGNORECASE | re.DOTALL)


class TurnContractError(ValueError):
    """A model result was not safe enough to cross the communication boundary."""


@dataclass(frozen=True, slots=True)
class ParsedTurn:
    draft: ConversationTurnDraft
    structured: bool


class StructuredTurnParser:
    """Parse compact decisions without exposing model-private material."""

    def parse(self, raw: str, *, legacy_delivery: DeliveryMode) -> ParsedTurn:
        candidate = (raw or "").strip()
        if not candidate:
            raise TurnContractError("empty_turn_result")
        if _PRIVATE_MARKERS.search(candidate):
            raise TurnContractError("private_marker_detected")

        fenced = _JSON_FENCE.match(candidate)
        if fenced:
            candidate = fenced.group(1).strip()
        if candidate.startswith("{"):
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError as exc:
                raise TurnContractError("malformed_structured_result") from exc
            try:
                draft = ConversationTurnDraft.model_validate(payload)
            except ValidationError as exc:
                raise TurnContractError("invalid_structured_result") from exc
            self._validate_decision(draft)
            self.validate_communicable_text(draft.final_text)
            return ParsedTurn(draft=draft, structured=True)

        # Temporary provider/API migration path: an ordinary plain-text reply is
        # treated as a reply draft. JSON-like planner output never enters it.
        if _PLANNER_KEYS.search(candidate) or re.match(r"\A\s*\[\s*\{", candidate):
            raise TurnContractError("planner_payload_detected")
        self.validate_communicable_text(candidate)
        return ParsedTurn(
            draft=ConversationTurnDraft(
                action="reply",
                requested_delivery=legacy_delivery,
                final_text=candidate,
                reason_code="legacy_plaintext",
            ),
            structured=False,
        )

    def validate_communicable_text(self, text: str) -> None:
        value = (text or "").strip()
        if _PRIVATE_MARKERS.search(value):
            raise TurnContractError("private_marker_in_final_text")
        if value.startswith("{") and _PLANNER_KEYS.search(value):
            raise TurnContractError("planner_json_in_final_text")
        if "```json" in value.lower() and _PLANNER_KEYS.search(value):
            raise TurnContractError("planner_json_in_final_text")

    def _validate_decision(self, draft: ConversationTurnDraft) -> None:
        text = draft.final_text.strip()
        if draft.action in {"stay_silent", "defer"} and text:
            raise TurnContractError("nonempty_text_for_noncommunicating_action")
        if draft.action == "stay_silent" and draft.requested_delivery != "silent":
            raise TurnContractError("silent_action_delivery_conflict")
        if draft.action == "defer" and draft.requested_delivery != "deferred":
            raise TurnContractError("defer_action_delivery_conflict")
        if draft.action in {"reply", "acknowledge"} and not text:
            raise TurnContractError("missing_final_text")
        if draft.action == "tool_first" and not draft.tool_calls:
            raise TurnContractError("tool_first_without_tools")
        if draft.action != "tool_first" and draft.tool_calls:
            raise TurnContractError("tools_require_tool_first")


def resolve_delivery(
    *,
    action: ResponseAction,
    requested: DeliveryMode,
    context: str,
    speech_requested: bool,
    speech_allowed: bool = True,
) -> DeliveryMode:
    """Resolve model preference under deterministic Gamma output policy."""
    if action == "stay_silent":
        return "silent"
    if action == "defer":
        return "deferred"
    if context == "audio_observation":
        return "silent" if requested in {"silent", "deferred"} else "text_only"
    if context == "presence_wake":
        if not speech_requested or not speech_allowed:
            return "text_only"
        return "speech" if requested == "speech" else requested
    if context in {"public_stream", "ambient"}:
        if not speech_requested or not speech_allowed:
            return "silent" if context != "public_stream" else "text_only"
        return "speech" if requested == "speech" else requested
    if context == "direct_voice":
        if requested == "text_only":
            return "text_only"
        return "speech" if speech_requested and speech_allowed else "text_only"
    # Direct text never speaks merely because the model requested it.
    return "text_only"


def structured_turn_instruction(*, default_delivery: DeliveryMode) -> str:
    return (
        "\n\n# Turn Decision Contract\n"
        "Return exactly one JSON object and no surrounding prose. Do not reveal or include chain-of-thought, "
        "private reasoning, scratchpad text, or hidden analysis. Make only a compact decision. Required keys: "
        '"action", "requested_delivery", "final_text", "internal_summary", "emotion", "voice_styles", '
        '"motions", "tool_calls", "memory_candidates", "state_updates", and "reason_code". '
        "action is reply, acknowledge, stay_silent, defer, or tool_first. requested_delivery is speech, "
        f"text_only, silent, or deferred; default to {default_delivery}. final_text is the only text that may be "
        "shown or spoken. internal_summary must be a short safe non-reasoning summary. state_updates may contain "
        "only emotion, active_topic, current_objective, deferred_intention, and relationship_signals. "
        "Use empty final_text for stay_silent, defer, or an unfinished tool_first decision."
    )
