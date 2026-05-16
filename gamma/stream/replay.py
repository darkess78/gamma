from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .trace import StreamTraceStore


EvalSeverity = Literal["info", "warning", "error"]


class StreamEvalFinding(BaseModel):
    trace_id: str | None = None
    severity: EvalSeverity
    rule: str
    message: str


class StreamEvalReport(BaseModel):
    checked_count: int
    passed: bool
    findings: list[StreamEvalFinding] = Field(default_factory=list)


class StreamReplayService:
    def __init__(self, trace_store: StreamTraceStore | None = None) -> None:
        self._trace_store = trace_store or StreamTraceStore()

    def recent_traces(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self._trace_store.read_recent(limit=limit)

    def evaluate_recent(self, *, limit: int = 50) -> StreamEvalReport:
        traces = self.recent_traces(limit=limit)
        findings: list[StreamEvalFinding] = []
        for trace in traces:
            findings.extend(self._evaluate_trace(trace))
        return StreamEvalReport(
            checked_count=len(traces),
            passed=not any(item.severity == "error" for item in findings),
            findings=findings,
        )

    def _evaluate_trace(self, trace: dict[str, Any]) -> list[StreamEvalFinding]:
        trace_id = self._string_or_none(trace.get("trace_id"))
        input_event = trace.get("input_event") if isinstance(trace.get("input_event"), dict) else {}
        decision = trace.get("decision") if isinstance(trace.get("decision"), dict) else {}
        action_plan = trace.get("action_plan") if isinstance(trace.get("action_plan"), dict) else {}
        output_events = trace.get("output_events") if isinstance(trace.get("output_events"), list) else []
        assistant_response = trace.get("assistant_response")
        findings: list[StreamEvalFinding] = []

        decision_kind = str(decision.get("decision") or "")
        event_kind = str(input_event.get("kind") or "")

        if decision_kind == "ignore" and (assistant_response is not None or output_events):
            findings.append(
                self._finding(
                    trace_id,
                    "error",
                    "ignored_turn_has_output",
                    "Ignored stream events must not have assistant responses or output events.",
                )
            )

        if decision_kind == "moderation_escalation" and (assistant_response is not None or output_events):
            findings.append(
                self._finding(
                    trace_id,
                    "error",
                    "moderation_escalation_generated_output",
                    "Moderation escalation turns must not generate performer output.",
                )
            )

        if event_kind == "mic_transcript" and assistant_response is not None and not output_events:
            findings.append(
                self._finding(
                    trace_id,
                    "error",
                    "mic_reply_missing_output_events",
                    "Mic transcript replies must emit performer-facing output events.",
                )
            )

        for item in action_plan.get("items", []) if isinstance(action_plan.get("items"), list) else []:
            if not isinstance(item, dict):
                continue
            risk_tier = str(item.get("risk_tier") or "none")
            requires_approval = bool(item.get("requires_approval", False))
            if risk_tier in {"high", "critical"} and not requires_approval:
                findings.append(
                    self._finding(
                        trace_id,
                        "error",
                        "high_risk_action_without_approval",
                        f"High-risk action {item.get('action_type') or '<unknown>'} must require approval.",
                    )
                )

        return findings

    def _finding(self, trace_id: str | None, severity: EvalSeverity, rule: str, message: str) -> StreamEvalFinding:
        return StreamEvalFinding(trace_id=trace_id, severity=severity, rule=rule, message=message)

    def _string_or_none(self, value: object) -> str | None:
        return value if isinstance(value, str) and value else None
