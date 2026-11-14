from __future__ import annotations

import re
import time
from concurrent.futures import Future
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol
from uuid import uuid4

from ..conversation.service import ConversationService
from ..errors import ConversationError
from ..safety.llm_reviewer import SpeechLLMReviewer
from ..integrations.twitch.sanitize import safe_username_alias
from ..memory.relationships import RelationshipStore, relationship_key, shared_line
from ..memory.viewer_memory import ViewerMemory, viewer_key
from ..schemas.conversation import RecipientContext
from ..schemas.response import AssistantResponse
from ..voice.tts import TTSResult, TTSService
from .actions import ActionPlanner
from .event_rules import _canned_response_from_decision, _decision_would_speak, _estimate_speech_seconds, _input_safety, _is_public_stream_event, _twitch_control_enabled, _would_twitch_dry_run_suppress
from .models import (
    ActionPlan,
    StreamActor,
    StreamInputEvent,
    StreamOutputEvent,
    StreamTurnResult,
    TurnDecision,
    output_context_from_input,
    output_events_from_response,
)
from .output import StreamOutputDispatcher
from .output_events import _apply_estimated_subtitle_timing, _apply_output_target_policy, _filter_stream_output_events, _mark_filtered_output_events
from .pacing import StreamSpeechPacer
from .publication_review import PublicationReviewMixin
from .safety_rules import StreamSafetyReviewer, _filtered_stream_response, _parallel_stream_safety_enabled
from .self_goals import StreamSelfGoalStore
from .temp_memory import RecentStreamContextAssembler, StreamTempMemoryStore
from .trace import StreamTraceStore


class StreamSpeechSynthesizer(Protocol):
    """Protocol for stream speech synthesizers.
    
    Methods:
        synthesize: Synthesize text to speech.
    """

    def synthesize(self, text: str, emotion: str | None = None, styles: list[str] | None = None) -> TTSResult:
        ...


