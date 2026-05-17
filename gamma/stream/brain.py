from __future__ import annotations

import time
from uuid import uuid4

from ..conversation.service import ConversationService
from ..errors import ConversationError
from ..schemas.response import AssistantResponse
from .actions import ActionPlanner
from .models import (
    ActionPlan,
    StreamActor,
    StreamInputEvent,
    StreamOutputEvent,
    StreamTurnResult,
    TurnDecision,
    output_events_from_response,
)
from .output import StreamOutputDispatcher
from .trace import StreamTraceStore


DEFAULT_STREAM_SPEECH_GAP_SECONDS = 5.0
HIGH_PRIORITY_BYPASS_THRESHOLD = 20


class StreamBrain:
    """Policy boundary between public/live events and the conversation service."""

    def __init__(
        self,
        *,
        conversation: ConversationService | None = None,
        trace_store: StreamTraceStore | None = None,
        action_planner: ActionPlanner | None = None,
        output_dispatcher: StreamOutputDispatcher | None = None,
    ) -> None:
        self._conversation = conversation or ConversationService()
        self._trace_store = trace_store or StreamTraceStore()
        self._action_planner = action_planner or ActionPlanner()
        self._output_dispatcher = output_dispatcher or StreamOutputDispatcher()
        self._pacer = StreamSpeechPacer()

    def handle_event(
        self,
        event: StreamInputEvent,
        *,
        synthesize_speech: bool = False,
        fast_mode: bool = True,
        brief_mode: bool = False,
        micro_mode: bool = False,
    ) -> StreamTurnResult:
        started_at = time.perf_counter()
        decision = self.decide(event)
        response = None
        output_events = []
        action_plan = ActionPlan()
        turn_id = uuid4().hex

        if _twitch_control_enabled(event, "dry_run", False) and _would_twitch_dry_run_suppress(decision):
            decision = TurnDecision(
                decision="defer",
                reason="twitch_dry_run_suppressed_output",
                should_call_conversation=False,
                response_mode=decision.response_mode,
                requires_moderation_review=decision.requires_moderation_review,
                metadata={
                    **decision.metadata,
                    "dry_run": True,
                    "would_decision": decision.decision,
                    "would_reason": decision.reason,
                    "would_call_conversation": decision.should_call_conversation,
                    "would_emit_canned_response": bool(_canned_response_from_decision(decision)),
                },
            )

        decision = self._pacer.apply(event, decision)
        canned_response = _canned_response_from_decision(decision)
        if canned_response:
            response = AssistantResponse(
                spoken_text=canned_response,
                emotion=str(decision.metadata.get("canned_emotion") or "teasing"),
            )
            output_events = output_events_from_response(input_event=event, turn_id=turn_id, response=response)
        elif decision.should_call_conversation:
            text = (event.text or "").strip()
            if not text:
                raise ConversationError("stream event text must not be empty for reply decisions.")
            response = self._conversation.respond(
                user_text=text,
                session_id=event.session_id,
                synthesize_speech=synthesize_speech,
                speaker_ctx=event.actor.to_speaker_context(),
                fast_mode=fast_mode,
                brief_mode=brief_mode,
                micro_mode=micro_mode,
            )
            output_events = output_events_from_response(input_event=event, turn_id=turn_id, response=response)
            action_plan = self._action_planner.plan_from_response(response)
        output_dispatch = self._output_dispatcher.dispatch(output_events) if output_events else None

        result = StreamTurnResult(
            input_event=event,
            decision=decision,
            action_plan=action_plan,
            assistant_response=response,
            output_events=output_events,
            output_dispatch=output_dispatch.model_dump() if output_dispatch else {},
            timing_ms={"stream_brain_ms": round((time.perf_counter() - started_at) * 1000, 1)},
        )
        self._trace_store.append(result)
        return result

    def record_result(self, result: StreamTurnResult) -> None:
        if result.output_events and not result.output_dispatch:
            result.output_dispatch = self._output_dispatcher.dispatch(result.output_events).model_dump()
        self._trace_store.append(result)

    def pending_queue(self) -> dict:
        return self._pacer.pending_snapshot()

    def stop_stream(self, *, reason: str = "operator_stop") -> StreamTurnResult:
        started_at = time.perf_counter()
        self._pacer.clear_pending()
        input_event = StreamInputEvent(
            kind="system",
            text="Stop stream speech requested.",
            actor=StreamActor(source="dashboard", roles=["operator"]),
            metadata={"control": "stop_shana", "reason": reason},
        )
        turn_id = uuid4().hex
        output_events = [
            StreamOutputEvent(
                input_event_id=input_event.event_id,
                turn_id=turn_id,
                type="speech_ended",
                payload={"reason": reason, "interrupted": True, "clear_pending": True},
            ),
            StreamOutputEvent(
                input_event_id=input_event.event_id,
                turn_id=turn_id,
                type="subtitle_line",
                payload={"text": "", "clear": True},
            ),
            StreamOutputEvent(
                input_event_id=input_event.event_id,
                turn_id=turn_id,
                type="overlay_update",
                payload={"target": "subtitles", "text": "", "clear": True},
            ),
        ]
        output_dispatch = self._output_dispatcher.dispatch(output_events)
        result = StreamTurnResult(
            input_event=input_event,
            decision=TurnDecision(
                decision="ignore",
                reason="stream_stop_requested",
                metadata={"reason": reason, "cleared_pending_queue": True},
            ),
            output_events=output_events,
            output_dispatch=output_dispatch.model_dump(),
            timing_ms={"stream_brain_ms": round((time.perf_counter() - started_at) * 1000, 1)},
        )
        self._trace_store.append(result)
        return result

    def decide(self, event: StreamInputEvent) -> TurnDecision:
        text = (event.text or "").strip()
        lowered = text.lower()
        input_safety = _input_safety(event)
        safety_category = str(input_safety.get("category") or "")

        if event.kind == "moderator_action":
            return TurnDecision(
                decision="moderation_escalation",
                reason="moderator_action_events_are_policy_inputs",
                requires_moderation_review=True,
                metadata={"event_kind": event.kind},
            )
        if event.kind == "game_state":
            return TurnDecision(decision="defer", reason="game_state_not_connected_yet", metadata={"event_kind": event.kind})
        if event.kind == "conversation_lull":
            idle_policy_decision = str(event.metadata.get("idle_policy_decision") or "defer")
            would_reply = idle_policy_decision in {"reply", "topic_shift", "check_in"}
            return TurnDecision(
                decision="defer" if would_reply else "ignore",
                reason="proactive_idle_dry_run_would_reply" if would_reply else "proactive_idle_policy_suppressed",
                should_call_conversation=False,
                response_mode="proactive_live",
                deferred_until=event.metadata.get("next_check_at"),
                metadata={
                    "event_kind": event.kind,
                    "dry_run": True,
                    "would_reply": would_reply,
                    "idle_policy_decision": idle_policy_decision,
                    "idle_policy_reason": event.metadata.get("idle_policy_reason"),
                    "speech_enabled": False,
                },
            )
        if event.kind == "system":
            return TurnDecision(decision="ignore", reason="system_events_are_not_conversation_turns", metadata={"event_kind": event.kind})
        if not text:
            return TurnDecision(decision="ignore", reason="empty_event_text", metadata={"event_kind": event.kind})
        if event.kind == "owner_command":
            return TurnDecision(
                decision="reply",
                reason="owner_command_requires_direct_reply",
                should_call_conversation=True,
                response_mode="direct",
            )
        if any(marker in lowered for marker in ["ban ", "timeout ", "delete message", "mod review"]):
            return TurnDecision(
                decision="moderation_escalation",
                reason="event_mentions_moderation_action",
                requires_moderation_review=True,
                metadata={"event_kind": event.kind},
            )
        if event.kind == "mic_transcript":
            return TurnDecision(
                decision="reply",
                reason="mic_transcripts_are_direct_turns",
                should_call_conversation=True,
                response_mode="spoken",
            )
        if event.kind in {"donation", "redeem", "follow"}:
            return TurnDecision(
                decision="acknowledge",
                reason="support_events_receive_acknowledgement",
                should_call_conversation=True,
                response_mode="brief_ack",
            )
        if event.kind == "chat_message":
            if input_safety.get("should_drop"):
                return TurnDecision(
                    decision="ignore",
                    reason=f"twitch_input_dropped_{safety_category or 'unsafe'}",
                    metadata={"event_kind": event.kind, "input_safety": input_safety},
                )
            if safety_category == "spam_or_scam":
                if _twitch_control_enabled(event, "spam_quips_enabled", True):
                    return TurnDecision(
                        decision="acknowledge",
                        reason="twitch_spam_quip_allowed",
                        response_mode="spam_quip",
                        metadata={
                            "event_kind": event.kind,
                            "input_safety": input_safety,
                            "canned_response": "Nice try. I am not buying views from your bargain-bin website.",
                            "canned_emotion": "teasing",
                        },
                    )
                return TurnDecision(
                    decision="ignore",
                    reason="twitch_spam_quips_disabled",
                    metadata={"event_kind": event.kind, "input_safety": input_safety},
                )
            if safety_category == "prompt_injection":
                return TurnDecision(
                    decision="ignore",
                    reason="twitch_prompt_injection_summarized",
                    metadata={"event_kind": event.kind, "input_safety": input_safety},
                )
            direct_mention = "gamma" in lowered or "shana" in lowered
            if direct_mention and not _twitch_control_enabled(event, "mention_replies_enabled", True):
                return TurnDecision(
                    decision="ignore",
                    reason="twitch_mention_replies_disabled",
                    metadata={"event_kind": event.kind, "input_safety": input_safety},
                )
            if direct_mention or event.priority >= 5:
                if not direct_mention and not _twitch_control_enabled(event, "ambient_chat_enabled", True):
                    return TurnDecision(
                        decision="ignore",
                        reason="twitch_ambient_chat_disabled",
                        metadata={"event_kind": event.kind, "input_safety": input_safety},
                    )
                return TurnDecision(
                    decision="reply",
                    reason="chat_message_addresses_assistant_or_has_priority",
                    should_call_conversation=True,
                    response_mode="chat",
                )
            return TurnDecision(decision="ignore", reason="ambient_chat_not_addressed_to_gamma", metadata={"event_kind": event.kind})

        return TurnDecision(decision="ignore", reason="unhandled_event_kind", metadata={"event_kind": event.kind})


