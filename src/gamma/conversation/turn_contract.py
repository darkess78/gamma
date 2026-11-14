from __future__ import annotations

import json
import re
from typing import get_args
from dataclasses import dataclass

from pydantic import ValidationError

from ..schemas.response import ConversationTurnDraft, DeliveryMode, ResponseAction, EmotionTag


_PRIVATE_MARKERS = re.compile(
    r"<\s*/?\s*(?:think|analysis|reasoning|chain[-_ ]?of[-_ ]?thought)\b|"
    r"\b(?:private_reasoning|hidden_reasoning|scratchpad|chain[-_ ]?of[-_ ]?thought)\b|"
    r"\b(?:my|private|hidden)\s+(?:analysis|reasoning)\b",
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

    def parse(
        self, raw: str, *, legacy_delivery: DeliveryMode,
        draft_type: type[ConversationTurnDraft] = ConversationTurnDraft,
    ) -> ParsedTurn:
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
                draft = draft_type.model_validate(payload)
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
            draft=draft_type(
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
        if any(len(item) > 80 for item in [*draft.voice_styles, *draft.motions]):
            raise TurnContractError("presentation_hint_too_long")
        if any(len(json.dumps(call.args, ensure_ascii=False)) > 4000 for call in draft.tool_calls):
            raise TurnContractError("tool_args_too_large")
        if any(len(candidate.text) > 2000 for candidate in draft.memory_candidates):
            raise TurnContractError("memory_candidate_too_large")


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


def structured_turn_instruction(*, default_delivery: DeliveryMode, coordination: bool = False, compact: bool = False,
                                channel_observation: bool = False) -> str:
    instruction = (
        "\n\n# Turn Decision Contract\n"
        "Return exactly one JSON object and no surrounding prose. Do not reveal or include chain-of-thought, "
        "private reasoning, scratchpad text, or hidden analysis. Make only a compact decision. Required keys: "
        '"action", "requested_delivery", "final_text", "internal_summary", "emotion", "voice_styles", '
        '"motions", "tool_calls", "memory_candidates", "state_updates", and "reason_code". '
        "action is reply, acknowledge, stay_silent, defer, or tool_first. requested_delivery is speech, "
        f"text_only, silent, or deferred; default to {default_delivery}. final_text is the only text that may be "
        "shown or spoken. internal_summary must be a short safe non-reasoning summary. state_updates may contain "
        "only emotion, active_topic, current_objective, deferred_intention, and relationship_signals. "
        "Use empty final_text for stay_silent, defer, or an unfinished tool_first decision. "
        "All list fields must be JSON arrays, even when empty. Follow this exact value shape: "
        '{"action":"reply","requested_delivery":"'
        + default_delivery
        + '","final_text":"...","internal_summary":"safe summary","emotion":"neutral",'
        '"voice_styles":[],"motions":[],"tool_calls":[],"memory_candidates":[],"state_updates":'
        '{"emotion":null,"active_topic":null,"current_objective":null,"deferred_intention":null,'
        '"relationship_signals":[]},"reason_code":"reply"}.'
    )
    instruction += ('\nThe emotion field must be one of: ' + ', '.join(get_args(EmotionTag))
                    + '. state_updates.emotion uses the same labels or null. '
                    'Other tone descriptions belong in voice_styles, not emotion.')
    if coordination:
        instruction = instruction.replace('"emotion":"neutral"', '"emotion":null').replace(
            'The emotion field must be one of: ' + ', '.join(get_args(EmotionTag))
            + '. state_updates.emotion uses the same labels or null. '
              'Other tone descriptions belong in voice_styles, not emotion.',
            'The emotion field: null inherits Core mood or its update; labels choose one reply-wide delivery. '
            'Only state_updates.emotion changes mood; null keeps it. No hidden tags. Labels: '
            + ', '.join(get_args(EmotionTag)) + '.',
        )
        instruction = instruction.replace(
            'only emotion, active_topic, current_objective, deferred_intention, and relationship_signals.',
            'only schema fields.',
        ).replace('"relationship_signals":[]}', '"relationship_signals":[],"attention_focus":null,'
                  '"activity_action":"wait","activity_run_id":null,"practice_exercise":null,"work_task_id":null,"reconsider_after_seconds":300,"personal_changes":[],"growth_proposals":[],"voice_pace":null,"voice_loudness_db":null}')
        instruction += (
            '\nPrivate coordination: activity_action is wait, practice, request_practice, start_work, pause, resume, stop, switch, or report_status. '
            'Quiet reviews may choose permitted practice, offered exploration, exploration controls, or silent wait. Focus permits quiet work. '
            'Practice uses authorized_practice.exercise_ids/practice_exercise; request_practice queues it, notes never queue work. '
            'start_work requires exact work_task_id and an owner-preauthorized staged request or current finite exploration offer. '
            'Ready is unstarted. Discussion, research and ideas never authorize building; ask about missing inputs or scope. '
            'For progress or outcome questions, including how is it going or did that work, select report_status and exact activity_run_id. '
            'Controls require exact activity_run_id; switch pauses safely. Waiting work needs capacity; paused work needs resume. No added scope or invented ids. '
            'Capabilities use outer stay_silent, silent delivery, empty final_text; code renders receipts. Conversation uses reply, text_only, nonempty final_text, activity_action wait. Wait controls nothing. '
            'Fill every state_updates field. Unused ids and practice_exercise are null; tool_calls, memory_candidates, voice_styles and motions are []. '
            'attention_focus tracks established focused/relaxed changes. '
            'voice_pace .90..1.25 and voice_loudness_db -5..5dB are absolute, reset 1/0. '
            'Null keeps voice_delivery across turns/sentences; change only deliberately. Pitch/generation fixed. '
            '\npersonal_changes: at most three proposals, [] if none. Fields: op remember/correct/forget/resolve/hypothesis; '
            'kind fact/preference/decision/commitment/observation/relationship/hypothesis; subject_id owner/self/exact person or unresolved id. '
            'Remember concise stable input information, never transcripts or assumptions; reports remain unverified. '
            'Correct/forget/resolve need an existing target_id. Corrections and revised beliefs add counterevidence or update interpretations; preserve original experiences. '
            'Use forget only for an explicit owner request to forget/delete, never to correct a belief. Code binds sources and authority. '
            'No invented provenance, verified reports, permissions, account confirmation or identity edits. Hypotheses stay tentative. '
            'Administrative character edits are authored changes, not lived experiences. '
            'growth_proposals is [] or at most two tentative hypotheses with statement, kind judgment/interest/character/skill, '
            'scope_target dialogue/work, literal scope_marker, alternatives list and expected_outcome. '
            'They are source-bound proposals only. Never supply evidence ids, approve trials, edit personality or claim training through them.'
        )
    if channel_observation:
        if not coordination:
            raise ValueError('channel observations require private coordination')
        instruction = instruction.replace(
            'Capabilities use outer stay_silent, silent delivery, empty final_text; code renders receipts. '
            'Conversation uses reply, text_only, nonempty final_text, activity_action wait. Wait controls nothing. ',
            'For this channel observation choose reply, acknowledge, stay_silent or defer. '
            'A contribution uses text_only and nonempty final_text; silence or deferral uses empty final_text. '
            'Use activity_action wait; no tools, personal edits, growth proposals or other actions. '
            'This observation is not an owner utterance or a quiet-time work review. '
            'Your earlier commentary, intentions and promises are not execution evidence. '
            'Do not promise to run, edit, test or complete work from this observation. '
            'You may suggest an unexecuted next step conditionally or remain silent; this channel cannot dispatch it. '
            'Limit each completion or correctness claim to the exact supplied task, artifact and executed probe result. '
            'A completed check establishes only its concrete supplied inputs and outputs, not untested cases or broader goals. '
            'An omitted result is unknown, not missing or failed; inspect the supplied output before claiming it is absent. '
            'current_work_observations contains only parsed caller-supplied current-event data, not independent execution proof. '
            'Read its reported task labels and output_field_present values literally; earlier commentary cannot replace those labels or make a supplied JSON output missing. '
            'A reported status or check label is not independent verification. Distinguish supplied output from whether its correctness has been independently checked. '
            'When current task records are supplied, unbound prior commentary text is deliberately omitted for this review; its receipt metadata remains. '
            'This omission does not establish missing execution or failed work. Use the current supplied records for reported outcomes, without substituting earlier broad-plan stages or assuming unseen steps are unfinished. '
            'If earlier commentary conflicts with current supplied observations, correct the earlier claim; repetition is not corroboration. ')
    if compact:
        if not coordination:
            raise ValueError('compact serialization requires private coordination')
        instruction=instruction.replace(
            '\"action\", \"requested_delivery\", \"final_text\", \"internal_summary\", \"emotion\", \"voice_styles\", '
            '\"motions\", \"tool_calls\", \"memory_candidates\", \"state_updates\", and \"reason_code\".',
            '\"action\", \"requested_delivery\", \"final_text\", \"emotion\", \"state_updates\", and \"reason_code\".')
        start=instruction.index('Follow this exact value shape: ')
        end=instruction.index('\nThe emotion field',start)
        instruction=(instruction[:start]+'For unchanged state use {\"activity_action\":\"wait\"}. '
            'Omit empty optional lists and unused null fields. The typed parser supplies their existing defaults. '
            'Include any meaningful bounded state, personal or growth proposal explicitly. '+instruction[end:])
        instruction=instruction.replace(
            'Fill every state_updates field. Unused ids and practice_exercise are null; tool_calls, memory_candidates, voice_styles and motions are []. ',
            'state_updates requires activity_action. Omit unused fields; empty lists, null ids and all other defaults remain unchanged. '
            'tool_calls, memory_candidates, voice_styles and motions have no authority and remain omitted or []. ')
    return instruction
