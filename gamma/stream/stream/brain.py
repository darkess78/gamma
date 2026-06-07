from __future__ import annotations

import time
from concurrent.futures import TimeoutError as FutureTimeoutError
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from ..config import settings
from ..conversation.service import ConversationService
from ..errors import ConversationError
from ..schemas.response import AssistantResponse
from ..safety.hard_blocklist import matched_rules as hard_block_matches
from ..safety.heuristic_filter import review as heuristic_review
from ..safety.llm_reviewer import LLMReviewDecision, SpeechLLMReviewer
from ..safety.privacy_guard import review_private_info_output
from ..safety.rewrite_guard import rewrite_text
from .actions import ActionPlanner
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
from .self_goals import StreamSelfGoalStore
from .temp_memory import StreamTempMemoryStore
from .trace import StreamTraceStore


DEFAULT_STREAM_SPEECH_GAP_SECONDS = 5.0
DEFAULT_SPAM_QUIP_COOLDOWN_SECONDS = 60.0
DEFAULT_MAX_SPEECH_SECONDS_PER_MINUTE = 20.0
DEFAULT_SUBTITLE_HOLD_MS = 1200
HIGH_PRIORITY_BYPASS_THRESHOLD = 20


class StreamSafetyReviewer(Protocol):
    def review(self, text: str) -> LLMReviewDecision:
        ...


