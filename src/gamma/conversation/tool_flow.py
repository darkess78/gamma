"""Tool calls during a turn: inferring, executing, finalizing the reply with results, and direct replies for simple tools."""
from __future__ import annotations

import json
from typing import Callable

from ..identity.profile import SpeakerProfile
from ..llm.base import LLMCallContext
from ..memory.attribution import MemoryOrigin
from ..schemas.conversation import RecipientContext
from ..schemas.response import ToolCall, ToolExecutionResult
from ..tools.disclosure import disclose_tool_call, disclose_tool_result
from ..vision.service import VisionImage
from .shared import _recipient_context
from .turn_contract import TurnContractError


class ToolFlowMixin:
    """Tool calls during a turn: inferring, executing, finalizing the reply with results, and direct replies for simple tools."""

    def _execute_tool_calls(self, tool_calls: list[ToolCall], *, speaker: SpeakerProfile | None = None,
                            recipient: RecipientContext | None = None,
                            memory_origin: MemoryOrigin | None = None,
                            tool_checkpoint: Callable[[], None] | None = None) -> list[ToolExecutionResult]:
        recipient = _recipient_context(recipient)
        results: list[ToolExecutionResult] = []
        for call in tool_calls[:3]:
            from ..tools.coding import CODING_TOOLS
            if call.tool in CODING_TOOLS and not (speaker is not None and speaker.is_owner is True and speaker.trust == 'owner'):
                results.append(ToolExecutionResult(tool=call.tool,ok=False,output='Only the verified owner can control coding work.',metadata={'reason':'owner_required'}))
                continue
            if call.tool == "save_core_memory" and not (
                speaker is not None and speaker.is_owner is True and speaker.trust == "owner"
            ):
                results.append(ToolExecutionResult(
                    tool=call.tool, ok=False,
                    output="Only the verified owner can save core memory.",
                    metadata={"reason": "owner_required"},
                ))
                continue
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
            if tool_checkpoint is not None:
                tool_checkpoint()
            try:
                args = {k:v for k,v in call.args.items() if k not in {'is_owner','speaker','trust','resolved_via'}} if call.tool in CODING_TOOLS else call.args
                if call.tool == "save_memory":
                    args = {k: v for k, v in args.items() if k != "_memory_origin"}
                    args["_memory_origin"] = memory_origin or MemoryOrigin.from_speaker(
                        speaker, source="tool", privacy_scope=recipient.privacy_scope)
                result = tool.run(**args)
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
        return [disclose_tool_result(result, recipient=recipient) for result in results]

    def _infer_tool_calls(self, user_text: str, speaker: SpeakerProfile | None = None) -> list[ToolCall]:
        lowered = user_text.lower()
        inferred: list[ToolCall] = []
        is_owner = speaker is None or speaker.is_owner

        if speaker is not None and speaker.is_owner is True and speaker.trust == 'owner' and lowered.startswith('coding goal:'):
            goal = user_text[len('coding goal:'):].strip()
            if len(goal) >= 12:
                return [ToolCall(tool='coding_goal',args={'goal':goal})]

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

        # Core memory â€” owner only. Trigger: "remember: <fact>"
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
        recipient: RecipientContext | None = None,
    ) -> str:
        tool_summary = []
        recipient = _recipient_context(recipient)
        tool_results = [disclose_tool_result(result, recipient=recipient) for result in tool_results]
        if not recipient.allows_private_memory and draft_reply.lstrip().startswith("{"):
            # Structured draft arguments are execution inputs, not disclosures.
            try:
                draft = json.loads(draft_reply)
                if isinstance(draft, dict) and isinstance(draft.get("tool_calls"), list):
                    draft["tool_calls"] = [disclose_tool_call(ToolCall.model_validate(call), recipient=recipient).model_dump()
                                           for call in draft["tool_calls"]]
                    draft_reply = json.dumps(draft, ensure_ascii=False)
            except (ValueError, TypeError):
                draft_reply = "A tool request was executed; use the recipient-visible results below."
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

    def _render_direct_tool_reply(self, *, user_text: str, tool_results: list[ToolExecutionResult],
                                  recipient: RecipientContext | None = None) -> str | None:
        recipient = _recipient_context(recipient)
        tool_results = [disclose_tool_result(result, recipient=recipient) for result in tool_results]
        if not tool_results or any(not result.ok for result in tool_results):
            return None
        if not recipient.allows_private_memory:
            return tool_results[0].output if len(tool_results) == 1 else None
        if len(tool_results) == 1:
            result = tool_results[0]
            if result.tool == 'coding_goal':
                return "I've queued that coding goal. I'll work through the tasks and record the checks on the Coding page."
            if result.tool == 'coding_control':
                return 'The coding control is requested for the next safe boundary.'
            if result.tool == 'coding_status':
                jobs = result.metadata.get('jobs', [])
                from ..tools.coding import coding_status_reply
                return coding_status_reply(jobs)
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