class StreamBrain(PublicationReviewMixin):
    """Stream brain orchestrating policy, safety, and output events.
    
    Attributes:
        _conversation: ConversationService instance.
        _trace_store: StreamTraceStore instance.
        _action_planner: ActionPlanner instance.
        _output_dispatcher: StreamOutputDispatcher instance.
        _pacer: StreamSpeechPacer instance.
        _safety_reviewer: StreamSafetyReviewer instance.
        _speech_synthesizer: StreamSpeechSynthesizer instance.
        _temp_memory_store: StreamTempMemoryStore instance.
        _self_goal_store: StreamSelfGoalStore instance.
    
    Methods:
        __init__: Initialize StreamBrain.
        handle_event: Handle stream input events.
        decide: Make turn decision.
        _review_stream_output: Review output stream safety.
        _synthesize_with_parallel_review: Synthesize with parallel review.
        _pacer.apply: Apply speech pacer.
        _pacer.apply_budget: Apply budget decision.
        _maybe_propose_self_goal: Propose self goal.
        save_output_event: Save output event.
        get_output_events: Get output events.
        stream_self_goal_id: Self goal ID.
        _stream_self_goal_approved: Check approved self goal.
        _stream_self_goal_ignored: Check ignored self goal.
    """

    def __init__(
        self,
        *,
        conversation: ConversationService | None = None,
        trace_store: StreamTraceStore | None = None,
        action_planner: ActionPlanner | None = None,
        output_dispatcher: StreamOutputDispatcher | None = None,
        safety_reviewer: StreamSafetyReviewer | None = None,
        speech_synthesizer: StreamSpeechSynthesizer | None = None,
        temp_memory_store: StreamTempMemoryStore | None = None,
        self_goal_store: StreamSelfGoalStore | None = None,
    ) -> None:
        """Initialize StreamBrain.
        
        Args:
            conversation: Optional ConversationService (default ConversationService).
            trace_store: Optional StreamTraceStore (default StreamTraceStore).
            action_planner: Optional ActionPlanner (default ActionPlanner).
            output_dispatcher: Optional StreamOutputDispatcher (default StreamOutputDispatcher).
            safety_reviewer: Optional StreamSafetyReviewer (default SpeechLLMReviewer).
            speech_synthesizer: Optional StreamSpeechSynthesizer.
            temp_memory_store: Optional StreamTempMemoryStore (default StreamTempMemoryStore).
            self_goal_store: Optional StreamSelfGoalStore (default StreamSelfGoalStore).
        """
        self._conversation = conversation or ConversationService()
        self._trace_store = trace_store or StreamTraceStore()
        self._action_planner = action_planner or ActionPlanner()
        self._output_dispatcher = output_dispatcher or StreamOutputDispatcher()
        self._pacer = StreamSpeechPacer()
        self._safety_reviewer = safety_reviewer or SpeechLLMReviewer()
        self._speech_synthesizer = speech_synthesizer
        self._temp_memory_store = temp_memory_store or StreamTempMemoryStore()
        self._recent_context = RecentStreamContextAssembler(self._temp_memory_store)
        self._self_goal_store = self_goal_store or StreamSelfGoalStore()

    @staticmethod
    def _recipient_for_event(event: StreamInputEvent, *, owner_channel: bool = False) -> RecipientContext:
        target = str(event.metadata.get("output_target_policy") or "").strip().lower() or None
        if target not in {None, "dashboard_monitor", "stream_public", "discord_call"}:
            raise ConversationError("unknown_recipient_output_target")
        shared = (
            target in {"stream_public", "discord_call"}
            or event.actor.source.strip().lower() in {"twitch", "discord"}
            or _is_public_stream_event(event)
        )
        if not shared and _is_owner_private_event(event, owner_channel=owner_channel):
            return RecipientContext(kind="owner_private", target_policy=target, owner_authorized=True)
        if shared and target is None and event.actor.source.strip().lower() == "twitch":
            target = "stream_public"  # Twitch replies go to the stream audience
        return RecipientContext(kind="shared" if shared else "unknown", target_policy=target)

    def handle_event(
        self,
        event: StreamInputEvent,
        *,
        synthesize_speech: bool = False,
        fast_mode: bool = True,
        brief_mode: bool = False,
        micro_mode: bool = False,
        owner_channel: bool = False,
        chat_summary: str | None = None,
    ) -> StreamTurnResult:
        """owner_channel: the API verified the request came from the owner's dashboard.

        chat_summary: a summary of the other recent chat messages, supplied by the
        chat-window picker (never read from the event body, which callers control).
        """
        try:
            return self._handle_event(
                event,
                synthesize_speech=synthesize_speech,
                fast_mode=fast_mode,
                brief_mode=brief_mode,
                micro_mode=micro_mode,
                owner_channel=owner_channel,
                chat_summary=chat_summary,
            )
        except BaseException:
            self._pacer.release(event.event_id)
            raise

    def _handle_event(
        self,
        event: StreamInputEvent,
        *,
        synthesize_speech: bool,
        fast_mode: bool,
        brief_mode: bool,
        micro_mode: bool,
        owner_channel: bool,
        chat_summary: str | None,
    ) -> StreamTurnResult:
        started_at = time.perf_counter()
        decision = self.decide(event)
        response = None
        output_events = []
        action_plan = ActionPlan()
        turn_id = uuid4().hex
        late_review: tuple[Future, ThreadPoolExecutor] | None = None

        dry_run_enabled = _twitch_control_enabled(event, "dry_run", False)
        if dry_run_enabled and _would_twitch_dry_run_suppress(decision):
            decision = TurnDecision(
                decision=decision.decision,
                reason=decision.reason,
                should_call_conversation=decision.should_call_conversation,
                response_mode=decision.response_mode,
                requires_moderation_review=decision.requires_moderation_review,
                metadata={
                    **decision.metadata,
                    "dry_run": True,
                    "dry_run_voice_suppressed": True,
                    "would_decision": decision.decision,
                    "would_reason": decision.reason,
                    "would_call_conversation": decision.should_call_conversation,
                    "would_emit_canned_response": bool(_canned_response_from_decision(decision)),
                },
            )
            synthesize_speech = False

        decision = self._pacer.apply(event, decision)
        decision = self._pacer.budget_exhausted(event, decision) or decision
        canned_response = _canned_response_from_decision(decision)
        parallel_safety = _parallel_stream_safety_enabled(event, synthesize_speech=synthesize_speech)
        if canned_response:
            response = AssistantResponse(
                spoken_text=canned_response,
                emotion=str(decision.metadata.get("canned_emotion") or "teasing"),
            )
        elif decision.should_call_conversation:
            text = (event.text or "").strip()
            if not text:
                raise ConversationError("stream event text must not be empty for reply decisions.")
            recipient = self._recipient_for_event(event, owner_channel=owner_channel)
            owner_private = recipient.allows_private_memory
            conversation_args = {
                "user_text": text,
                "session_id": event.session_id,
                "synthesize_speech": synthesize_speech and not parallel_safety,
                "speaker_ctx": event.actor.to_speaker_context(),
                "fast_mode": fast_mode,
                "brief_mode": brief_mode,
                "micro_mode": micro_mode,
                "defer_llm_safety_review": _is_public_stream_event(event),
                "delivery_context": (
                    ("direct_voice" if synthesize_speech else "direct_text") if owner_private
                    else "ambient" if event.kind in {"conversation_lull", "audio_event", "system"}
                    else "public_stream"
                ),
                "speech_allowed": synthesize_speech,
                "speech_requested": synthesize_speech,
                "recipient": recipient,
                "owner_verified": owner_channel and event.actor.source.strip().lower() == "local_monitor",
                "speaker_label": _speaker_label(event),
                "viewer_notes": self._viewer_notes_for(event, recipient),
            }
            relationship = self._relationship_target(event, recipient)
            if relationship:
                conversation_args["relationship_note"] = self._relationship_note(event, *relationship)
            if _is_public_stream_event(event) and event.actor.source.strip().lower() != "discord":
                recent_context = self._assemble_recent_context()
                stream_context = "\n\n".join(part for part in (recent_context, (chat_summary or "").strip()) if part)
                if stream_context:
                    conversation_args["stream_context"] = stream_context
            response = self._conversation.respond(**conversation_args)
            self._remember_viewer(event, recipient)
            if relationship:
                self._record_relationship(event, *relationship)
            action_plan = self._action_planner.plan_from_response(response)
        safety_decision = self._review_stream_output(
            event, response, include_llm=not (parallel_safety and response.speech_text)
        ) if response else {}
        if response is not None and response.speech_text and parallel_safety and not safety_decision.get("blocked"):
            response, safety_decision, late_review = self._synthesize_with_parallel_review(event, response)
        if safety_decision.get("blocked"):
            response = _filtered_stream_response()
            action_plan = ActionPlan()
        elif safety_decision.get("action") in {"skip", "defer", "hold"}:
            safety_action = str(safety_decision.get("action") or "skip")
            decision = TurnDecision(
                decision="defer",
                reason="stream_safety_review_timeout" if safety_decision.get("review_timeout") else "stream_safety_review_skipped",
                should_call_conversation=False,
                response_mode=decision.response_mode,
                metadata={
                    **decision.metadata,
                    "safety_action": safety_action,
                    "review_timeout": bool(safety_decision.get("review_timeout")),
                },
            )
            response = None
            action_plan = ActionPlan()
        budget_decision = self._pacer.apply_budget(event, decision, response.speech_text if response else "")
        if budget_decision is not None:
            decision = budget_decision
            response = None
            action_plan = ActionPlan()
            output_events = []
        elif response is not None:
            output_events = output_events_from_response(input_event=event, turn_id=turn_id, response=response)
            if safety_decision.get("blocked"):
                output_events = _mark_filtered_output_events(output_events, safety_decision)
            else:
                output_events = _apply_estimated_subtitle_timing(event, output_events)
            output_events = _apply_output_target_policy(event, output_events)
        output_events = _filter_stream_output_events(event, output_events)
        output_dispatch = self._output_dispatcher.dispatch(output_events) if output_events else None
        spoken_text = _dispatched_speech_text(output_events)
        if _decision_would_speak(decision) and spoken_text is not None:
            self._pacer.confirm_spoken(event.event_id, speech_seconds=_estimate_speech_seconds(spoken_text))
        else:
            self._pacer.release(event.event_id)
        if decision.decision == "defer" and decision.metadata.get("pending_slot"):
            self._pacer.remember_pending_options(
                event.event_id, {"synthesize_speech": synthesize_speech, "fast_mode": fast_mode},
            )
        if chat_summary:
            decision.metadata.setdefault("chat_window_summary", chat_summary)

        result = StreamTurnResult(
            input_event=event,
            decision=decision,
            action_plan=action_plan,
            assistant_response=response,
            output_events=output_events,
            output_dispatch=output_dispatch.model_dump() if output_dispatch else {},
            safety_decision=safety_decision,
            timing_ms={"stream_brain_ms": round((time.perf_counter() - started_at) * 1000, 1)},
        )
        self._maybe_propose_self_goal(event, result)
        self._record_temp_memory(result)
        self._trace_store.append(result)
        if late_review is not None:
            self._arm_late_safety_interrupt(
                event=event,
                turn_id=turn_id,
                review_future=late_review[0],
                executor=late_review[1],
            )
        return result

    def _viewer_memory(self):
        engine = getattr(getattr(self._conversation, "_memory", None), "_engine", None)
        return ViewerMemory(engine) if engine is not None else None

    def _viewer_notes_for(self, event: StreamInputEvent, recipient) -> list[str]:
        audience = _AUDIENCE.get(recipient.privacy_scope)
        key = viewer_key(event.actor.source, event.actor.platform_id)
        memory = self._viewer_memory()
        if not (audience and key and memory and event.kind == "chat_message"):
            return []
        try:
            return memory.recall(key, audience)
        except Exception:
            return []

    def _remember_viewer(self, event: StreamInputEvent, recipient) -> None:
        audience = _AUDIENCE.get(recipient.privacy_scope)
        key = viewer_key(event.actor.source, event.actor.platform_id)
        memory = self._viewer_memory()
        if not (audience and key and memory and event.kind == "chat_message"):
            return
        raw = str(event.metadata.get("raw_text") or event.text or "")
        try:
            memory.remember(key, raw, alias=_speaker_label(event) or "viewer", audience=audience,
                            scope=recipient.privacy_scope,
                            trust_level=str(event.metadata.get("trust_level") or "new_viewer"))
        except Exception:
            pass

    def _relationship_target(self, event: StreamInputEvent, recipient) -> tuple[RelationshipStore, str, str] | None:
        """(store, key, audience) for a Twitch or Discord speaker; None for the owner and everyone else.

        The owner's relationship is owner-private and kept by the conversation service.
        """
        audience = _AUDIENCE.get(recipient.privacy_scope)
        memory = getattr(self._conversation, "_memory", None)
        engine = getattr(memory, "_engine", None)
        source, platform_id = event.actor.source.strip().lower(), event.actor.platform_id
        if not (audience and engine is not None and event.kind in {"chat_message", "mic_transcript"}
                and viewer_key(source, platform_id)):
            return None
        if event.metadata.get("is_owner") is True or str(event.metadata.get("trust_level") or "") == "owner":
            return None
        try:
            person = memory.resolve_person_identity(source, platform_id or "")
        except Exception:
            person = None
        if person and str(person.get("trust") or "") == "owner":
            return None
        key = relationship_key(source, platform_id, person_id=person.get("id") if person else None)
        return (RelationshipStore(engine), key, audience) if key else None

    def _relationship_note(self, event: StreamInputEvent, store: RelationshipStore, key: str, audience: str) -> str | None:
        name = _speaker_label(event) or safe_username_alias(event.actor.display_name, fallback="this person")
        try:
            return shared_line(store.get(key, audience), name)
        except Exception:
            return None

    def _record_relationship(self, event: StreamInputEvent, store: RelationshipStore, key: str, audience: str) -> None:
        raw = str(event.metadata.get("raw_text") or event.text or "")
        try:
            store.record(key, raw, audience=audience, trust_level=str(event.metadata.get("trust_level") or "new_viewer"))
        except Exception:
            pass

    def _record_temp_memory(self, result: StreamTurnResult) -> None:
        if result.safety_decision.get("review_pending"):
            return
        try:
            self._temp_memory_store.record_turn(result)
        except Exception:
            pass

    def _assemble_recent_context(self) -> str | None:
        try:
            return self._recent_context.assemble()
        except Exception:
            return None

    def _maybe_propose_self_goal(self, event: StreamInputEvent, result: StreamTurnResult) -> None:
        if event.kind != "conversation_lull":
            return
        if not _twitch_control_enabled(event, "self_goal_proposals_enabled", True):
            return
        if not result.decision.metadata.get("would_reply"):
            return
        try:
            self._self_goal_store.propose(
                title="Keep chat warm during quiet moments",
                description="When the stream is quiet, propose a short safe question or observation for chat instead of letting silence stretch.",
                source="stream_brain",
                metadata={
                    "event_id": event.event_id,
                    "trace_id": result.trace_id,
                    "idle_policy_reason": event.metadata.get("idle_policy_reason"),
                },
            )
        except Exception:
            pass

    def _stream_speech_synthesizer(self) -> StreamSpeechSynthesizer:
        if self._speech_synthesizer is None:
            self._speech_synthesizer = TTSService()
        return self._speech_synthesizer

    def record_result(self, result: StreamTurnResult) -> None:
        if result.output_events and not result.output_dispatch:
            result.output_dispatch = self._output_dispatcher.dispatch(result.output_events).model_dump()
        self._trace_store.append(result)

    def speech_ready_in(self, event: StreamInputEvent) -> float:
        """Seconds until a paced turn for ``event`` could speak; used by the chat-window picker."""
        return self._pacer.ready_in(event)

    def take_deferred(self) -> list[dict]:
        """Hand the pacer's deferred turns to the chat-window picker for replay."""
        return self._pacer.take_pending()

    def record_skipped(self, event: StreamInputEvent, *, reason: str, metadata: dict | None = None) -> StreamTurnResult:
        """Trace a chat message the picker dropped (stale, overflow) without calling the model."""
        result = StreamTurnResult(
            input_event=event,
            decision=TurnDecision(
                decision="ignore",
                reason=reason,
                metadata={"event_kind": event.kind, **(metadata or {})},
            ),
        )
        self._record_temp_memory(result)
        self._trace_store.append(result)
        return result

    def pending_queue(self) -> dict:
        """Get pending queue snapshot.
        
        Returns:
            dict: Pending queue snapshot.
        """
        return self._pacer.pending_snapshot()

    def stop_stream(self, *, reason: str = "operator_stop", live_cancellations: dict | None = None) -> StreamTurnResult:
        """Stop stream speech.
        
        Args:
            reason: Reason for stopping stream.
            live_cancellations: Optional live cancellations.
        
        Returns:
            StreamTurnResult: Stop stream result.
        """
        started_at = time.perf_counter()
        self._pacer.clear_pending()
        metadata = {"reason": reason, "cleared_pending_queue": True}
        if live_cancellations is not None:
            metadata["live_cancellations"] = live_cancellations
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
                payload={"reason": reason, "interrupted": True, "clear_pending": True, **output_context_from_input(input_event)},
            ),
            StreamOutputEvent(
                input_event_id=input_event.event_id,
                turn_id=turn_id,
                type="subtitle_line",
                payload={"text": "", "clear": True, **output_context_from_input(input_event)},
            ),
            StreamOutputEvent(
                input_event_id=input_event.event_id,
                turn_id=turn_id,
                type="overlay_update",
                payload={"target": "subtitles", "text": "", "clear": True, **output_context_from_input(input_event)},
            ),
        ]
        output_dispatch = self._output_dispatcher.dispatch(output_events)
        result = StreamTurnResult(
            input_event=input_event,
            decision=TurnDecision(
                decision="ignore",
                reason="stream_stop_requested",
                metadata=metadata,
            ),
            output_events=output_events,
            output_dispatch=output_dispatch.model_dump(),
            timing_ms={"stream_brain_ms": round((time.perf_counter() - started_at) * 1000, 1)},
        )
        self._trace_store.append(result)
        return result

    def decide(self, event: StreamInputEvent) -> TurnDecision:
        """Make turn decision from event.
        
        Args:
            event: Stream input event.
        
        Returns:
            TurnDecision: Turn decision.
        """
        text = (event.text or "").strip()
        lowered = text.lower()
        input_safety = _input_safety(event)
        safety_category = str(input_safety.get("category") or "")
        presence_mode = str(event.metadata.get("presence_mode") or "").strip().lower()

        if event.metadata.get("presence_suppressed"):
            return TurnDecision(
                decision="ignore",
                reason=f"presence_{presence_mode or 'unknown'}_suppressed_public_output",
                metadata={"event_kind": event.kind, "presence_mode": presence_mode or None},
            )

        if event.kind == "moderator_action":
            return TurnDecision(
                decision="moderation_escalation",
                reason="moderator_action_events_are_policy_inputs",
                requires_moderation_review=True,
                metadata={"event_kind": event.kind},
            )
        if event.kind == "game_state":
            return _game_decision(event)
        if event.kind == "conversation_lull":
            idle_policy_decision = str(event.metadata.get("idle_policy_decision") or "defer")
            would_reply = idle_policy_decision in {"reply", "topic_shift", "check_in"}
            if event.metadata.get("scheduler_authorized") and would_reply:
                return TurnDecision(
                    decision="reply",
                    reason="bounded_proactive_scheduler_authorized",
                    should_call_conversation=True,
                    response_mode="proactive_public" if event.metadata.get("public_output") else "proactive_local",
                    metadata={
                        "event_kind": event.kind,
                        "dry_run": False,
                        "idle_policy_decision": idle_policy_decision,
                        "idle_policy_reason": event.metadata.get("idle_policy_reason"),
                        "tools_allowed": False,
                    },
                )
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
        if event.kind == "audio_event":
            return TurnDecision(
                decision="defer",
                reason="audio_events_are_recorded_without_forced_response",
                should_call_conversation=False,
                response_mode="audio_observation",
                metadata={"event_kind": event.kind},
            )
        if not text:
            return TurnDecision(decision="ignore", reason="empty_event_text", metadata={"event_kind": event.kind})
        if event.kind == "owner_command":
            return TurnDecision(
                decision="reply",
                reason="owner_command_requires_direct_reply",
                should_call_conversation=True,
                response_mode="direct",
            )
        if _MODERATION_REQUEST_RE.search(lowered):
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
        if event.kind == "redeem":
            if ("gamma" in lowered or "shana" in lowered) and _twitch_control_enabled(event, "mention_replies_enabled", True):
                return TurnDecision(
                    decision="reply",
                    reason="twitch_redeem_mentions_assistant",
                    should_call_conversation=True,
                    response_mode="chat",
                    metadata={"event_kind": event.kind, "priority": event.priority},
                )
            return TurnDecision(
                decision="defer",
                reason="twitch_redeem_recorded_without_forced_response",
                should_call_conversation=False,
                response_mode="redeem",
                metadata={"event_kind": event.kind, "priority": event.priority},
            )
        if event.kind in {"donation", "follow", "raid", "bits", "subscription"}:
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


