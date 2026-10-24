"""Recipient views for tool disclosures; execution authority stays with the caller."""
from __future__ import annotations

from ..schemas.conversation import RecipientContext
from ..schemas.response import ToolCall, ToolExecutionResult


_PUBLIC_TOOL_ACKS = {
    "search_memory": "Memory search completed. Its details are private.",
    "memory_stats": "Memory status checked. Its details are private.",
    "known_people": "Known people checked. Their details are private.",
    "provider_status": "Provider status checked. Its details are private.",
    "recent_artifacts": "Recent artifacts checked. Their details are private.",
    "save_memory": "Memory save completed. Its details are private.",
    "save_core_memory": "Core memory save completed. Its details are private.",
    "coding_goal": "Coding request completed. Its details are private.",
    "coding_status": "Coding status checked. Its details are private.",
    "coding_control": "Coding control requested. Its details are private.",
}


def _recipient(value: RecipientContext | None) -> RecipientContext:
    if value is None:
        return RecipientContext()
    if type(value) is not RecipientContext:
        raise TypeError("recipient_context_must_be_internal")
    return value


def _public_tool_name(name: str) -> str:
    return name if name in _PUBLIC_TOOL_ACKS else "unclassified_tool"


def disclose_tool_result(
    result: ToolExecutionResult, *, recipient: RecipientContext | None = None,
) -> ToolExecutionResult:
    """Select a fresh result view before any response or model consumes it.

    Public fields are constructed from finite constants. Tool output, metadata,
    counts and exceptions remain unclassified regardless of tool/model kwargs.
    """
    context = _recipient(recipient)
    if context.allows_private_memory:
        return result.model_copy(deep=True)
    name = _public_tool_name(result.tool)
    ok = result.ok is True
    status = "completed" if ok else "failed"
    output = _PUBLIC_TOOL_ACKS.get(name, "Tool execution completed. Its details are private.") if ok else "Tool execution failed. Its details are private."
    if ok and name == "coding_goal" and type(result.metadata.get("status")) is str and result.metadata["status"] == "queued":
        status = "queued"
        output = "Coding goal queued. Its details are private."
    return ToolExecutionResult(tool=name, ok=ok, output=output,
                               metadata={"disclosure": "status_only", "status": status})


def disclose_tool_call(
    call: ToolCall, *, recipient: RecipientContext | None = None,
) -> ToolCall:
    """Keep execution arguments internal to shared/default conversation paths."""
    if _recipient(recipient).allows_private_memory:
        return call.model_copy(deep=True)
    return ToolCall(tool=_public_tool_name(call.tool), args={})
