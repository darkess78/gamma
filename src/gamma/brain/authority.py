"""Core-side capabilities for one brain request. No worker-selected identity."""
from dataclasses import asdict

from ..persona.loader import _load_core_memories
from ..schemas.response import ToolCall, AssistantResponse


class BrainAuthority:
    def __init__(self, service, fields):
        self.service = service
        self.speaker = fields.get("speaker")
        self.session = fields.get("session_id")
        self.enabled = fields.get("persist_state", False)
        self.proposals = fields.get("state_proposals")
        self.user_text = fields.get("user_text", "") or ""

    def __call__(self, operation, args):
        if not isinstance(args, dict):
            raise ValueError("invalid authority arguments")
        if operation == "tools":
            calls = [ToolCall.model_validate(value) for value in args["calls"][:3]]
            return [value.model_dump(mode="json") for value in self.service._execute_tool_calls(
                calls, speaker=self.speaker, enabled=self.enabled, state_proposals=self.proposals)]
        if operation == "tool_summaries":
            return self.service._tools.tool_summaries()
        if operation == "continuity":
            return self.service._continuity.prompt_context(self.session,
                privacy_scopes=self.service._privacy_scopes_for_speaker(self.speaker)) if self.session else ""
        if operation == "persona":
            context = self.service._emotion_memory.relevant_context(user_text=self.user_text)
            return {"core_memories": _load_core_memories(), "emotion_context": {
                "state": asdict(context["state"]),
                "episodes": [asdict(value) for value in context["episodes"]],
                "patterns": [asdict(value) for value in context["patterns"]]}}
        if operation in {"profile", "memories"}:
            if self.speaker is not None and not self.speaker.memory_read_allowed:
                return []
            if operation == "profile":
                # Retrieval is read-only and bounded; no path or SQL is accepted.
                result = self.service._memory.get_profile_facts(limit=max(1, min(int(args.get("limit", 3)), 20)),
                    subject_type=args.get("subject_type"), subject_name=args.get("subject_name"))
            else:
                result = self.service._memory.search_memories(self.user_text, session_id=self.session,
                    limit=max(1, min(int(args.get("limit", 3)), 20)), subject_type=args.get("subject_type"),
                    subject_name=args.get("subject_name"))
            return [value.model_dump(mode="json") for value in result]
        if operation == "safety":
            return asdict(self.service._speech_filter.apply(args["text"], include_llm=args.get("include_llm", True)))
        if operation == "timing":
            self.service._append_timing_log(user_text=self.user_text, session_id=self.session,
                response=AssistantResponse.model_validate(args["response"]), saved_count=0,
                timing=args["timing"], route_events=args["route_events"])
            return None
        raise ValueError("unsupported brain authority operation")