OWNER_EVENT_KINDS = {"mic_transcript", "owner_command"}


def _game_decision(event: StreamInputEvent) -> TurnDecision:
    """A game move line is said as written (after safety review); a comment event gets a short reaction."""
    meta = {"event_kind": event.kind, "game": event.metadata.get("game")}
    line = str(event.metadata.get("game_say") or "").strip()
    if line:
        return TurnDecision(decision="acknowledge", reason="game_move_line", response_mode="game_say",
                            metadata={**meta, "canned_response": line[:300], "canned_emotion": "neutral"})
    if event.metadata.get("game_comment") and (event.text or "").strip():
        return TurnDecision(decision="reply", reason="game_event_comment", should_call_conversation=True,
                            response_mode="game_comment", metadata=meta)
    return TurnDecision(decision="defer", reason="game_state_context_only", metadata=meta)


def _dispatched_speech_text(output_events: list[StreamOutputEvent]) -> str | None:
    """The text this turn put on stream (subtitle or audio), or None when nothing was spoken."""
    spoke = False
    text = ""
    for output in output_events:
        if output.type == "subtitle_line" and not output.payload.get("clear"):
            spoke = True
            text = text or str(output.payload.get("text") or "")
        elif output.type in {"speech_started", "speech_chunk"}:
            spoke = True
    return text if spoke else None


