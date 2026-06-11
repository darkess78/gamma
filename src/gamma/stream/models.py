from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from ..schemas.conversation import SpeakerContext
from ..schemas.response import AssistantResponse


def utc_now() -> str:
    """Return current UTC time as ISO 8601 formatted string.
    
    Returns:
        str: Current UTC timestamp in "YYYY-MM-DDTHH:MM:SZ" format.
    """
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


StreamEventKind = Literal[
    "mic_transcript",
    "owner_command",
    "chat_message",
    "follow",
    "raid",
    "moderator_action",
    "donation",
    "bits",
    "subscription",
    "redeem",
    "game_state",
    "conversation_lull",
    "system",
]
TurnDecisionKind = Literal["reply", "acknowledge", "ignore", "defer", "tool_action", "moderation_escalation"]
StreamOutputEventType = Literal[
    "subtitle_line",
    "speech_started",
    "speech_chunk",
    "speech_ended",
    "emotion_changed",
    "avatar_motion",
    "obs_command",
    "overlay_update",
]
ActionRiskTier = Literal["none", "low", "medium", "high", "critical"]
ActionPlanStatus = Literal["planned", "approved", "blocked", "executed", "failed", "skipped"]


class StreamActor(BaseModel):
    source: str = "local"
    platform_id: str | None = None
    display_name: str | None = None
    roles: list[str] = Field(default_factory=list)

    def to_speaker_context(self) -> SpeakerContext:
        """Convert actor to speaker context for identity resolution.
        
        Returns:
            SpeakerContext: Speaker context with source and platform info.
        """
        return SpeakerContext(source=self.source, platform_id=self.platform_id)


class StreamInputEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    kind: StreamEventKind
    text: str | None = None
    actor: StreamActor = Field(default_factory=StreamActor)
    session_id: str | None = None
    occurred_at: str = Field(default_factory=utc_now)
    priority: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class TurnDecision(BaseModel):
    decision: TurnDecisionKind
    reason: str
    should_call_conversation: bool = False
    response_mode: str = "none"
    requires_moderation_review: bool = False
    deferred_until: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActionPlanItem(BaseModel):
    action_id: str = Field(default_factory=lambda: uuid4().hex)
    action_type: str
    args: dict[str, Any] = Field(default_factory=dict)
    risk_tier: ActionRiskTier = "none"
    requires_approval: bool = False
    status: ActionPlanStatus = "planned"
    audit_reason: str | None = None
    result: dict[str, Any] | None = None


class ActionPlan(BaseModel):
    items: list[ActionPlanItem] = Field(default_factory=list)


class StreamOutputEvent(BaseModel):
    output_event_id: str = Field(default_factory=lambda: uuid4().hex)
    input_event_id: str
    turn_id: str
    type: StreamOutputEventType
    occurred_at: str = Field(default_factory=utc_now)
    payload: dict[str, Any] = Field(default_factory=dict)


class StreamTurnResult(BaseModel):
    input_event: StreamInputEvent
    decision: TurnDecision
    action_plan: ActionPlan = Field(default_factory=ActionPlan)
    assistant_response: AssistantResponse | None = None
    output_events: list[StreamOutputEvent] = Field(default_factory=list)
    output_dispatch: dict[str, Any] = Field(default_factory=dict)
    safety_decision: dict[str, Any] = Field(default_factory=dict)
    trace_id: str = Field(default_factory=lambda: uuid4().hex)
    timing_ms: dict[str, float] = Field(default_factory=dict)


def output_events_from_response(
    *,
    input_event: StreamInputEvent,
    turn_id: str,
    response: AssistantResponse,
) -> list[StreamOutputEvent]:
    """Build output events from an assistant response.
    
    Creates subtitle, speech, emotion, and motion events based on the response.
    
    Args:
        input_event: The original input event that triggered the response.
        turn_id: Unique identifier for this turn.
        response: AssistantResponse object with spoken text and metadata.
        
    Returns:
        list[StreamOutputEvent]: List of output events matching the response.
    """
    context = output_context_from_input(input_event)
    events = [
        StreamOutputEvent(
            input_event_id=input_event.event_id,
            turn_id=turn_id,
            type="emotion_changed",
            payload={"emotion": response.emotion, **context},
        ),
        StreamOutputEvent(
            input_event_id=input_event.event_id,
            turn_id=turn_id,
            type="subtitle_line",
            payload={"text": response.spoken_text, **context},
        ),
    ]
    if response.audio_path or response.audio_content_type:
        events.insert(
            1,
            StreamOutputEvent(
                input_event_id=input_event.event_id,
                turn_id=turn_id,
                type="speech_started",
                payload={
                    "audio_path": response.audio_path,
                    "audio_content_type": response.audio_content_type,
                    **context,
                },
            ),
        )
        events.append(
            StreamOutputEvent(
                input_event_id=input_event.event_id,
                turn_id=turn_id,
                type="speech_ended",
                payload={"audio_path": response.audio_path, **context},
            )
        )
    for motion in response.motions:
        events.append(
            StreamOutputEvent(
                input_event_id=input_event.event_id,
                turn_id=turn_id,
                type="avatar_motion",
                payload={"motion": motion, **context},
            )
        )
    return events


def output_context_from_input(input_event: StreamInputEvent) -> dict[str, Any]:
    """Extract output context from an input event.
    
    Args:
        input_event: StreamInputEvent with event and actor data.
        
    Returns:
        dict[str, Any]: Context dict with input kind, event_id, session_id,
            and actor information (source, platform_id, display_name, roles).
    """
    return {
        "input": {
            "kind": input_event.kind,
            "event_id": input_event.event_id,
            "session_id": input_event.session_id,
        },
        "actor": {
            "source": input_event.actor.source,
            "platform_id": input_event.actor.platform_id,
            "display_name": input_event.actor.display_name,
            "roles": list(input_event.actor.roles),
        },
    }