class StreamSpeechPacer:
    def __init__(self, *, now=time.monotonic, default_min_gap_seconds: float = DEFAULT_STREAM_SPEECH_GAP_SECONDS) -> None:
        self._now = now
        self._default_min_gap_seconds = default_min_gap_seconds
        self._last_spoken_at: float | None = None
        self._pending: dict[str, dict] = {}

    def apply(self, event: StreamInputEvent, decision: TurnDecision) -> TurnDecision:
        if not _decision_would_speak(decision):
            return decision
        if not _is_paced_stream_event(event):
            self.mark_spoken()
            return decision
        min_gap = _stream_min_gap_seconds(event, self._default_min_gap_seconds)
        now = self._now()
        if self._last_spoken_at is not None:
            elapsed = now - self._last_spoken_at
            if elapsed < min_gap and event.priority < HIGH_PRIORITY_BYPASS_THRESHOLD:
                pending = self._store_pending(event=event, decision=decision, min_gap=min_gap, elapsed=elapsed)
                return TurnDecision(
                    decision="defer",
                    reason="stream_speech_pacing_deferred",
                    should_call_conversation=False,
                    response_mode=decision.response_mode,
                    requires_moderation_review=decision.requires_moderation_review,
                    metadata={
                        **decision.metadata,
                        "would_decision": decision.decision,
                        "would_reason": decision.reason,
                        "min_gap_seconds": min_gap,
                        "elapsed_seconds": round(elapsed, 3),
                        "priority": event.priority,
                        "pending_slot": pending["slot"],
                        "replaced_event_id": pending.get("replaced_event_id"),
                    },
                )
        self.mark_spoken(now)
        return decision

    def mark_spoken(self, now: float | None = None) -> None:
        self._last_spoken_at = self._now() if now is None else now

    def pending_snapshot(self) -> dict:
        return {
            "updated_at": _utc_now(),
            "slots": {
                slot: dict(item)
                for slot, item in sorted(self._pending.items())
            },
        }

    def clear_pending(self) -> None:
        self._pending.clear()

    def _store_pending(self, *, event: StreamInputEvent, decision: TurnDecision, min_gap: float, elapsed: float) -> dict:
        slot = _pending_slot(event)
        replaced = self._pending.get(slot)
        item = {
            "slot": slot,
            "queued_at": _utc_now(),
            "event_id": event.event_id,
            "kind": event.kind,
            "text": event.text,
            "priority": event.priority,
            "actor": event.actor.model_dump(),
            "decision": decision.decision,
            "reason": decision.reason,
            "response_mode": decision.response_mode,
            "min_gap_seconds": min_gap,
            "elapsed_seconds": round(elapsed, 3),
        }
        if replaced:
            item["replaced_event_id"] = replaced.get("event_id")
        self._pending[slot] = item
        return item