def _is_owner_private_event(event: StreamInputEvent, *, owner_channel: bool) -> bool:
    """The owner speaking to Shana privately from the dashboard Monitor.

    Needs the verified owner channel; the event's own actor and metadata are
    claims from the request body and are not enough by themselves.
    """
    return (
        owner_channel
        and event.actor.source.strip().lower() == "local_monitor"
        and event.kind in OWNER_EVENT_KINDS
    )


def _speaker_label(event: StreamInputEvent) -> str | None:
    """Who said a shared chat message, as the stream already shows them.

    Kept out of the message text so owner commands ("remember: ...") still parse.
    """
    if event.kind != "chat_message" or event.actor.source.strip().lower() not in {"twitch", "discord"}:
        return None
    return str(event.metadata.get("safe_display_name") or "").strip() or safe_username_alias(event.actor.display_name)


# A request to moderate someone, not a message that happens to contain "ban"
# or "timeout" ("timeout error", "urban").
_MODERATION_REQUEST_RE = re.compile(
    r"\b(?:ban|unban|timeout|time\s+out|mute)\s+(?:@\w+|him|her|them|this\s+(?:guy|person|user|bot)|"
    r"that\s+(?:guy|person|user|bot)|the\s+(?:spammer|bot|troll))\b"
    r"|\bdelete\s+(?:that|this|his|her|their)\s+message\b|\bmod\s+review\b"
)


_AUDIENCE = {"public_stream": "stream", "public_discord": "discord"}
