from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Callable

from ..config import settings
from ..errors import ConversationError, GammaError
from ..identity.profile import SpeakerProfile
from ..identity.resolver import IdentityResolver
from ..llm.base import LLMCallContext, LLMReply
from ..llm.capabilities import estimate_text_tokens
from ..llm.context_budget import ContextBudgetManager, prompt_layers_from_system_prompt
from ..llm.factory import build_llm_adapter
from ..llm.route_trace import begin_route_trace, take_route_trace
from ..memory.attribution import MemoryOrigin
from ..memory.continuity import ContinuityService, JournalOwnershipError
from ..memory.service import MemoryService
from ..persona.assistant_state import AssistantStateStore
from ..persona.emotion_service import EmotionMemoryService
from ..persona.loader import build_system_prompt, load_persona_config
from ..safety.privacy_guard import review_private_info_request
from ..safety.speech_filter import SpeechFilterResult, SpeechSafetyFilter
from ..schemas.conversation import RecipientContext, SpeakerContext
from ..schemas.response import AssistantResponse, ConversationTurnDraft, DeliveryMode, VisionAnalysis
from ..tools.disclosure import disclose_tool_call
from ..tools.registry import ToolRegistry
from ..vision.service import VisionImage, VisionService
from ..voice.expressive_text import strip_hidden_style_tags
from ..voice.tts import TTSService
from .memory_candidates import MemoryCandidatesMixin
from .shared import _memory_candidate_view, _recipient_context
from .tool_flow import ToolFlowMixin
from .turn_contract import StructuredTurnParser, resolve_delivery, structured_turn_instruction
from .turn_metadata import TurnMetadataMixin


# Shared audiences whose own recent turns are given back to Shana. Only rows
# written under these scopes are read, never legacy "public" rows.
SHARED_HISTORY_SCOPES = {"public_stream", "public_discord"}
SHARED_HISTORY_TOKENS = 1200

