from __future__ import annotations

import json
import re
import time
import threading
from datetime import datetime, timezone

from ..config import settings
from ..errors import ConversationError, GammaError
from ..identity.profile import SpeakerProfile
from ..identity.resolver import IdentityResolver
from ..llm.base import LLMCallContext
from ..llm.capabilities import estimate_text_tokens
from ..llm.context_budget import ContextBudgetManager, prompt_layers_from_system_prompt
from ..llm.factory import build_llm_adapter
from ..llm.router_adapter import begin_route_trace, take_route_trace
from ..memory.service import MemoryService
from ..memory.continuity import ContinuityService
from ..persona.assistant_state import AssistantStateStore
from ..persona.emotion_service import EmotionMemoryService
from ..persona.loader import build_system_prompt
from ..safety.privacy_guard import review_private_info_request
from ..safety.speech_filter import SpeechFilterResult, SpeechSafetyFilter
from ..schemas.conversation import SpeakerContext
from ..schemas.response import (
    AssistantResponse,
    ConversationTurnDraft,
    DeliveryMode,
    EmotionTag,
    MemoryCandidate,
    ToolCall,
    ToolExecutionResult,
    VisionAnalysis,
)
from ..tools.registry import ToolRegistry
from ..vision.service import VisionImage, VisionService
from ..voice.expressive_text import strip_hidden_style_tags
from ..voice.tts import TTSService
from .turn_contract import ParsedTurn, StructuredTurnParser, TurnContractError, resolve_delivery, structured_turn_instruction


ALLOWED_EMOTIONS: set[str] = {"neutral", "happy", "teasing", "concerned", "excited", "embarrassed", "annoyed"}
_CODE_FENCE_RE = re.compile(r"```+")
_INLINE_TICK_RE = re.compile(r"`([^`]*)`")