class StreamBrain:
    """Policy boundary between public/live events and the conversation service."""

    def __init__(
        self,
        *,
        conversation: ConversationService | None = None,
        trace_store: StreamTraceStore | None = None,
        action_planner: ActionPlanner | None = None,
        output_dispatcher: StreamOutputDispatcher | None = None,
        safety_reviewer: StreamSafetyReviewer | None = None,
        temp_memory_store: StreamTempMemoryStore | None = None,
        self_goal_store: StreamSelfGoalStore | None = None,
    ) -> None:
        self._conversation = conversation or ConversationService()
        self._trace_store = trace_store or StreamTraceStore()
        self._action_planner = action_planner or ActionPlanner()
        self._output_dispatcher = output_dispatcher or StreamOutputDispatcher()
        self._pacer = StreamSpeechPacer()
        self._safety_reviewer = safety_reviewer or SpeechLLMReviewer()
        self._temp_memory_store = temp_memory_store or StreamTempMemoryStore()
        self._self_goal_store = self_goal_store or StreamSelfGoalStore()

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
        canned_response = _canned_response_from_decision(decision)
        if canned_response:
            response = AssistantResponse(
                spoken_text=canned_response,
                emotion=str(decision.metadata.get("canned_emotion") or "teasing"),
            )
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
            action_plan = self._action_planner.plan_from_response(response)
        safety_decision = self._review_stream_output(event, response) if response else {}
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
        budget_decision = self._pacer.apply_budget(event, decision, response.spoken_text if response else "")
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
        output_events = _filter_stream_output_events(event, output_events)
        output_dispatch = self._output_dispatcher.dispatch(output_events) if output_events else None

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
        return result

    def _record_temp_memory(self, result: StreamTurnResult) -> None:
        try:
            self._temp_memory_store.record_turn(result)
        except Exception:
            pass

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

    def _review_stream_output(self, event: StreamInputEvent, response: AssistantResponse | None) -> dict:
        if response is None or not _is_public_stream_event(event):
            return {}
        speech_filter = response.tts_metadata.get("speech_filter") if isinstance(response.tts_metadata, dict) else None
        if isinstance(speech_filter, dict) and speech_filter.get("blocked"):
            return {
                "action": "filtered",
                "blocked": True,
                "source": "conversation_speech_filter",
                "stage": "fast",
                "matched_rules": speech_filter.get("matched_rules", []),
                "layers": speech_filter.get("layers", []),
                "filter_action": speech_filter.get("action"),
                "safe_output": "filtered",
                "playback_approved": False,
            }
        fast_review = _fast_stream_safety_review(response.spoken_text)
        if fast_review["blocked"]:
            return {
                "action": "filtered",
                "blocked": True,
                "source": "stream_output_gate_fast",
                "stage": "fast",
                "matched_rules": fast_review["matched_rules"],
                "layers": fast_review["layers"],
                "filter_action": fast_review["filter_action"],
                "safe_output": "filtered",
                "playback_approved": False,
            }
        llm_decision = self._review_stream_output_with_llm(event, response.spoken_text)
        if llm_decision is not None:
            return llm_decision
        return {
            "action": "allow",
            "blocked": False,
            "source": "stream_output_gate_fast",
            "stage": "fast",
            "matched_rules": fast_review["matched_rules"],
            "layers": fast_review["layers"],
            "filter_action": fast_review["filter_action"],
            "playback_approved": True,
        }

    def _review_stream_output_with_llm(self, event: StreamInputEvent, text: str) -> dict | None:
        if not _twitch_control_enabled(event, "llm_safety_review_enabled", True):
            return None
        if not settings.speech_filter_llm_enabled:
            return None
        timeout_seconds = max(0.05, float(settings.stream_safety_review_timeout_seconds or 2.0))
        started_at = time.perf_counter()
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(self._safety_reviewer.review, text)
        try:
            review = future.result(timeout=timeout_seconds)
        except FutureTimeoutError:
            future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            return {
                "action": _stream_review_timeout_action(),
                "blocked": False,
                "source": "stream_output_gate_llm",
                "stage": "llm",
                "review_timeout": True,
                "timeout_seconds": timeout_seconds,
                "review_ms": round((time.perf_counter() - started_at) * 1000, 1),
                "playback_approved": False,
            }
        except Exception as exc:
            executor.shutdown(wait=False, cancel_futures=True)
            return {
                "action": "skip",
                "blocked": False,
                "source": "stream_output_gate_llm",
                "stage": "llm",
                "review_error": str(exc),
                "review_ms": round((time.perf_counter() - started_at) * 1000, 1),
                "playback_approved": False,
            }
        finally:
            if future.done():
                executor.shutdown(wait=False, cancel_futures=True)
        review_ms = round((time.perf_counter() - started_at) * 1000, 1)
        if review.action != "allow":
            return {
                "action": "filtered",
                "blocked": True,
                "source": "stream_output_gate_llm",
                "stage": "llm",
                "matched_rules": [review.reason] if review.reason else [],
                "layers": ["llm"],
                "filter_action": review.action,
                "review_ms": review_ms,
                "safe_output": "filtered",
                "playback_approved": False,
            }
        return {
            "action": "allow",
            "blocked": False,
            "source": "stream_output_gate_llm",
            "stage": "llm",
            "matched_rules": [review.reason] if review.reason else [],
            "layers": ["llm"],
            "filter_action": review.action,
            "review_ms": review_ms,
            "playback_approved": True,
        }

    def record_result(self, result: StreamTurnResult) -> None:
        if result.output_events and not result.output_dispatch:
            result.output_dispatch = self._output_dispatcher.dispatch(result.output_events).model_dump()
        self._trace_store.append(result)

    def pending_queue(self) -> dict:
        return self._pacer.pending_snapshot()

    def stop_stream(self, *, reason: str = "operator_stop", live_cancellations: dict | None = None) -> StreamTurnResult:
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