class ConversationService(TurnMetadataMixin, ToolFlowMixin, MemoryCandidatesMixin):
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
        recipient: RecipientContext | None = None,
        owner_verified: bool = False,
        stream_context: str | None = None,
        speaker_label: str | None = None,
        viewer_notes: list[str] | None = None,
        relationship_note: str | None = None,
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
            owner_verified: The caller proved this turn comes from the owner (the
                dashboard's owner channel). The speaker is then the owner and, unless a
                recipient is given, the reply is owner-private, so memory and history load.
            
        Returns:
            AssistantResponse with spoken text and timing.
        """
        if owner_verified and recipient is None and delivery_context != "public_stream":
            recipient = RecipientContext(kind="owner_private", owner_authorized=True)
        recipient = _recipient_context(recipient, delivery_context=delivery_context)
        speaker = self._speaker(speaker_ctx, recipient, owner_verified)
        exchange_id = None
        tool_checkpoint = None
        presence_snapshot = None
        if session_id and not evaluation_mode:
            try:
                from ..presence import load_presence_state

                presence_snapshot = load_presence_state()
            except Exception:
                pass
        privacy_scope = recipient.privacy_scope
        if session_id and not evaluation_mode:
            continuity = self._continuity
            exchange_id = continuity.begin_exchange(
                session_id=session_id,
                text=user_text,
                speaker_name=(speaker_label if speaker_label and not recipient.allows_private_memory else speaker.name),
                input_source=speaker_ctx.source if speaker_ctx is not None else "local",
                privacy_scope=privacy_scope,
            )
            if exchange_id:
                def tool_checkpoint() -> None:
                    continuity.require_owned_pending_exchange(
                        exchange_id, session_id=session_id, privacy_scope=privacy_scope,
                    )
        try:
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
                    stream_context=stream_context,
                    speaker_label=speaker_label if not recipient.allows_private_memory else None,
                    viewer_notes=viewer_notes if recipient.privacy_scope in SHARED_HISTORY_SCOPES else None,
                    relationship_note=relationship_note if recipient.privacy_scope in SHARED_HISTORY_SCOPES else None,
                    persist_state=not evaluation_mode,
                    memory_origin=MemoryOrigin.from_speaker(speaker,
                        source=speaker_ctx.source if speaker_ctx is not None else "local",
                        privacy_scope=privacy_scope, session_id=session_id, turn_id=exchange_id),
                    interaction_mode="evaluation" if evaluation_mode else "chat",
                    delivery_context=delivery_context or ("direct_voice" if synthesize_speech else "direct_text"),
                    speech_allowed=speech_allowed,
                    speech_requested=synthesize_speech if speech_requested is None else speech_requested,
                    recipient=recipient,
                    tool_checkpoint=tool_checkpoint,
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
                    output_target=recipient.target_policy or "dashboard_monitor",
                    privacy_scope=privacy_scope,
                    internal_summary=response.internal_summary,
                    state_updates=response.state_updates.model_dump(exclude_none=True),
                    tool_metadata={
                        "tool_calls": [item.model_dump() for item in response.tool_calls],
                        "tool_results": [item.model_dump() for item in response.tool_results],
                    },
                )
                if presence_snapshot is not None:
                    self._record_presence_interaction(session_id, expected_state=presence_snapshot)
            if not evaluation_mode and recipient.allows_private_memory and speaker.is_owner:
                self._record_owner_relationship(user_text)
            return response
        except BaseException as interruption:
            if isinstance(interruption, Exception):
                raise
            if exchange_id:
                try:
                    self._continuity.fail_exchange(exchange_id, status="interrupted")
                except Exception as cleanup_error:
                    logging.getLogger(__name__).warning(
                        "Journal interruption cleanup failed (%s)",
                        type(cleanup_error).__name__,
                    )
            raise

    def _record_owner_relationship(self, user_text: str) -> None:
        """Count a finished owner-private turn. Shared turns are counted by the stream brain."""
        from ..memory.relationships import OWNER_AUDIENCE, OWNER_KEY, RelationshipStore
        from ..persona.loader import owner_baseline_familiarity

        try:
            RelationshipStore(self._memory._engine).record(
                OWNER_KEY, user_text, audience=OWNER_AUDIENCE, trust_level="owner",
                neutral=owner_baseline_familiarity())
        except Exception as exc:
            logging.getLogger(__name__).warning("Owner relationship update failed (%s)", type(exc).__name__)

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
    def _record_presence_interaction(session_id: str, *, expected_state: dict | None = None) -> None:
        try:
            from ..presence import presence_snapshot_matches, presence_state_path, update_presence_state, utc_now

            def mutate(state: dict) -> None:
                if expected_state is not None and not presence_snapshot_matches(
                    expected_state, state, missing_file=not presence_state_path().resolve().exists(),
                ):
                    return
                state.setdefault("lifecycle", {})["last_interaction_at"] = utc_now()
                state.setdefault("activity", {})["session_id"] = session_id
                scheduler = dict((state.setdefault("autonomy", {})).get("scheduler") or {})
                scheduler["attempts_for_topic"] = 0
                state["autonomy"]["scheduler"] = scheduler

            update_presence_state(mutate)
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
        recipient: RecipientContext | None = None,
        owner_verified: bool = False,
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
        if owner_verified and recipient is None:
            recipient = RecipientContext(kind="owner_private", owner_authorized=True)
        recipient = _recipient_context(recipient)
        load_persona_config(private=recipient.allows_private_memory)
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
        image_speaker = self._speaker(speaker_ctx, recipient, owner_verified)
        return self._respond(
            memory_origin=MemoryOrigin.from_speaker(image_speaker,
                source=speaker_ctx.source if speaker_ctx is not None else "local",
                privacy_scope=recipient.privacy_scope, session_id=session_id),
            user_text=user_text,
            session_id=session_id,
            synthesize_speech=synthesize_speech,
            image=image,
            vision_analysis=vision_analysis,
            speaker=image_speaker,
            delivery_context="direct_voice" if synthesize_speech else "direct_text",
            recipient=recipient,
        )

    def _speaker(self, speaker_ctx, recipient: RecipientContext, owner_verified: bool) -> SpeakerProfile:
        """Who is talking. The owner only when proven: the owner channel, or in-process code
        that built an owner-private recipient (HTTP callers can't supply one). Naming no
        speaker otherwise means an unknown speaker, never the owner."""
        if owner_verified or (speaker_ctx is None and recipient.kind == "owner_private" and recipient.owner_authorized):
            return self._identity.owner_profile()
        return self._identity.resolve(speaker_ctx)

    def respond_presence_wake(
        self,
        *,
        session_id: str,
        speaker: SpeakerProfile,
        wake_context: str,
        synthesize_speech: bool,
        recipient: RecipientContext | None = None,
    ) -> AssistantResponse:
        """Generate a dedicated Presence Wake opening without creating user memory."""
        recipient = _recipient_context(recipient)
        wake_context = wake_context if recipient.allows_private_memory else ""
        try:
            begin_route_trace()
            started_at = time.perf_counter()
            persona_prompt = build_system_prompt(
                memory_service=self._memory,
                user_text=None,
                session_id=session_id,
                speaker=speaker,
                recipient=recipient,
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
                recipient=recipient,
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
        recipient: RecipientContext | None = None,
        memory_origin: MemoryOrigin | None = None,
        tool_checkpoint: Callable[[], None] | None = None,
        stream_context: str | None = None,
        speaker_label: str | None = None,
        viewer_notes: list[str] | None = None,
        relationship_note: str | None = None,
    ) -> AssistantResponse:
        recipient = _recipient_context(recipient, delivery_context=delivery_context)
        memory_origin = memory_origin or MemoryOrigin.from_speaker(
            speaker, privacy_scope=recipient.privacy_scope, session_id=session_id)
        background_context = background_context if recipient.allows_private_memory else None
        # Recent public stream chat, only ever shown to the stream audience.
        stream_context = stream_context if recipient.privacy_scope == "public_stream" else None
        stripped = user_text.strip()
        if not stripped:
            raise ConversationError("user_text must not be empty.")
        wants_speech = synthesize_speech if speech_requested is None else speech_requested

        try:
            load_persona_config(private=recipient.allows_private_memory)
            privacy_decision = review_private_info_request(stripped)
            if privacy_decision.blocked:
                started_at = time.perf_counter()
                privacy_delivery = resolve_delivery(
                    action="reply",
                    requested="speech" if synthesize_speech else "text_only",
                    context=delivery_context,
                    speech_requested=wants_speech,
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
                recipient=recipient,
            )
            if session_id and persist_state and recipient.allows_private_memory:
                continuity_context = self._continuity.prompt_context(
                    session_id,
                    privacy_scopes={"local_private"},
                )
                if continuity_context:
                    system_prompt += "\n\n" + continuity_context
            elif session_id and persist_state and recipient.privacy_scope in SHARED_HISTORY_SCOPES:
                continuity_context = self._continuity.prompt_context(
                    session_id,
                    privacy_scopes={recipient.privacy_scope},
                    token_budget=SHARED_HISTORY_TOKENS,
                    named_speakers=True,
                )
                if continuity_context:
                    system_prompt += (
                        "\n\n# Shared Channel History\n"
                        "Everyone in this channel could see these turns. Nothing private is included.\n\n"
                        + continuity_context
                    )
            if background_context and background_context.strip():
                system_prompt += f"\n\n# Stream Background Context\n{background_context.strip()}"
            if stream_context and stream_context.strip():
                system_prompt += f"\n\n# Recent Stream Chat\n{stream_context.strip()}"
            if speaker_label:
                system_prompt += f"\n\n# Speaking Now\n{speaker_label} (as shown in chat)"
            if viewer_notes and recipient.privacy_scope in SHARED_HISTORY_SCOPES:
                notes = "\n".join(f"- {note}" for note in viewer_notes)
                system_prompt += (f"\n\n# What {speaker_label or 'this viewer'} Has Told You Before\n"
                                  f"They said these things about themselves in this channel:\n{notes}")
            if relationship_note and recipient.privacy_scope in SHARED_HISTORY_SCOPES:
                system_prompt += f"\n\n# How Well You Know Them\n{' '.join(relationship_note.split())[:240]}"
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
            legacy_delivery: DeliveryMode = "speech" if wants_speech else "text_only"
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
            owner_coding_receipt = (len(inferred_tool_calls) == 1 and inferred_tool_calls[0].tool == 'coding_goal'
                                    and speaker is not None and speaker.is_owner is True and speaker.trust == 'owner')
            if owner_coding_receipt:
                draft_reply = LLMReply(text=ConversationTurnDraft(
                    action='reply', requested_delivery=legacy_delivery,
                    final_text='Checking that coding request.', reason_code='owner_coding_control',
                ).model_dump_json())
            else:
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
                recipient=recipient,
            )
            turn_draft = parsed_turn.draft
            timing["turn_parse_ms"] = round((time.perf_counter() - parse_started) * 1000, 1)

            memory_write_ok = persist_state and (speaker is None or speaker.memory_write_allowed)

            requested_tool_calls = turn_draft.tool_calls if parsed_turn.structured and tools_ok else []
            primary_tool_calls = self._merge_tool_calls(inferred_tool_calls, requested_tool_calls)
            # Authorized tool calls always run synchronously and their raw output
            # never becomes communicable text.
            tool_started = time.perf_counter()
            inferred_results = self._execute_tool_calls(primary_tool_calls, speaker=speaker, recipient=recipient, memory_origin=memory_origin, tool_checkpoint=tool_checkpoint)
            timing["tool_exec_ms"] = round((time.perf_counter() - tool_started) * 1000, 1)

            final_reply_text = turn_draft.final_text
            final_reply_text = self._cleanup_spoken_text(final_reply_text)
            if inferred_results:
                direct_reply = self._render_direct_tool_reply(user_text=stripped, tool_results=inferred_results, recipient=recipient)
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
                        recipient=recipient,
                    ))
                    timing["finalizer_ms"] = round((time.perf_counter() - finalizer_started) * 1000, 1)
            else:
                timing["finalizer_ms"] = 0.0

            final_reply_text = self._guard_finalized_text(
                final_reply_text,
                tool_results=inferred_results,
                delivery_context=delivery_context,
                recipient=recipient,
            )

            if fast_mode:
                # Fast path: use draft metadata without another metadata LLM call.
                # Persist eligible structured candidates synchronously. If none are
                # provided and writes are allowed, save heuristic candidates in a
                # background thread without metadata LLM extraction.
                timing["metadata_ms"] = 0.0
                memory_started = time.perf_counter()
                structured_candidates = turn_draft.memory_candidates if parsed_turn.structured and memory_write_ok else []
                saved_count = (
                    self._memory.persist_candidates(structured_candidates, session_id=session_id, origin=memory_origin)
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
                    speech_requested=wants_speech,
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
                    tool_calls=[disclose_tool_call(call, recipient=recipient) for call in primary_tool_calls],
                    tool_results=inferred_results,
                    memory_candidates=_memory_candidate_view(turn_draft.memory_candidates if memory_write_ok else [], recipient=recipient),
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
                        args=(stripped, response.display_text or "", session_id, memory_origin),
                        daemon=True,
                    ).start()

                if persist_state:
                    self._remember_assistant_state(user_text=stripped, reply_text=response.display_text or "", emotion=response.emotion, session_id=session_id)
                return response

            # Standard path: use structured draft metadata directly; extract
            # legacy metadata only when needed, then save candidates synchronously.
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
            extra_results = self._execute_tool_calls(extra_tool_calls, speaker=speaker, recipient=recipient, memory_origin=memory_origin, tool_checkpoint=tool_checkpoint)
            if extra_results:
                direct_reply = self._render_direct_tool_reply(user_text=stripped, tool_results=extra_results, recipient=recipient)
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
                        recipient=recipient,
                    )
                    timing["finalizer_ms"] = round((time.perf_counter() - finalizer_started) * 1000, 1)

            all_tool_calls = primary_tool_calls + extra_tool_calls
            all_tool_results = inferred_results + extra_results
            final_reply_text = self._guard_finalized_text(
                final_reply_text,
                tool_results=all_tool_results,
                delivery_context=delivery_context,
                recipient=recipient,
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
            saved_count = self._memory.persist_candidates(memory_candidates, session_id=session_id, origin=memory_origin) if memory_write_ok else 0
            timing["memory_persist_ms"] = round((time.perf_counter() - memory_started) * 1000, 1)

            delivery_mode = resolve_delivery(
                action=turn_draft.action,
                requested=turn_draft.requested_delivery,
                context=delivery_context,
                speech_requested=wants_speech,
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
                tool_calls=[disclose_tool_call(call, recipient=recipient) for call in all_tool_calls],
                tool_results=all_tool_results,
                memory_candidates=_memory_candidate_view(memory_candidates, recipient=recipient),
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
        except JournalOwnershipError:
            raise
        except GammaError:
            raise
        except Exception as exc:
            raise ConversationError(f"Conversation pipeline failed: {exc}") from exc

    def _llm_adapter(self):
        if self._llm is None:
            self._llm = build_llm_adapter()
        return self._llm

    def _tts_service(self) -> TTSService:
        if self._tts is None:
            self._tts = TTSService()
        return self._tts

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