class ConversationService:
    """Orchestrates conversation responses, memory, tools, and voice."""

    def __init__(self) -> None:
        """Initialize conversation service with dependencies."""
        self._memory = MemoryService()
        self._llm = None
        self._tts = None
        self._tools = ToolRegistry()
        self._vision = VisionService()
        self._identity = IdentityResolver()
        self._assistant_state = AssistantStateStore()
        self._emotion_memory = EmotionMemoryService()
        self._speech_filter = SpeechSafetyFilter(settings.speech_filter_level)
        self._continuity = ContinuityService()
        self._turn_parser = StructuredTurnParser()

    def respond(
        self,
        user_text: str,
        session_id: str | None = None,
        synthesize_speech: bool = False,
        speaker_ctx: SpeakerContext | None = None,
        fast_mode: bool = False,
        brief_mode: bool = False,
        micro_mode: bool = False,
        defer_llm_safety_review: bool = False,
        background_context: str | None = None,
        evaluation_mode: bool = False,
        delivery_context: str | None = None,
        speech_allowed: bool = True,
        speech_requested: bool | None = None,
    ) -> AssistantResponse:
        """Respond to a user text message.
        
        Args:
            user_text: User message text.
            session_id: Optional session identifier.
            synthesize_speech: Whether to generate TTS audio.
            speaker_ctx: Optional speaker context.
            fast_mode: Fast path without metadata extraction.
            brief_mode: Brief response mode.
            micro_mode: Micro-reply mode.
            defer_llm_safety_review: Defer LLM safety review.
            background_context: Optional trusted background note for the system prompt.
            evaluation_mode: Run without durable state, tools, or production timing logs.
            
        Returns:
            AssistantResponse with spoken text and timing.
        """
        speaker = self._identity.resolve(speaker_ctx)
        exchange_id = None
        privacy_scope = "local_private" if speaker.memory_read_allowed else "local_generic"
        if session_id and not evaluation_mode:
            exchange_id = self._continuity.begin_exchange(
                session_id=session_id,
                text=user_text,
                speaker_name=speaker.name,
                input_source=speaker_ctx.source if speaker_ctx is not None else "local",
                privacy_scope=privacy_scope,
            )
        try:
            response = self._respond(
                user_text=user_text,
                session_id=session_id,
                synthesize_speech=synthesize_speech,
                speaker=speaker,
                fast_mode=fast_mode,
                brief_mode=brief_mode,
                micro_mode=micro_mode,
                defer_llm_safety_review=defer_llm_safety_review,
                background_context=background_context,
                persist_state=not evaluation_mode,
                interaction_mode="evaluation" if evaluation_mode else "chat",
                delivery_context=delivery_context or ("direct_voice" if synthesize_speech else "direct_text"),
                speech_allowed=speech_allowed,
                speech_requested=synthesize_speech if speech_requested is None else speech_requested,
            )
        except Exception:
            if exchange_id:
                self._continuity.fail_exchange(exchange_id)
            raise
        if exchange_id and session_id and not evaluation_mode:
            response.route_trace_id = exchange_id
            self._continuity.complete_exchange(
                exchange_id=exchange_id,
                session_id=session_id,
                assistant_text=response.display_text or "",
                trace_id=response.route_trace_id,
                output_target="dashboard_monitor",
                privacy_scope=privacy_scope,
                internal_summary=response.internal_summary,
                state_updates=response.state_updates.model_dump(exclude_none=True),
                tool_metadata={
                    "tool_calls": [item.model_dump() for item in response.tool_calls],
                    "tool_results": [item.model_dump() for item in response.tool_results],
                },
            )
            self._record_presence_interaction(session_id)
        return response

    def continuity_snapshot(self, session_id: str) -> dict:
        return self._continuity.snapshot(session_id)

    def delete_continuity_session(self, session_id: str) -> int:
        return self._continuity.delete_session(session_id)

    def record_durable_output(
        self,
        *,
        target_policy: str,
        turn_id: str,
        text: str,
        status: str,
        spoken: bool,
    ) -> None:
        self._continuity.record_output(
            target_policy=target_policy,
            turn_id=turn_id,
            text=text,
            status=status,
            spoken=spoken,
        )

    def durable_output_state(self, target_policy: str) -> dict | None:
        return self._continuity.last_output(target_policy)

    @staticmethod
    def _record_presence_interaction(session_id: str) -> None:
        try:
            from ..presence import load_presence_state, save_presence_state, utc_now

            state = load_presence_state(downgrade_stale_live=True)
            state.setdefault("lifecycle", {})["last_interaction_at"] = utc_now()
            state.setdefault("activity", {})["session_id"] = session_id
            scheduler = dict((state.setdefault("autonomy", {})).get("scheduler") or {})
            scheduler["attempts_for_topic"] = 0
            state["autonomy"]["scheduler"] = scheduler
            save_presence_state(state)
        except Exception:
            pass

    def respond_with_image(
        self,
        *,
        user_text: str,
        image_bytes: bytes,
        image_media_type: str,
        image_filename: str | None = None,
        vision_mode: str | None = None,
        session_id: str | None = None,
        synthesize_speech: bool = False,
        speaker_ctx: SpeakerContext | None = None,
    ) -> AssistantResponse:
        """Respond to a user message with image analysis.
        
        Args:
            user_text: User message text.
            image_bytes: Image bytes.
            image_media_type: Image MIME type.
            image_filename: Optional filename.
            vision_mode: Optional vision mode.
            session_id: Optional session identifier.
            synthesize_speech: Whether to generate TTS audio.
            speaker_ctx: Optional speaker context.
            
        Returns:
            AssistantResponse with vision analysis and reply.
        """
        image = self._vision.prepare_image(
            image_bytes=image_bytes,
            media_type=image_media_type,
            filename=image_filename,
        )
        vision_analysis = self._vision.analyze_image(
            llm_adapter=self._llm_adapter(),
            image=image,
            user_text=user_text,
            mode=vision_mode,
        )
        return self._respond(
            user_text=user_text,
            session_id=session_id,
            synthesize_speech=synthesize_speech,
            image=image,
            vision_analysis=vision_analysis,
            speaker=self._identity.resolve(speaker_ctx),
            delivery_context="direct_voice" if synthesize_speech else "direct_text",
        )

    def respond_presence_wake(
        self,
        *,
        session_id: str,
        speaker: SpeakerProfile,
        wake_context: str,
        synthesize_speech: bool,
    ) -> AssistantResponse:
        """Generate a dedicated Presence Wake opening without creating user memory."""
        try:
            begin_route_trace()
            started_at = time.perf_counter()
            persona_prompt = build_system_prompt(
                memory_service=self._memory,
                user_text=None,
                session_id=session_id,
                speaker=speaker,
            )
            wake_instruction = (
                "\n\nBegin this session naturally as Shana using the supplied context. "
                "Do not list or explain the context. Choose one brief greeting, observation, "
                "remembered thread, or question. Avoid repeating recent openings. "
                "Do not invent an external event. Keep the opening to one or two short sentences."
                + structured_turn_instruction(default_delivery="speech")
            )
            event_user_text = "Begin the presence_wake event now."

            def build_wake_prompt(usable_input_tokens: int) -> tuple[str, str, dict[str, object]]:
                prefix = persona_prompt + "\n\n# Presence Wake Event\n"
                mandatory_tokens = estimate_text_tokens(prefix, wake_instruction, event_user_text)
                optional_tokens = max(0, usable_input_tokens - mandatory_tokens - 128)
                bounded_context = wake_context.strip()[: optional_tokens * 4]
                compacted = len(bounded_context) < len(wake_context.strip())
                return (
                    prefix + bounded_context + wake_instruction,
                    event_user_text,
                    {
                        "compaction_level": 1 if compacted else 0,
                        "compaction_actions": ["truncate_wake_optional_context"] if compacted else [],
                    },
                )

            system_prompt, event_user_text, _prompt_metrics = build_wake_prompt(12_288)
            draft = self._llm_adapter().generate_reply(
                system_prompt=system_prompt,
                user_text=event_user_text,
                call_context=LLMCallContext(
                    session_id=session_id,
                    purpose="presence_wake",
                    persona_sensitive=True,
                    interaction_mode="presence",
                    reasoning_depth="normal",
                    cost_sensitive=False,
                    quality_tier="primary",
                    minimum_context_tokens=8192,
                    prompt_builder=build_wake_prompt,
                ),
            )
            parsed = self._parse_turn_result(
                raw=draft.text,
                system_prompt=system_prompt,
                user_text=event_user_text,
                legacy_delivery="speech",
                delivery_context="presence_wake",
                image=None,
            )
            turn_draft = parsed.draft
            expressive = strip_hidden_style_tags(self._cleanup_spoken_text(turn_draft.final_text), default_emotion=turn_draft.emotion)
            safe_spoken = (
                SpeechFilterResult(spoken_text="", blocked=False, matched_rules=[], action="allow", layers=[])
                if turn_draft.action in {"stay_silent", "defer"}
                else self._speech_filter.apply(expressive.clean_text, include_llm=True)
            )
            delivery_mode = resolve_delivery(
                action=turn_draft.action,
                requested=turn_draft.requested_delivery,
                context="presence_wake",
                speech_requested=synthesize_speech,
                speech_allowed=synthesize_speech,
            )
            communicated_text = safe_spoken.spoken_text if delivery_mode not in {"silent", "deferred"} else ""
            response = AssistantResponse(
                spoken_text=communicated_text,
                display_text=communicated_text,
                speech_text=communicated_text if delivery_mode == "speech" else "",
                delivery_mode=delivery_mode,
                response_action=turn_draft.action,
                reason_code=turn_draft.reason_code,
                emotion=self._normalize_emotion(expressive.emotion),
                voice_styles=list(dict.fromkeys([*turn_draft.voice_styles, *expressive.styles])),
                internal_summary=turn_draft.internal_summary or "Generated a Presence Wake opening.",
                state_updates=turn_draft.state_updates,
            )
            response.tts_metadata["speech_filter"] = {
                "level": settings.speech_filter_level,
                "blocked": safe_spoken.blocked,
                "matched_rules": safe_spoken.matched_rules,
                "action": safe_spoken.action,
                "layers": safe_spoken.layers,
            }
            if synthesize_speech and response.speech_text:
                tts_result = self._tts_service().synthesize(
                    response.speech_text,
                    emotion=response.emotion,
                    styles=response.voice_styles,
                )
                response.audio_path = tts_result.audio_path
                response.audio_content_type = tts_result.content_type
                response.tts_metadata.update(dict(tts_result.metadata or {}))
            response.timing_ms = {"total_ms": round((time.perf_counter() - started_at) * 1000, 1)}
            response.tts_metadata["route_events"] = take_route_trace()
            return response
        except GammaError:
            raise
        except Exception as exc:
            raise ConversationError(f"Presence Wake generation failed: {exc}") from exc

    def analyze_image(
        self,
        *,
        user_text: str,
        image_bytes: bytes,
        image_media_type: str,
        image_filename: str | None = None,
        vision_mode: str | None = None,
    ) -> VisionAnalysis:
        """Analyze an image using vision service.
        
        Args:
            user_text: Optional user text context.
            image_bytes: Image bytes.
            image_media_type: Image MIME type.
            image_filename: Optional filename.
            vision_mode: Optional vision mode.
            
        Returns:
            VisionAnalysis with structured image data.
        """
        image = self._vision.prepare_image(
            image_bytes=image_bytes,
            media_type=image_media_type,
            filename=image_filename,
        )
        return self._vision.analyze_image(
            llm_adapter=self._llm_adapter(),
            image=image,
            user_text=user_text,
            mode=vision_mode,
        )

    def memory_stats(self) -> dict[str, str | int]:
        """Get memory service statistics.
        
        Returns:
            dict: Memory stats string data.
        """
        return self._memory.stats()

    def _respond(
        self,
        *,
        user_text: str,
        session_id: str | None,
        synthesize_speech: bool,
        speaker: SpeakerProfile | None = None,
        fast_mode: bool = False,
        brief_mode: bool = False,
        micro_mode: bool = False,
        image: VisionImage | None = None,
        vision_analysis: VisionAnalysis | None = None,
        defer_llm_safety_review: bool = False,
        background_context: str | None = None,
        persist_state: bool = True,
        interaction_mode: str = "chat",
        delivery_context: str = "direct_text",
        speech_allowed: bool = True,
        speech_requested: bool | None = None,
    ) -> AssistantResponse:
        stripped = user_text.strip()
        if not stripped:
            raise ConversationError("user_text must not be empty.")

        try:
            privacy_decision = review_private_info_request(stripped)
            if privacy_decision.blocked:
                started_at = time.perf_counter()
                privacy_delivery = resolve_delivery(
                    action="reply",
                    requested="speech" if synthesize_speech else "text_only",
                    context=delivery_context,
                    speech_requested=synthesize_speech if speech_requested is None else speech_requested,
                    speech_allowed=speech_allowed,
                )
                response = AssistantResponse(
                    spoken_text=privacy_decision.replacement_text,
                    display_text=privacy_decision.replacement_text,
                    speech_text=privacy_decision.replacement_text if privacy_delivery == "speech" else "",
                    delivery_mode=privacy_delivery,
                    reason_code="privacy_refusal",
                    emotion="neutral",
                    internal_summary="Refused a request for private identifying information.",
                )
                speech_filter_metadata = {
                    "level": settings.speech_filter_level,
                    "blocked": True,
                    "matched_rules": privacy_decision.matched_rules,
                    "action": "privacy_refusal",
                    "layers": ["privacy_guard"],
                }
                response.tts_metadata["speech_filter"] = speech_filter_metadata
                timing = {
                    "privacy_guard_ms": round((time.perf_counter() - started_at) * 1000, 1),
                    "total_ms": round((time.perf_counter() - started_at) * 1000, 1),
                }
                if synthesize_speech and response.speech_text:
                    tts_started = time.perf_counter()
                    tts_result = self._tts_service().synthesize(response.speech_text, emotion=response.emotion)
                    response.audio_path = tts_result.audio_path
                    response.audio_content_type = tts_result.content_type
                    response.tts_metadata = dict(tts_result.metadata or {})
                    response.tts_metadata["speech_filter"] = speech_filter_metadata
                    timing["tts_ms"] = round((time.perf_counter() - tts_started) * 1000, 1)
                    timing["total_ms"] = round((time.perf_counter() - started_at) * 1000, 1)
                else:
                    timing["tts_ms"] = 0.0
                response.timing_ms = timing
                privacy_events = [
                    {
                        "provider": "privacy_guard",
                        "status": "blocked",
                        "matched_rules": privacy_decision.matched_rules,
                    }
                ]
                if persist_state:
                    self._append_timing_log(
                        user_text=stripped,
                        session_id=session_id,
                        response=response,
                        saved_count=0,
                        timing=timing,
                        route_events=privacy_events,
                    )
                else:
                    response.tts_metadata["evaluation_route_events"] = privacy_events
                return response

            begin_route_trace(persist=persist_state)
            started_at = time.perf_counter()
            timing: dict[str, float] = {}
            prompt_context_started = time.perf_counter()
            system_prompt = build_system_prompt(
                memory_service=self._memory,
                user_text=stripped,
                session_id=session_id,
                speaker=speaker,
            )
            if session_id and persist_state:
                continuity_context = self._continuity.prompt_context(
                    session_id,
                    privacy_scopes=self._privacy_scopes_for_speaker(speaker),
                )
                if continuity_context:
                    system_prompt += "\n\n" + continuity_context
            if background_context and background_context.strip():
                system_prompt += f"\n\n# Stream Background Context\n{background_context.strip()}"
            if brief_mode:
                system_prompt += (
                    "\n\n# Live Voice Brevity\n"
                    "This is a low-latency live voice turn. "
                    "Reply in one short sentence when possible, or at most two very short sentences. "
                    "Prefer under 16 words unless clarity requires more. "
                    "Use concise spoken phrasing and avoid paragraphs. "
                    "Do not use markdown, lists, code fences, or quoted formatting."
                )
            if micro_mode:
                system_prompt += (
                    "\n\n# Live Voice Micro Reply\n"
                    "This is a micro-reply turn for live speech. "
                    "Reply with exactly one very short natural sentence. "
                    "Prefer 2 to 8 words. "
                    "No markdown. No code fences. No lists. No roleplay formatting. "
                    "No explanation unless the user explicitly asks for detail."
                )
            if fast_mode:
                system_prompt += (
                    "\n\n# Live Voice Formatting\n"
                    "For live speech, avoid numbered lists, bullet lists, section labels, and essay formatting. "
                    "Prefer natural spoken prose with short clauses and clear sentence boundaries."
                )
            legacy_delivery: DeliveryMode = "speech" if synthesize_speech else "text_only"
            system_prompt += structured_turn_instruction(default_delivery=legacy_delivery)
            timing["prompt_context_ms"] = round(
                (time.perf_counter() - prompt_context_started) * 1000,
                1,
            )
            tools_ok = persist_state and (speaker is None or speaker.tools_allowed)
            inferred_tool_calls = self._infer_tool_calls(stripped, speaker=speaker) if tools_ok else []
            draft_started = time.perf_counter()
            draft_request_started = time.perf_counter()
            draft_user_text = self._build_user_input_text(
                user_text=stripped,
                image=image,
                vision_analysis=vision_analysis,
            )
            prompt_layers = prompt_layers_from_system_prompt(system_prompt)
            budget_manager = ContextBudgetManager()

            def build_conversation_prompt(usable_input_tokens: int) -> tuple[str, str, dict[str, object]]:
                built = budget_manager.build(
                    layers=prompt_layers,
                    user_text=draft_user_text,
                    usable_input_tokens=usable_input_tokens,
                )
                return (
                    built.system_prompt,
                    built.user_text,
                    {
                        "compaction_level": built.compaction_level,
                        "compaction_actions": list(built.compaction_actions),
                        "included_layers": list(built.included_layers),
                    },
                )

            timing["draft_request_build_ms"] = round(
                (time.perf_counter() - draft_request_started) * 1000,
                1,
            )
            draft_llm_started = time.perf_counter()
            draft_reply = self._llm_adapter().generate_reply(
                system_prompt=system_prompt,
                user_text=draft_user_text,
                image_inputs=[image.to_llm_input()] if image is not None else None,
                call_context=LLMCallContext(
                    purpose="conversation_draft",
                    fast_mode=fast_mode,
                    brief_mode=brief_mode,
                    micro_mode=micro_mode,
                    reasoning_depth="heavy" if self._looks_like_heavy_reasoning_turn(stripped) else "normal",
                    persona_sensitive=not bool(inferred_tool_calls),
                    interaction_mode=interaction_mode,
                    cost_sensitive=bool(fast_mode or brief_mode or micro_mode),
                    minimum_context_tokens=4096,
                    prompt_builder=build_conversation_prompt,
                ),
            )
            timing["draft_llm_ms"] = round(
                (time.perf_counter() - draft_llm_started) * 1000,
                1,
            )
            timing["draft_reply_ms"] = round(
                (time.perf_counter() - draft_started) * 1000,
                1,
            )

            parse_started = time.perf_counter()
            parsed_turn = self._parse_turn_result(
                raw=draft_reply.text,
                system_prompt=system_prompt,
                user_text=draft_user_text,
                legacy_delivery=legacy_delivery,
                delivery_context=delivery_context,
                image=image,
            )
            turn_draft = parsed_turn.draft
            timing["turn_parse_ms"] = round((time.perf_counter() - parse_started) * 1000, 1)

            memory_write_ok = persist_state and (speaker is None or speaker.memory_write_allowed)

            requested_tool_calls = turn_draft.tool_calls if parsed_turn.structured and tools_ok else []
            primary_tool_calls = self._merge_tool_calls(inferred_tool_calls, requested_tool_calls)
            # Authorized tool calls always run synchronously and their raw output
            # never becomes communicable text.
            tool_started = time.perf_counter()
            inferred_results = self._execute_tool_calls(primary_tool_calls)
            timing["tool_exec_ms"] = round((time.perf_counter() - tool_started) * 1000, 1)

            final_reply_text = turn_draft.final_text
            final_reply_text = self._cleanup_spoken_text(final_reply_text)
            if inferred_results:
                direct_reply = self._render_direct_tool_reply(user_text=stripped, tool_results=inferred_results)
                if direct_reply is not None:
                    final_reply_text = self._cleanup_spoken_text(direct_reply)
                    timing["finalizer_ms"] = 0.0
                else:
                    finalizer_started = time.perf_counter()
                    final_reply_text = self._cleanup_spoken_text(self._finalize_reply_with_tools(
                        system_prompt=system_prompt,
                        user_text=self._build_user_input_text(
                            user_text=stripped,
                            image=image,
                            vision_analysis=vision_analysis,
                        ),
                        draft_reply=draft_reply.text,
                        tool_results=inferred_results,
                        image=image,
                    ))
                    timing["finalizer_ms"] = round((time.perf_counter() - finalizer_started) * 1000, 1)
            else:
                timing["finalizer_ms"] = 0.0

            final_reply_text = self._guard_finalized_text(
                final_reply_text,
                tool_results=inferred_results,
                delivery_context=delivery_context,
            )

            if fast_mode:
                # Fast path: skip the metadata LLM call entirely.
                # Return immediately with default emotion/motions.
                # Memory saving and metadata extraction run in a background thread.
                timing["metadata_ms"] = 0.0
                memory_started = time.perf_counter()
                structured_candidates = turn_draft.memory_candidates if parsed_turn.structured and memory_write_ok else []
                saved_count = (
                    self._memory.persist_candidates(structured_candidates, session_id=session_id)
                    if structured_candidates
                    else 0
                )
                timing["memory_persist_ms"] = round((time.perf_counter() - memory_started) * 1000, 1)
                timing["total_ms"] = round((time.perf_counter() - started_at) * 1000, 1)
                expressive = strip_hidden_style_tags(final_reply_text, default_emotion=turn_draft.emotion)
                safe_spoken = (
                    SpeechFilterResult(spoken_text="", blocked=False, matched_rules=[], action="allow", layers=[])
                    if turn_draft.action in {"stay_silent", "defer"}
                    else self._speech_filter.apply(
                        expressive.clean_text,
                        include_llm=not defer_llm_safety_review,
                    )
                )

                delivery_mode = resolve_delivery(
                    action=turn_draft.action,
                    requested=turn_draft.requested_delivery,
                    context=delivery_context,
                    speech_requested=synthesize_speech if speech_requested is None else speech_requested,
                    speech_allowed=speech_allowed,
                )
                communicated_text = safe_spoken.spoken_text if delivery_mode not in {"silent", "deferred"} else ""
                response = AssistantResponse(
                    spoken_text=communicated_text,
                    display_text=communicated_text,
                    speech_text=communicated_text if delivery_mode == "speech" else "",
                    delivery_mode=delivery_mode,
                    response_action=turn_draft.action,
                    reason_code=turn_draft.reason_code,
                    emotion=self._normalize_emotion(turn_draft.state_updates.emotion or expressive.emotion or turn_draft.emotion),
                    voice_styles=list(dict.fromkeys([*turn_draft.voice_styles, *expressive.styles])),
                    internal_summary=turn_draft.internal_summary,
                    motions=turn_draft.motions,
                    tool_calls=primary_tool_calls,
                    tool_results=inferred_results,
                    memory_candidates=turn_draft.memory_candidates if memory_write_ok else [],
                    vision=vision_analysis,
                    state_updates=turn_draft.state_updates,
                )
                speech_filter_metadata = {
                    "level": settings.speech_filter_level,
                    "blocked": safe_spoken.blocked,
                    "matched_rules": safe_spoken.matched_rules,
                    "action": safe_spoken.action,
                    "layers": safe_spoken.layers,
                }
                response.tts_metadata["speech_filter"] = speech_filter_metadata
                if synthesize_speech and response.speech_text:
                    tts_started = time.perf_counter()
                    tts_result = self._tts_service().synthesize(
                        response.speech_text,
                        emotion=response.emotion,
                        styles=response.voice_styles,
                    )
                    response.audio_path = tts_result.audio_path
                    response.audio_content_type = tts_result.content_type
                    response.tts_metadata = dict(tts_result.metadata or {})
                    response.tts_metadata["speech_filter"] = speech_filter_metadata
                    timing["tts_ms"] = round((time.perf_counter() - tts_started) * 1000, 1)
                else:
                    timing["tts_ms"] = 0.0

                timing["total_ms"] = round((time.perf_counter() - started_at) * 1000, 1)
                response.timing_ms = dict(timing)
                route_events = take_route_trace()
                if persist_state:
                    self._append_timing_log(
                        user_text=stripped,
                        session_id=session_id,
                        response=response,
                        saved_count=saved_count,
                        timing=timing,
                        route_events=route_events,
                    )
                else:
                    response.tts_metadata["evaluation_route_events"] = route_events

                if memory_write_ok and not structured_candidates:
                    threading.Thread(
                        target=self._background_memory_save,
                        args=(stripped, response.display_text or "", session_id),
                        daemon=True,
                    ).start()

                if persist_state:
                    self._remember_assistant_state(user_text=stripped, reply_text=response.display_text or "", emotion=response.emotion, session_id=session_id)
                return response

            # Standard path: full metadata extraction + synchronous memory save.
            metadata_started = time.perf_counter()
            if parsed_turn.structured:
                extracted = {
                    "internal_summary": turn_draft.internal_summary,
                    "emotion": turn_draft.state_updates.emotion or turn_draft.emotion,
                    "motions": turn_draft.motions,
                    "tool_calls": [],
                    "memory_candidates": turn_draft.memory_candidates,
                }
            else:
                extracted = (
                    self._extract_turn_metadata(
                        user_text=stripped,
                        reply_text=draft_reply.text,
                        speaker=speaker,
                    )
                    if self._needs_metadata_pass(stripped, inferred_tool_calls)
                    else self._default_metadata()
                )
            timing["metadata_ms"] = round((time.perf_counter() - metadata_started) * 1000, 1)

            # Merge any additional tool calls the LLM suggested via metadata
            extra_tool_calls = self._merge_tool_calls(extracted["tool_calls"], []) if tools_ok and not parsed_turn.structured else []
            extra_results = self._execute_tool_calls(extra_tool_calls)
            if extra_results:
                direct_reply = self._render_direct_tool_reply(user_text=stripped, tool_results=extra_results)
                if direct_reply is not None:
                    final_reply_text = direct_reply
                else:
                    finalizer_started = time.perf_counter()
                    final_reply_text = self._finalize_reply_with_tools(
                        system_prompt=system_prompt,
                        user_text=self._build_user_input_text(
                            user_text=stripped,
                            image=image,
                            vision_analysis=vision_analysis,
                        ),
                        draft_reply=final_reply_text,
                        tool_results=extra_results,
                        image=image,
                    )
                    timing["finalizer_ms"] = round((time.perf_counter() - finalizer_started) * 1000, 1)

            all_tool_calls = primary_tool_calls + extra_tool_calls
            all_tool_results = inferred_results + extra_results
            final_reply_text = self._guard_finalized_text(
                final_reply_text,
                tool_results=all_tool_results,
                delivery_context=delivery_context,
            )
            expressive = strip_hidden_style_tags(final_reply_text, default_emotion=extracted["emotion"])
            response_emotion = self._normalize_emotion(expressive.emotion)
            safe_spoken = (
                SpeechFilterResult(spoken_text="", blocked=False, matched_rules=[], action="allow", layers=[])
                if turn_draft.action in {"stay_silent", "defer"}
                else self._speech_filter.apply(
                    expressive.clean_text,
                    include_llm=not defer_llm_safety_review,
                )
            )
            final_reply_text = safe_spoken.spoken_text

            memory_candidates = (
                (extracted["memory_candidates"] or self._build_memory_candidates(
                    user_text=stripped,
                    reply_text=final_reply_text,
                ))
                if memory_write_ok
                else []
            )
            memory_started = time.perf_counter()
            saved_count = self._memory.persist_candidates(memory_candidates, session_id=session_id) if memory_write_ok else 0
            timing["memory_persist_ms"] = round((time.perf_counter() - memory_started) * 1000, 1)

            delivery_mode = resolve_delivery(
                action=turn_draft.action,
                requested=turn_draft.requested_delivery,
                context=delivery_context,
                speech_requested=synthesize_speech if speech_requested is None else speech_requested,
                speech_allowed=speech_allowed,
            )
            communicated_text = final_reply_text if delivery_mode not in {"silent", "deferred"} else ""
            response = AssistantResponse(
                spoken_text=communicated_text,
                display_text=communicated_text,
                speech_text=communicated_text if delivery_mode == "speech" else "",
                delivery_mode=delivery_mode,
                response_action=turn_draft.action,
                reason_code=turn_draft.reason_code,
                emotion=response_emotion,
                voice_styles=list(dict.fromkeys([*turn_draft.voice_styles, *expressive.styles])),
                internal_summary=extracted["internal_summary"],
                motions=extracted["motions"],
                tool_calls=all_tool_calls,
                tool_results=all_tool_results,
                memory_candidates=memory_candidates,
                vision=vision_analysis,
                state_updates=turn_draft.state_updates,
            )
            speech_filter_metadata = {
                "level": settings.speech_filter_level,
                "blocked": safe_spoken.blocked,
                "matched_rules": safe_spoken.matched_rules,
                "action": safe_spoken.action,
                "layers": safe_spoken.layers,
            }
            response.tts_metadata["speech_filter"] = speech_filter_metadata
            if synthesize_speech and response.speech_text:
                tts_started = time.perf_counter()
                tts_result = self._tts_service().synthesize(response.speech_text, emotion=response.emotion, styles=response.voice_styles)
                response.audio_path = tts_result.audio_path
                response.audio_content_type = tts_result.content_type
                response.tts_metadata = dict(tts_result.metadata or {})
                response.tts_metadata["speech_filter"] = speech_filter_metadata
                timing["tts_ms"] = round((time.perf_counter() - tts_started) * 1000, 1)
            else:
                timing["tts_ms"] = 0.0
            timing["total_ms"] = round((time.perf_counter() - started_at) * 1000, 1)
            response.timing_ms = dict(timing)
            route_events = take_route_trace()
            if persist_state:
                self._append_timing_log(
                    user_text=stripped,
                    session_id=session_id,
                    response=response,
                    saved_count=saved_count,
                    timing=timing,
                    route_events=route_events,
                )
                self._remember_assistant_state(user_text=stripped, reply_text=response.display_text or "", emotion=response.emotion, session_id=session_id)
            else:
                response.tts_metadata["evaluation_route_events"] = route_events
            return response
        except GammaError:
            raise
        except Exception as exc:
            raise ConversationError(f"Conversation pipeline failed: {exc}") from exc

    def _parse_turn_result(
        self,
        *,
        raw: str,
        system_prompt: str,
        user_text: str,
        legacy_delivery: DeliveryMode,
        delivery_context: str,
        image: VisionImage | None,
    ) -> ParsedTurn:
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
                draft=self._fallback_turn_draft(delivery_context=delivery_context),
                structured=True,
            )

    @staticmethod
    def _fallback_turn_draft(*, delivery_context: str) -> ConversationTurnDraft:
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
            final_text="I’m having trouble forming a safe response right now. Please try again.",
            internal_summary="The turn result failed the communication contract and used a safe direct fallback.",
            reason_code="contract_fallback_direct",
        )

    def _guard_finalized_text(
        self,
        text: str,
        *,
        tool_results: list[ToolExecutionResult],
        delivery_context: str,
    ) -> str:
        value = (text or "").strip()
        try:
            self._turn_parser.validate_communicable_text(value)
            raw_outputs = {item.output.strip() for item in tool_results if item.output.strip()}
            if value and value in raw_outputs:
                raise TurnContractError("raw_tool_output_selected")
            return value
        except TurnContractError:
            return self._fallback_turn_draft(delivery_context=delivery_context).final_text

    def _cleanup_spoken_text(self, text: str) -> str:
        cleaned = _CODE_FENCE_RE.sub("", text or "")
        cleaned = _INLINE_TICK_RE.sub(r"\1", cleaned)
        cleaned = cleaned.replace("*", " ").replace("#", " ")
        cleaned = re.sub(r"^\s*[-•]\s+", "", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
        return cleaned

    @staticmethod
    def _privacy_scopes_for_speaker(speaker: SpeakerProfile | None) -> set[str]:
        if speaker is not None and speaker.memory_read_allowed:
            return {"local_private", "local_generic", "public"}
        return {"local_generic", "public"}

    def _background_memory_save(self, user_text: str, reply_text: str, session_id: str | None) -> None:
        """Persist memory candidates in a background thread (fast_mode only)."""
        try:
            candidates = self._build_memory_candidates(user_text=user_text, reply_text=reply_text)
            if candidates:
                self._memory.persist_candidates(candidates, session_id=session_id)
        except Exception:
            pass  # background thread — never propagate

    def _remember_assistant_state(self, *, user_text: str, reply_text: str, emotion: str, session_id: str | None = None) -> None:
        if not settings.assistant_state_enabled:
            return
        try:
            self._assistant_state.update(emotion=emotion, user_text=user_text, reply_text=reply_text)
            self._emotion_memory.update_from_turn(
                emotion=emotion,
                user_text=user_text,
                reply_text=reply_text,
                session_id=session_id,
            )
        except Exception:
            pass

    def _llm_adapter(self):
        if self._llm is None:
            self._llm = build_llm_adapter()
        return self._llm

    def _tts_service(self) -> TTSService:
        if self._tts is None:
            self._tts = TTSService()
        return self._tts

    def _extract_turn_metadata(self, user_text: str, reply_text: str, speaker: SpeakerProfile | None = None) -> dict:
        # save_core_memory is owner-only — hide it from the tool list for everyone else
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
            "Only emit tool_calls when the user is explicitly asking for current system, provider, artifact, or memory state that these tools can answer.\n"
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

    def _execute_tool_calls(self, tool_calls: list[ToolCall]) -> list[ToolExecutionResult]:
        results: list[ToolExecutionResult] = []
        for call in tool_calls[:3]:
            tool = self._tools.get(call.tool)
            if tool is None:
                results.append(
                    ToolExecutionResult(
                        tool=call.tool,
                        ok=False,
                        output=f"Unknown tool: {call.tool}",
                        metadata={"reason": "unknown_tool", "available_tools": self._tools.names()},
                    )
                )
                continue
            try:
                result = tool.run(**call.args)
                results.append(
                    ToolExecutionResult(
                        tool=call.tool,
                        ok=result.ok,
                        output=result.output,
                        metadata=result.metadata,
                    )
                )
            except Exception as exc:
                results.append(
                    ToolExecutionResult(
                        tool=call.tool,
                        ok=False,
                        output=f"Tool execution failed: {exc}",
                        metadata={"reason": "execution_error"},
                    )
                )
        return results

    def _infer_tool_calls(self, user_text: str, speaker: SpeakerProfile | None = None) -> list[ToolCall]:
        lowered = user_text.lower()
        inferred: list[ToolCall] = []
        is_owner = speaker is None or speaker.is_owner

        if any(term in lowered for term in ["provider", "providers", "ollama", "qwen", "stt", "tts", "llm"]):
            if any(term in lowered for term in ["status", "using", "use", "configured", "running", "right now", "current"]):
                inferred.append(ToolCall(tool="provider_status", args={}))

        if "known people" in lowered or "who do you know" in lowered:
            inferred.append(ToolCall(tool="known_people", args={}))

        if "memory stats" in lowered or ("memory" in lowered and any(term in lowered for term in ["stats", "count", "counts", "database"])):
            inferred.append(ToolCall(tool="memory_stats", args={}))

        if any(term in lowered for term in ["recent artifacts", "recent audio", "generated audio", "latest artifacts", "latest audio"]):
            inferred.append(ToolCall(tool="recent_artifacts", args={}))

        if any(term in lowered for term in ["what do you remember", "do you remember", "remember about", "what do you know about", "search memory"]):
            inferred.append(ToolCall(tool="search_memory", args={"query": user_text, "limit": 5}))

        if any(term in lowered for term in ["remember this", "save this", "make a note", "note that", "store this", "keep in mind that"]):
            inferred.append(
                ToolCall(
                    tool="save_memory",
                    args={
                        "type": "episodic",
                        "text": user_text,
                        "importance": 0.8,
                        "tags": ["explicit_save"],
                    },
                )
            )

        # Core memory — owner only. Trigger: "remember: <fact>"
        if is_owner and lowered.startswith("remember:"):
            fact = user_text[len("remember:"):].strip()
            if fact:
                inferred.append(ToolCall(tool="save_core_memory", args={"fact": fact}))

        return inferred[:3]

    def _merge_tool_calls(self, primary: list[ToolCall], fallback: list[ToolCall]) -> list[ToolCall]:
        merged: list[ToolCall] = []
        seen: set[tuple[str, str]] = set()
        for collection in (primary, fallback):
            for call in collection:
                key = (call.tool, json.dumps(call.args, sort_keys=True))
                if key in seen:
                    continue
                seen.add(key)
                merged.append(call)
        return merged[:3]

    def _finalize_reply_with_tools(
        self,
        *,
        system_prompt: str,
        user_text: str,
        draft_reply: str,
        tool_results: list[ToolExecutionResult],
        image: VisionImage | None = None,
    ) -> str:
        tool_summary = []
        for result in tool_results:
            tool_summary.append(
                json.dumps(
                    {
                        "tool": result.tool,
                        "ok": result.ok,
                        "output": result.output,
                    },
                    ensure_ascii=False,
                )
            )
        finalizer_prompt = (
            system_prompt
            + "\n\nYou are revising a draft reply after safe local tool calls were executed.\n"
            "Use tool results when they help answer the user directly.\n"
            "Do not mention raw JSON unless the user clearly wants raw debug data.\n"
            "If a tool failed, gracefully answer with what you do know.\n"
            "Return the same exact structured turn JSON contract. Set action to reply or acknowledge, "
            "put only the finalized user-facing response in final_text, and request no additional tools."
        )
        finalizer_input = (
            f"User message:\n{user_text}\n\n"
            f"Draft reply:\n{draft_reply}\n\n"
            "Tool results:\n"
            + "\n".join(tool_summary)
        )
        try:
            raw_final = self._llm_adapter().generate_reply(
                system_prompt=finalizer_prompt,
                user_text=finalizer_input,
                image_inputs=[image.to_llm_input()] if image is not None else None,
                call_context=LLMCallContext(
                    purpose="tool_finalizer",
                    reasoning_depth="normal",
                    persona_sensitive=True,
                    interaction_mode="tooling",
                ),
            ).text
            parsed = self._turn_parser.parse(raw_final, legacy_delivery="speech")
            if parsed.draft.action not in {"reply", "acknowledge"}:
                raise TurnContractError("tool_finalizer_did_not_finalize")
            return parsed.draft.final_text
        except Exception:
            return draft_reply

    def _build_user_input_text(
        self,
        *,
        user_text: str,
        image: VisionImage | None,
        vision_analysis: VisionAnalysis | None = None,
    ) -> str:
        if image is None:
            return user_text
        image_note = "The user attached an image."
        if image.filename:
            image_note = f"The user attached an image named {image.filename}."
        if image.stored_path:
            image_note += f" Stored at {image.stored_path}."
        if vision_analysis is None:
            return f"{user_text}\n\n{image_note}\nUse the image directly when answering."
        analysis_lines = [
            f"Image type: {vision_analysis.image_type}.",
            f"Scene summary: {vision_analysis.summary}",
        ]
        if vision_analysis.visible_text:
            analysis_lines.append(f"Visible text:\n{vision_analysis.visible_text}")
        if vision_analysis.key_text_blocks:
            block_summary = "; ".join(
                f"{block.label}: {block.text[:120]}" for block in vision_analysis.key_text_blocks[:4]
            )
            analysis_lines.append(f"Key text blocks: {block_summary}")
        if vision_analysis.interface_elements:
            ui_summary = ", ".join(
                f"{item.name} ({item.element_type})" for item in vision_analysis.interface_elements[:6]
            )
            analysis_lines.append(f"Interface elements: {ui_summary}.")
        if vision_analysis.document_structure:
            analysis_lines.append("Document structure: " + " ".join(vision_analysis.document_structure))
        if vision_analysis.likely_actions:
            analysis_lines.append("Likely actions: " + " ".join(vision_analysis.likely_actions))
        if vision_analysis.objects:
            object_summary = ", ".join(obj.name for obj in vision_analysis.objects[:6])
            analysis_lines.append(f"Detected notable objects: {object_summary}.")
        if vision_analysis.spatial_notes:
            analysis_lines.append("Spatial notes: " + " ".join(vision_analysis.spatial_notes))
        analysis_lines.append(f"Vision confidence: {vision_analysis.confidence:.2f}.")
        return (
            f"{user_text}\n\n{image_note}\n"
            "Here is structured vision context for the same image:\n"
            + "\n".join(analysis_lines)
            + "\nAnswer the user using both the image and this structured context."
        )

    def _render_direct_tool_reply(self, *, user_text: str, tool_results: list[ToolExecutionResult]) -> str | None:
        if not tool_results or any(not result.ok for result in tool_results):
            return None
        if len(tool_results) == 1:
            result = tool_results[0]
            if result.tool == "provider_status":
                return self._format_provider_status_reply(result.metadata)
            if result.tool == "search_memory":
                return self._format_search_memory_reply(result.metadata)
            if result.tool == "memory_stats":
                return self._format_memory_stats_reply(result.metadata)
            if result.tool == "known_people":
                return self._format_known_people_reply(result.output)
            if result.tool == "recent_artifacts":
                return self._format_recent_artifacts_reply(result.output)
            if result.tool == "save_memory":
                return self._format_save_memory_reply(result.metadata)
            if result.tool == "save_core_memory":
                return self._format_save_core_memory_reply(result.metadata)
        return None

    def _format_provider_status_reply(self, metadata: dict) -> str:
        llm = metadata.get("llm", {}) if isinstance(metadata, dict) else {}
        stt = metadata.get("stt", {}) if isinstance(metadata, dict) else {}
        tts = metadata.get("tts", {}) if isinstance(metadata, dict) else {}
        llm_health = "healthy" if llm.get("health", {}).get("ok") else "down"
        tts_health = "healthy" if tts.get("health", {}).get("ok") else "down"
        vision_capability = llm.get("vision_capability", {}) if isinstance(llm, dict) else {}
        vision_enabled = bool(llm.get("vision_enabled")) if isinstance(llm, dict) else False
        vision_status = "disabled"
        if vision_enabled:
            vision_status = "ready" if vision_capability.get("supports_vision") else "not supported by model"
        return (
            f"LLM: {llm.get('provider', 'n/a')} ({llm.get('model', 'n/a')}) is {llm_health}. "
            f"LLM vision: {vision_status}. "
            f"STT: {stt.get('provider', 'n/a')} ({stt.get('model', 'n/a')}) on {stt.get('device', 'n/a')}. "
            f"TTS: {tts.get('provider', 'n/a')} ({tts.get('model', 'n/a')}) is {tts_health}."
        )

    def _format_search_memory_reply(self, metadata: dict) -> str:
        if not isinstance(metadata, dict):
            return "I couldn't read anything useful from memory."
        profile_count = int(metadata.get("profile_count", 0) or 0)
        episodic_count = int(metadata.get("episodic_count", 0) or 0)
        if profile_count == 0 and episodic_count == 0:
            return "I don't have anything useful stored for that yet."
        return f"I found {profile_count} profile facts and {episodic_count} episodic memories relevant to that."

    def _format_memory_stats_reply(self, metadata: dict) -> str:
        if not isinstance(metadata, dict):
            return "I couldn't read the memory stats."
        return (
            f"Memory has {metadata.get('profile_count', 0)} profile facts, "
            f"{metadata.get('episodic_count', 0)} episodic memories, and "
            f"{metadata.get('known_people_count', 0)} known people."
        )

    def _format_known_people_reply(self, output: str) -> str:
        try:
            people = json.loads(output)
        except Exception:
            return "I couldn't parse the known people list."
        if not people:
            return "I don't have any known people stored yet."
        names = []
        for person in people[:5]:
            if not isinstance(person, dict):
                continue
            name = person.get("name") or "Unnamed"
            relationship = person.get("relationship_to_user")
            names.append(f"{name} ({relationship})" if relationship else str(name))
        return "Known people: " + ", ".join(names) + "."

    def _format_recent_artifacts_reply(self, output: str) -> str:
        try:
            artifacts = json.loads(output)
        except Exception:
            return "I couldn't parse the recent artifacts."
        if not artifacts:
            return "There are no recent artifacts yet."
        names = [artifact.get("name", "unknown") for artifact in artifacts[:5] if isinstance(artifact, dict)]
        return "Recent artifacts: " + ", ".join(names) + "."

    def _format_save_memory_reply(self, metadata: dict) -> str:
        if not isinstance(metadata, dict):
            return "Noted."
        saved = int(metadata.get("saved", 0) or 0)
        if saved > 0:
            return "Noted. I'll keep that in mind."
        return "I already had that stored."

    def _format_save_core_memory_reply(self, metadata: dict) -> str:
        if not isinstance(metadata, dict):
            return "Stored."
        if metadata.get("duplicate"):
            return "I already have that."
        fact = metadata.get("fact", "")
        if fact:
            return f"Stored permanently: {fact}"
        return "Stored."

    def _build_memory_candidates(self, user_text: str, reply_text: str) -> list[MemoryCandidate]:
        candidates: list[MemoryCandidate] = []
        stripped = user_text.strip()
        lowered = stripped.lower()
        has_structured_candidate = False
        if "my name is " in lowered:
            candidates.append(
                MemoryCandidate(
                    type="profile",
                    text=stripped,
                    importance=0.9,
                    tags=["identity"],
                )
            )
            has_structured_candidate = True
        elif any(phrase in lowered for phrase in ["remember that ", "my favorite", "i like ", "i prefer ", "i dislike ", "i do not like ", "i hate "]):
            candidates.append(
                MemoryCandidate(
                    type="profile",
                    text=stripped,
                    importance=0.84,
                    tags=["preference"],
                )
            )
            has_structured_candidate = True
        elif any(phrase in lowered for phrase in ["i am working on ", "i'm working on ", "the main project is "]):
            candidates.append(
                MemoryCandidate(
                    type="project",
                    text=stripped,
                    importance=0.86,
                    tags=["project_state"],
                )
            )
            has_structured_candidate = True
        elif lowered.startswith("this is ") and len(stripped.split()) >= 4:
            candidates.append(
                MemoryCandidate(
                    type="profile",
                    text=stripped,
                    importance=0.8,
                    tags=["other_person"],
                    subject_type="other_person",
                    subject_name=self._extract_named_person(stripped),
                )
            )
            has_structured_candidate = True
        elif any(phrase in lowered for phrase in ["my friend ", "my brother ", "my sister ", "my coworker ", "my manager ", "my wife ", "my husband ", "my partner "]):
            candidates.append(
                MemoryCandidate(
                    type="profile",
                    text=stripped,
                    importance=0.82,
                    tags=["other_person"],
                    subject_type="other_person",
                    subject_name=self._extract_named_person(stripped),
                    relationship_to_user=self._extract_relationship_label(lowered),
                )
            )
            has_structured_candidate = True
        if len(stripped.split()) >= 8 and self._should_store_episodic_memory(lowered, has_structured_candidate=has_structured_candidate):
            candidates.append(
                MemoryCandidate(
                    type="episodic",
                    text=f"User said: {stripped} | Assistant replied: {reply_text}",
                    importance=self._episodic_importance(lowered),
                    tags=self._episodic_tags(lowered),
                )
            )
        return candidates[:3]

    def _should_store_episodic_memory(self, lowered_user_text: str, *, has_structured_candidate: bool = False) -> bool:
        if self._memory_personality() == "assistant":
            return self._assistant_should_store_episodic_memory(
                lowered_user_text,
                has_structured_candidate=has_structured_candidate,
            )
        return self._entertainer_should_store_episodic_memory(
            lowered_user_text,
            has_structured_candidate=has_structured_candidate,
        )

    def _entertainer_should_store_episodic_memory(
        self,
        lowered_user_text: str,
        *,
        has_structured_candidate: bool = False,
    ) -> bool:
        word_count = len(lowered_user_text.split())
        if word_count < 12:
            return False
        low_signal_markers = {"hi", "hello", "thanks", "thank you", "ok", "okay", "cool", "nice", "good morning", "good night"}
        if lowered_user_text.strip() in low_signal_markers:
            return False
        memorable_markers = (
            "remember",
            "deadline",
            "anniversary",
            "birthday",
            "graduated",
            "promotion",
            "promoted",
            "engaged",
            "married",
            "broke up",
            "passed away",
            "died",
            "won",
            "lost",
            "celebrate",
            "celebrating",
            "proud of",
            "upset",
            "frustrated",
            "sad about",
            "excited about",
            "nervous about",
            "afraid",
            "diagnosed",
            "moving to",
            "moving into",
            "starting a new job",
            "quit my job",
            "apologize",
            "sorry about",
            "never forget",
            "please don't forget",
            "important to me",
            "means a lot to me",
            "big day",
        )
        if any(marker in lowered_user_text for marker in memorable_markers):
            return True
        if has_structured_candidate:
            return False
        routine_work_markers = (
            "project",
            "working on",
            "plan",
            "issue",
            "bug",
            "error",
            "task",
            "todo",
            "fixing",
        )
        if any(marker in lowered_user_text for marker in routine_work_markers):
            return False
        return word_count >= 18 and any(marker in lowered_user_text for marker in ["always", "never", "every time", "keeps happening"])

    def _assistant_should_store_episodic_memory(
        self,
        lowered_user_text: str,
        *,
        has_structured_candidate: bool = False,
    ) -> bool:
        if len(lowered_user_text.split()) < 10:
            return False
        low_signal_markers = {"hi", "hello", "thanks", "thank you", "ok", "okay", "cool", "nice", "good morning", "good night"}
        if lowered_user_text.strip() in low_signal_markers:
            return False
        strong_markers = (
            "remember",
            "project",
            "working on",
            "plan",
            "deadline",
            "prefer",
            "favorite",
            "friend",
            "manager",
            "brother",
            "sister",
            "issue",
            "bug",
            "error",
        )
        if any(marker in lowered_user_text for marker in strong_markers):
            return True
        if has_structured_candidate:
            return False
        return False

    def _episodic_importance(self, lowered_user_text: str) -> float:
        if self._memory_personality() == "assistant":
            return self._assistant_episodic_importance(lowered_user_text)
        return self._entertainer_episodic_importance(lowered_user_text)

    def _entertainer_episodic_importance(self, lowered_user_text: str) -> float:
        if any(marker in lowered_user_text for marker in ["never forget", "please don't forget", "important to me", "means a lot to me"]):
            return 0.86
        if any(marker in lowered_user_text for marker in ["birthday", "anniversary", "graduated", "promotion", "engaged", "married", "passed away", "died"]):
            return 0.82
        if any(marker in lowered_user_text for marker in ["won", "lost", "celebrate", "proud of", "upset", "frustrated", "sad about", "excited about", "nervous about"]):
            return 0.76
        return 0.7

    def _assistant_episodic_importance(self, lowered_user_text: str) -> float:
        if any(marker in lowered_user_text for marker in ["deadline", "urgent", "important", "remember"]):
            return 0.72
        if any(marker in lowered_user_text for marker in ["project", "working on", "plan", "issue", "bug", "error"]):
            return 0.64
        return 0.56

    def _episodic_tags(self, lowered_user_text: str) -> list[str]:
        if self._memory_personality() == "assistant":
            return self._assistant_episodic_tags(lowered_user_text)
        return self._entertainer_episodic_tags(lowered_user_text)

    def _entertainer_episodic_tags(self, lowered_user_text: str) -> list[str]:
        tags = ["conversation"]
        if any(marker in lowered_user_text for marker in ["birthday", "anniversary", "graduated", "promotion", "engaged", "married", "moving to", "starting a new job"]):
            tags.append("milestone")
        if any(marker in lowered_user_text for marker in ["won", "lost", "celebrate", "proud of"]):
            tags.append("event")
        if any(marker in lowered_user_text for marker in ["upset", "frustrated", "sad about", "excited about", "nervous about", "afraid"]):
            tags.append("emotion")
        if any(marker in lowered_user_text for marker in ["remember", "never forget", "please don't forget", "important to me", "means a lot to me"]):
            tags.append("durable")
        return tags[:4]

    def _assistant_episodic_tags(self, lowered_user_text: str) -> list[str]:
        tags = ["conversation"]
        if any(marker in lowered_user_text for marker in ["project", "working on", "plan"]):
            tags.append("project")
        if any(marker in lowered_user_text for marker in ["issue", "bug", "error"]):
            tags.append("problem")
        if "remember" in lowered_user_text:
            tags.append("durable")
        return tags[:4]

    def _memory_personality(self) -> str:
        personality = (settings.memory_personality or "").strip().lower()
        if personality in {"assistant", "entertainer"}:
            return personality
        return "entertainer"

    def _extract_named_person(self, text: str) -> str | None:
        relation_match = re.search(
            r"(?i:\bmy\s+(?:friend|brother|sister|coworker|manager|wife|husband|partner)\s+)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b",
            text,
        )
        if relation_match:
            return relation_match.group(1)
        intro_match = re.search(r"(?i:\bthis is\s+)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b", text)
        if intro_match:
            return intro_match.group(1)
        match = re.search(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b", text)
        if not match:
            return None
        return match.group(1)

    def _extract_relationship_label(self, lowered: str) -> str | None:
        for label in ["friend", "brother", "sister", "coworker", "manager", "wife", "husband", "partner"]:
            if f"my {label} " in lowered:
                return label
        return None

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

    def _infer_subject_type(self, *, text: str, tags: list[str]) -> str:
        lowered = text.lower()
        if any(tag.lower() in {"friend", "family", "coworker", "partner", "other_person"} for tag in tags):
            return "other_person"
        if lowered.startswith(("my friend ", "my brother ", "my sister ", "my mom ", "my mother ", "my dad ", "my father ", "my coworker ", "my manager ", "this is ")):
            return "other_person"
        return "primary_user"

    def _normalize_subject_name(self, value: object) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = " ".join(value.strip().split())
        return normalized[:120] if normalized else None

    def _append_timing_log(
        self,
        *,
        user_text: str,
        session_id: str | None,
        response: AssistantResponse,
        saved_count: int,
        timing: dict[str, float],
        route_events: list[dict[str, object]] | None = None,
    ) -> None:
        log_dir = settings.data_dir / "runtime"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "conversation.timings.jsonl"
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "session_id": session_id,
            "user_text_preview": user_text[:140],
            "response_preview": (response.display_text or "")[:140],
            "tool_call_count": len(response.tool_calls),
            "tool_result_count": len(response.tool_results),
            "memory_candidate_count": len(response.memory_candidates),
            "memory_saved_count": saved_count,
            "synthesize_speech": bool(response.speech_text and (response.audio_path or response.audio_content_type)),
            "timing_ms": timing,
            "route_events": route_events or [],
        }
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