class StreamSpeechPacer:
    def __init__(
        self,
        *,
        now=time.monotonic,
        default_min_gap_seconds: float = DEFAULT_STREAM_SPEECH_GAP_SECONDS,
        default_spam_quip_cooldown_seconds: float = DEFAULT_SPAM_QUIP_COOLDOWN_SECONDS,
        default_max_speech_seconds_per_minute: float = DEFAULT_MAX_SPEECH_SECONDS_PER_MINUTE,
    ) -> None:
        self._now = now
        self._default_min_gap_seconds = default_min_gap_seconds
        self._default_spam_quip_cooldown_seconds = default_spam_quip_cooldown_seconds
        self._default_max_speech_seconds_per_minute = default_max_speech_seconds_per_minute
        self._last_spoken_at: float | None = None
        self._last_spam_quip_at: float | None = None
        self._pending: dict[str, dict] = {}
        self._speech_budget_events: list[tuple[float, float]] = []

    def apply(self, event: StreamInputEvent, decision: TurnDecision) -> TurnDecision:
        if not _decision_would_speak(decision):
            return decision
        if not _is_paced_stream_event(event):
            self.mark_spoken()
            return decision
        now = self._now()
        spam_cooldown_decision = self._spam_quip_cooldown_decision(event, decision, now)
        if spam_cooldown_decision is not None:
            return spam_cooldown_decision
        min_gap = _stream_min_gap_seconds(event, self._default_min_gap_seconds)
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
        if decision.response_mode == "spam_quip":
            self._last_spam_quip_at = now
        return decision

    def apply_budget(self, event: StreamInputEvent, decision: TurnDecision, spoken_text: str) -> TurnDecision | None:
        if not _decision_would_speak(decision) or not _is_paced_stream_event(event):
            return None
        if not _twitch_control_enabled(event, "voice_enabled", True):
            return None
        max_seconds = _stream_max_speech_seconds_per_minute(event, self._default_max_speech_seconds_per_minute)
        if max_seconds <= 0:
            estimate = _estimate_speech_seconds(spoken_text)
            pending = self._store_pending(
                event=event,
                decision=decision,
                min_gap=0.0,
                elapsed=0.0,
                budget_seconds=0.0,
                estimated_speech_seconds=estimate,
            )
            return self._budget_deferred_decision(
                decision=decision,
                pending=pending,
                max_seconds=max_seconds,
                used_seconds=0.0,
                estimated_seconds=estimate,
            )
        now = self._now()
        self._prune_speech_budget(now)
        used_seconds = sum(seconds for _timestamp, seconds in self._speech_budget_events)
        estimated_seconds = _estimate_speech_seconds(spoken_text)
        if used_seconds + estimated_seconds > max_seconds:
            pending = self._store_pending(
                event=event,
                decision=decision,
                min_gap=0.0,
                elapsed=0.0,
                budget_seconds=max_seconds,
                budget_used_seconds=used_seconds,
                estimated_speech_seconds=estimated_seconds,
            )
            return self._budget_deferred_decision(
                decision=decision,
                pending=pending,
                max_seconds=max_seconds,
                used_seconds=used_seconds,
                estimated_seconds=estimated_seconds,
            )
        self._speech_budget_events.append((now, estimated_seconds))
        return None

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

    def _store_pending(
        self,
        *,
        event: StreamInputEvent,
        decision: TurnDecision,
        min_gap: float,
        elapsed: float,
        **extra: float,
    ) -> dict:
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
        for key, value in extra.items():
            item[key] = round(value, 3)
        if replaced:
            item["replaced_event_id"] = replaced.get("event_id")
        self._pending[slot] = item
        return item

    def _prune_speech_budget(self, now: float) -> None:
        self._speech_budget_events = [
            (timestamp, seconds)
            for timestamp, seconds in self._speech_budget_events
            if now - timestamp < 60.0
        ]

    def _budget_deferred_decision(
        self,
        *,
        decision: TurnDecision,
        pending: dict,
        max_seconds: float,
        used_seconds: float,
        estimated_seconds: float,
    ) -> TurnDecision:
        return TurnDecision(
            decision="defer",
            reason="stream_speech_budget_deferred",
            should_call_conversation=False,
            response_mode=decision.response_mode,
            requires_moderation_review=decision.requires_moderation_review,
            metadata={
                **decision.metadata,
                "would_decision": decision.decision,
                "would_reason": decision.reason,
                "max_speech_seconds_per_minute": max_seconds,
                "budget_used_seconds": round(used_seconds, 3),
                "estimated_speech_seconds": round(estimated_seconds, 3),
                "pending_slot": pending["slot"],
                "replaced_event_id": pending.get("replaced_event_id"),
            },
        )

    def _spam_quip_cooldown_decision(self, event: StreamInputEvent, decision: TurnDecision, now: float) -> TurnDecision | None:
        if decision.response_mode != "spam_quip":
            return None
        cooldown = _stream_spam_quip_cooldown_seconds(event, self._default_spam_quip_cooldown_seconds)
        if self._last_spam_quip_at is None or cooldown <= 0:
            return None
        elapsed = now - self._last_spam_quip_at
        if elapsed >= cooldown:
            return None
        return TurnDecision(
            decision="ignore",
            reason="twitch_spam_quip_cooldown_active",
            should_call_conversation=False,
            response_mode="none",
            metadata={
                **_metadata_without_canned_response(decision.metadata),
                "would_decision": decision.decision,
                "would_reason": decision.reason,
                "cooldown_seconds": cooldown,
                "elapsed_seconds": round(elapsed, 3),
            },
        )