def _input_safety(event: StreamInputEvent) -> dict:
    value = event.metadata.get("input_safety")
    return value if isinstance(value, dict) else {}


def _twitch_control_enabled(event: StreamInputEvent, key: str, default: bool) -> bool:
    controls = event.metadata.get("twitch_controls")
    if not isinstance(controls, dict) or key not in controls:
        return default
    return bool(controls[key])


def _stream_min_gap_seconds(event: StreamInputEvent, default: float) -> float:
    controls = event.metadata.get("twitch_controls")
    if not isinstance(controls, dict):
        return default
    raw_value = controls.get("min_speech_gap_seconds")
    if raw_value is None:
        return default
    try:
        return max(0.0, float(raw_value))
    except (TypeError, ValueError):
        return default


def _canned_response_from_decision(decision: TurnDecision) -> str | None:
    value = decision.metadata.get("canned_response")
    if not value:
        return None
    return str(value).strip() or None


def _would_twitch_dry_run_suppress(decision: TurnDecision) -> bool:
    if decision.decision in {"ignore", "moderation_escalation"}:
        return False
    return decision.should_call_conversation or bool(_canned_response_from_decision(decision))


def _decision_would_speak(decision: TurnDecision) -> bool:
    if decision.decision not in {"reply", "acknowledge"}:
        return False
    return decision.should_call_conversation or bool(_canned_response_from_decision(decision))


def _is_paced_stream_event(event: StreamInputEvent) -> bool:
    if event.actor.source != "twitch":
        return False
    if event.kind in {"chat_message", "follow", "donation", "redeem"}:
        return True
    return False


def _pending_slot(event: StreamInputEvent) -> str:
    if event.kind in {"follow", "donation", "redeem"} or event.priority >= 10:
        return "high_priority"
    return "ambient"


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
