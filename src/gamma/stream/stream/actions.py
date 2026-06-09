from __future__ import annotations

from ..schemas.response import AssistantResponse, ToolCall, ToolExecutionResult
from .models import ActionPlan, ActionPlanItem, ActionRiskTier


class ActionPlanner:
    """Build auditable action plans from existing assistant tool decisions."""

    _RISK_BY_TOOL: dict[str, ActionRiskTier] = {
        "provider_status": "low",
        "memory_stats": "low",
        "known_people": "low",
        "recent_artifacts": "low",
        "search_memory": "low",
        "save_memory": "medium",
        "save_core_memory": "high",
    }

    def plan_from_response(self, response: AssistantResponse) -> ActionPlan:
        results_by_tool = self._results_by_tool(response.tool_results)
        items = [
            self._plan_item(call, result=results_by_tool.get(call.tool))
            for call in response.tool_calls
        ]
        return ActionPlan(items=items)

    def _plan_item(self, call: ToolCall, *, result: ToolExecutionResult | None) -> ActionPlanItem:
        risk_tier = self._RISK_BY_TOOL.get(call.tool, "high")
        requires_approval = call.tool == "save_core_memory" or call.tool not in self._RISK_BY_TOOL
        status = "planned"
        result_payload = None
        if result is not None:
            status = "executed" if result.ok else "failed"
            result_payload = result.model_dump()
        return ActionPlanItem(
            action_type=f"tool.{call.tool}",
            args=dict(call.args),
            risk_tier=risk_tier,
            requires_approval=requires_approval,
            status=status,
            audit_reason=self._audit_reason(call.tool, risk_tier, requires_approval),
            result=result_payload,
        )

    def _results_by_tool(self, results: list[ToolExecutionResult]) -> dict[str, ToolExecutionResult]:
        indexed: dict[str, ToolExecutionResult] = {}
        for result in results:
            indexed.setdefault(result.tool, result)
        return indexed

    def _audit_reason(self, tool_name: str, risk_tier: ActionRiskTier, requires_approval: bool) -> str:
        if tool_name in {"provider_status", "memory_stats", "known_people", "recent_artifacts", "search_memory"}:
            return f"{tool_name} is a read-only internal tool."
        if tool_name == "save_memory":
            return "save_memory writes scoped assistant memory and is currently auto-executed by the existing conversation path."
        if tool_name == "save_core_memory":
            return "save_core_memory writes permanent core memory and should require owner approval."
        if requires_approval:
            return f"Unknown action has {risk_tier} risk and requires approval before execution."
        return f"{tool_name} has {risk_tier} risk."