def _input_safety(event: StreamInputEvent) -> dict:
    value = event.metadata.get("input_safety")
    return value if isinstance(value, dict) else {}


def _twitch_control_enabled(event: StreamInputEvent, key: str, default: bool) -> bool:
    controls = event.metadata.get("twitch_controls")
    if not isinstance(controls, dict) or key not in controls:
        return default
    return bool(controls[key])


def _filter_stream_output_events(event: StreamInputEvent, output_events: list[StreamOutputEvent]) -> list[StreamOutputEvent]:
    controls = event.metadata.get("twitch_controls")
    if event.actor.source != "twitch" or not isinstance(controls, dict):
        return output_events
    subtitles_enabled = bool(controls.get("subtitles_enabled", True))
    voice_enabled = bool(controls.get("voice_enabled", True))
    filtered: list[StreamOutputEvent] = []
    for output_event in output_events:
        if output_event.type == "subtitle_line" and not subtitles_enabled:
            continue
        if output_event.type in {"speech_started", "speech_chunk", "speech_ended"} and not voice_enabled:
            continue
        filtered.append(output_event)
    return filtered


def _mark_filtered_output_events(output_events: list[StreamOutputEvent], safety_decision: dict) -> list[StreamOutputEvent]:
    marked: list[StreamOutputEvent] = []
    for output_event in output_events:
        payload = dict(output_event.payload)
        payload["filtered"] = True
        payload["safety_action"] = safety_decision.get("action")
        marked.append(output_event.model_copy(update={"payload": payload}))
    return marked


def _apply_estimated_subtitle_timing(event: StreamInputEvent, output_events: list[StreamOutputEvent]) -> list[StreamOutputEvent]:
    if not _is_public_stream_event(event):
        return output_events
    if not any(output_event.type == "speech_started" for output_event in output_events):
        return output_events
    timed_events: list[StreamOutputEvent] = []
    for output_event in output_events:
        if output_event.type != "subtitle_line" or output_event.payload.get("filtered"):
            timed_events.append(output_event)
            continue
        timed_events.extend(_timed_subtitle_events(output_event))
    return timed_events


def _timed_subtitle_events(output_event: StreamOutputEvent) -> list[StreamOutputEvent]:
    text = str(output_event.payload.get("text") or "").strip()
    words = text.split()
    if len(words) <= 4:
        payload = {
            **dict(output_event.payload),
            "timing": "estimated",
            "subtitle_sequence": 0,
            "estimated_start_ms": 0,
            "estimated_end_ms": int(_estimate_speech_seconds(text) * 1000),
            "hold_ms": DEFAULT_SUBTITLE_HOLD_MS,
            "is_final": True,
        }
        return [output_event.model_copy(update={"payload": payload})]
    total_ms = max(1, int(_estimate_speech_seconds(text) * 1000))
    chunk_size = 4
    chunks = [words[index:index + chunk_size] for index in range(0, len(words), chunk_size)]
    events: list[StreamOutputEvent] = []
    for index, chunk in enumerate(chunks):
        end_word = min((index + 1) * chunk_size, len(words))
        start_word = index * chunk_size
        is_final = index == len(chunks) - 1
        payload = {
            **dict(output_event.payload),
            "text": " ".join(words[:end_word]),
            "timing": "estimated",
            "subtitle_sequence": index,
            "estimated_start_ms": int(total_ms * start_word / len(words)),
            "estimated_end_ms": int(total_ms * end_word / len(words)),
            "hold_ms": DEFAULT_SUBTITLE_HOLD_MS if is_final else 0,
            "is_final": is_final,
        }
        events.append(
            StreamOutputEvent(
                input_event_id=output_event.input_event_id,
                turn_id=output_event.turn_id,
                type="subtitle_line",
                payload=payload,
            )
        )
    return events


