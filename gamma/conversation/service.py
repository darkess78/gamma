from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from ..config import settings
from ..errors import ConversationError, GammaError
from ..llm.factory import build_llm_adapter
from ..memory.service import MemoryService
from ..persona.loader import build_system_prompt
from ..schemas.response import AssistantResponse, EmotionTag, MemoryCandidate, ToolCall, ToolExecutionResult, VisionAnalysis
from ..tools.registry import ToolRegistry
from ..vision.service import VisionImage, VisionService
from ..voice.tts import TTSService


ALLOWED_EMOTIONS: set[str] = {"neutral", "happy", "teasing", "concerned", "excited", "embarrassed", "annoyed"}


class ConversationService:
    def __init__(self) -> None:
        self._memory = MemoryService()
        self._llm = None
        self._tts = None
        self._tools = ToolRegistry()
        self._vision = VisionService()

    def respond(
        self,
        user_text: str,
        session_id: str | None = None,
        synthesize_speech: bool = False,
    ) -> AssistantResponse:
        return self._respond(
            user_text=user_text,
            session_id=session_id,
            synthesize_speech=synthesize_speech,
        )

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
    ) -> AssistantResponse:
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
        )

    def analyze_image(
        self,
        *,
        user_text: str,
        image_bytes: bytes,
        image_media_type: str,
        image_filename: str | None = None,
        vision_mode: str | None = None,
    ) -> VisionAnalysis:
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
        return self._memory.stats()

    def _respond(
        self,
        *,
        user_text: str,
        session_id: str | None,
        synthesize_speech: bool,
        image: VisionImage | None = None,
        vision_analysis: VisionAnalysis | None = None,
    ) -> AssistantResponse:
        stripped = user_text.strip()
        if not stripped:
            raise ConversationError("user_text must not be empty.")

        try:
            started_at = time.perf_counter()
            timing: dict[str, float] = {}
            system_prompt = build_system_prompt(
                memory_service=self._memory,
                user_text=stripped,
                session_id=session_id,
            )
            llm_started = time.perf_counter()
            draft_reply = self._llm_adapter().generate_reply(
                system_prompt=system_prompt,
                user_text=self._build_user_input_text(
                    user_text=stripped,
                    image=image,
                    vision_analysis=vision_analysis,
                ),
                image_inputs=[image.to_llm_input()] if image is not None else None,
            )
            timing["draft_reply_ms"] = round((time.perf_counter() - llm_started) * 1000, 1)
            inferred_tool_calls = self._infer_tool_calls(stripped)
            metadata_started = time.perf_counter()
            extracted = self._extract_turn_metadata(user_text=stripped, reply_text=draft_reply.text) if self._needs_metadata_pass(stripped, inferred_tool_calls) else self._default_metadata()
            timing["metadata_ms"] = round((time.perf_counter() - metadata_started) * 1000, 1)
            planned_tool_calls = self._merge_tool_calls(extracted["tool_calls"], inferred_tool_calls)
            tool_started = time.perf_counter()
            tool_results = self._execute_tool_calls(planned_tool_calls)
            timing["tool_exec_ms"] = round((time.perf_counter() - tool_started) * 1000, 1)
            final_reply_text = draft_reply.text
            if tool_results:
                direct_reply = self._render_direct_tool_reply(user_text=stripped, tool_results=tool_results)
                if direct_reply is not None:
                    final_reply_text = direct_reply
                    timing["finalizer_ms"] = 0.0
                else:
                    finalizer_started = time.perf_counter()
                    final_reply_text = self._finalize_reply_with_tools(
                        system_prompt=system_prompt,
                        user_text=self._build_user_input_text(
                            user_text=stripped,
                            image=image,
                            vision_analysis=vision_analysis,
                        ),
                        draft_reply=draft_reply.text,
                        tool_results=tool_results,
                        image=image,
                    )
                    timing["finalizer_ms"] = round((time.perf_counter() - finalizer_started) * 1000, 1)
            else:
                timing["finalizer_ms"] = 0.0
            memory_candidates = extracted["memory_candidates"] or self._build_memory_candidates(
                user_text=stripped,
                reply_text=final_reply_text,
            )
            memory_started = time.perf_counter()
            saved_count = self._memory.persist_candidates(memory_candidates, session_id=session_id)
            timing["memory_persist_ms"] = round((time.perf_counter() - memory_started) * 1000, 1)

            response = AssistantResponse(
                spoken_text=final_reply_text,
                emotion=extracted["emotion"],
                internal_summary=extracted["internal_summary"],
                motions=extracted["motions"],
                tool_calls=planned_tool_calls,
                tool_results=tool_results,
                memory_candidates=memory_candidates,
                vision=vision_analysis,
            )
            if synthesize_speech:
                tts_started = time.perf_counter()
                tts_result = self._tts_service().synthesize(final_reply_text, emotion=response.emotion)
                response.audio_path = tts_result.audio_path
                response.audio_content_type = tts_result.content_type
                timing["tts_ms"] = round((time.perf_counter() - tts_started) * 1000, 1)
            else:
                timing["tts_ms"] = 0.0
            timing["total_ms"] = round((time.perf_counter() - started_at) * 1000, 1)
            self._append_timing_log(
                user_text=stripped,
                session_id=session_id,
                response=response,
                saved_count=saved_count,
                timing=timing,
            )
            return response
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

    def _extract_turn_metadata(self, user_text: str, reply_text: str) -> dict:
        tool_help = "\n".join(f"- {item}" for item in self._tools.tool_summaries())
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
            raw = self._llm_adapter().generate_reply(system_prompt=extraction_prompt, user_text=extraction_input).text
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

    def _infer_tool_calls(self, user_text: str) -> list[ToolCall]:
        lowered = user_text.lower()
        inferred: list[ToolCall] = []

        if any(term in lowered for term in ["provider", "providers", "ollama", "gpt-sovits", "gpt sovits", "stt", "tts", "llm"]):
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
            "Return only the final user-facing reply."
        )
        finalizer_input = (
            f"User message:\n{user_text}\n\n"
            f"Draft reply:\n{draft_reply}\n\n"
            "Tool results:\n"
            + "\n".join(tool_summary)
        )
        try:
            return self._llm_adapter().generate_reply(
                system_prompt=finalizer_prompt,
                user_text=finalizer_input,
                image_inputs=[image.to_llm_input()] if image is not None else None,
            ).text
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

    def _build_memory_candidates(self, user_text: str, reply_text: str) -> list[MemoryCandidate]:
        candidates: list[MemoryCandidate] = []
        stripped = user_text.strip()
        lowered = stripped.lower()
        if "my name is " in lowered:
            candidates.append(
                MemoryCandidate(
                    type="profile",
                    text=stripped,
                    importance=0.9,
                    tags=["identity"],
                )
            )
        elif any(phrase in lowered for phrase in ["remember that ", "my favorite", "i like ", "i prefer "]):
            candidates.append(
                MemoryCandidate(
                    type="profile",
                    text=stripped,
                    importance=0.8,
                    tags=["preference"],
                )
            )
        if len(stripped.split()) >= 8:
            candidates.append(
                MemoryCandidate(
                    type="episodic",
                    text=f"User said: {stripped} | Assistant replied: {reply_text}",
                    importance=0.55,
                    tags=["conversation"],
                )
            )
        return candidates

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
    ) -> None:
        log_dir = settings.data_dir / "runtime"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "conversation.timings.jsonl"
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "session_id": session_id,
            "user_text_preview": user_text[:140],
            "response_preview": response.spoken_text[:140],
            "tool_call_count": len(response.tool_calls),
            "tool_result_count": len(response.tool_results),
            "memory_candidate_count": len(response.memory_candidates),
            "memory_saved_count": saved_count,
            "synthesize_speech": bool(response.audio_path or response.audio_content_type),
            "timing_ms": timing,
        }
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
