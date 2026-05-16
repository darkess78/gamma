from __future__ import annotations

import time
from uuid import uuid4

from ..conversation.service import ConversationService
from ..errors import ConversationError
from .actions import ActionPlanner
from .models import (
    ActionPlan,
    StreamInputEvent,
    StreamTurnResult,
    TurnDecision,
    output_events_from_response,
)
from .output import StreamOutputDispatcher
from .trace import StreamTraceStore


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

        if decision.should_call_conversation:
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

    def decide(self, event: StreamInputEvent) -> TurnDecision:
        text = (event.text or "").strip()
        lowered = text.lower()

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
        if event.kind in {"donation", "redeem"}:
            return TurnDecision(
                decision="acknowledge",
                reason="support_events_receive_acknowledgement",
                should_call_conversation=True,
                response_mode="brief_ack",
            )
        if event.kind == "chat_message":
            if event.priority > 0 or "gamma" in lowered:
                return TurnDecision(
                    decision="reply",
                    reason="chat_message_addresses_gamma_or_has_priority",
                    should_call_conversation=True,
                    response_mode="chat",
                )
            return TurnDecision(decision="ignore", reason="ambient_chat_not_addressed_to_gamma", metadata={"event_kind": event.kind})

        return TurnDecision(decision="ignore", reason="unhandled_event_kind", metadata={"event_kind": event.kind})