def _filtered_stream_response() -> AssistantResponse:
    audio_path = _filtered_audio_path()
    return AssistantResponse(
        spoken_text="filtered",
        emotion="concerned",
        audio_path=str(audio_path) if audio_path else None,
        audio_content_type="audio/wav" if audio_path else None,
        motions=[],
        tool_calls=[],
        tool_results=[],
        memory_candidates=[],
    )


def _filtered_audio_path() -> Path | None:
    raw_path = settings.stream_filtered_audio_path
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.is_absolute():
        path = settings.project_root / path
    return path if path.exists() else None


def _is_public_stream_event(event: StreamInputEvent) -> bool:
    return event.actor.source == "twitch" or event.kind in {
        "chat_message",
        "follow",
        "raid",
        "donation",
        "bits",
        "subscription",
        "redeem",
    }


def _fast_stream_safety_review(text: str) -> dict:
    normalized = " ".join((text or "").split())
    privacy = review_private_info_output(normalized)
    if privacy.blocked:
        return {
            "blocked": True,
            "matched_rules": privacy.matched_rules,
            "layers": ["privacy_guard"],
            "filter_action": "privacy_refusal",
        }
    matched: list[str] = []
    layers: list[str] = []
    filter_action = "allow"
    if settings.speech_filter_hard_block_enabled:
        hard = hard_block_matches(normalized)
        if hard:
            matched.extend(hard)
            layers.append("hard_block")
            filter_action = "block"
    if filter_action == "allow" and settings.speech_filter_heuristic_enabled:
        heuristic = heuristic_review(text=normalized, level=settings.speech_filter_level)
        if heuristic.matched_rules:
            matched.extend(heuristic.matched_rules)
            layers.append("heuristic")
            filter_action = heuristic.action
    return {
        "blocked": filter_action == "block",
        "matched_rules": matched,
        "layers": layers,
        "filter_action": filter_action,
        "safe_text": rewrite_text(text=normalized, action=filter_action) if filter_action != "allow" else normalized,
    }


def _stream_review_timeout_action() -> str:
    if settings.stream_safety_review_timeout_action in {"skip", "defer", "hold"}:
        return settings.stream_safety_review_timeout_action
    return "skip"


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


def _stream_spam_quip_cooldown_seconds(event: StreamInputEvent, default: float) -> float:
    controls = event.metadata.get("twitch_controls")
    if not isinstance(controls, dict):
        return default
    raw_value = controls.get("spam_quip_cooldown_seconds")
    if raw_value is None:
        return default
    try:
        return max(0.0, float(raw_value))
    except (TypeError, ValueError):
        return default


def _stream_max_speech_seconds_per_minute(event: StreamInputEvent, default: float) -> float:
    controls = event.metadata.get("twitch_controls")
    if not isinstance(controls, dict):
        return default
    raw_value = controls.get("max_speech_seconds_per_minute")
    if raw_value is None:
        return default
    try:
        return max(0.0, float(raw_value))
    except (TypeError, ValueError):
        return default


def _estimate_speech_seconds(text: str) -> float:
    words = len((text or "").split())
    if words <= 0:
        return 0.0
    return round(max(0.8, words / 2.6), 3)


def _metadata_without_canned_response(metadata: dict) -> dict:
    cleaned = dict(metadata)
    cleaned.pop("canned_response", None)
    cleaned.pop("canned_emotion", None)
    return cleaned


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
    if event.kind in {"chat_message", "follow", "raid", "donation", "bits", "subscription", "redeem"}:
        return True
    return False


def _pending_slot(event: StreamInputEvent) -> str:
    if event.kind in {"follow", "raid", "donation", "bits", "subscription", "redeem"} or event.priority >= 10:
        return "high_priority"
    return "ambient"


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
