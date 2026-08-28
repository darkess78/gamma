from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class ChangeClass(StrEnum):
    RUNTIME_ADAPTATION = "runtime_adaptation"
    TRACKED_CONFIGURATION = "tracked_configuration"
    BEHAVIOR_OR_CODE = "behavior_or_code"
    RESTRICTED_OPERATION = "restricted_operation"


class MetricState(StrEnum):
    INSUFFICIENT = "insufficient_data"
    IMPROVED = "improved"
    STABLE = "stable"
    REGRESSED = "regressed"


class DistributionSummary(BaseModel):
    count: int = 0
    mean: float | None = None
    p50: float | None = None
    p95: float | None = None
    p99: float | None = None
    minimum: float | None = None
    maximum: float | None = None


class MetricSnapshot(BaseModel):
    metric_id: str
    source: str
    role: Literal["objective", "guardrail", "diagnostic"]
    unit: str
    statistic: Literal["mean", "p50", "p95", "p99"]
    summary: DistributionSummary
    selected_value: float | None = None
    sufficient_data: bool = False


class CohortSummary(BaseModel):
    source: Literal["conversation", "llm_routes", "live_voice"]
    dimensions: dict[str, str]
    record_count: int
    success_count: int
    failure_count: int
    duration: DistributionSummary


class MetricComparison(BaseModel):
    metric_id: str
    role: Literal["objective", "guardrail", "diagnostic"]
    baseline: MetricSnapshot
    candidate: MetricSnapshot
    directional_change_percent: float | None = None
    state: MetricState
    reason: str


class ImprovementOpportunity(BaseModel):
    domain: str
    priority: Literal["high", "medium", "low"]
    kind: str
    evidence: str
    suggested_next_step: str


class ObservationReport(BaseModel):
    contract_version: int
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    runtime_dir: str
    source_record_counts: dict[str, int]
    metrics: list[MetricSnapshot]
    cohorts: list[CohortSummary] = Field(default_factory=list)
    opportunities: list[ImprovementOpportunity]
    warnings: list[str] = Field(default_factory=list)


class ValidationEvidence(BaseModel):
    passed_gates: set[str] = Field(default_factory=set)
    human_approved: bool = False
    approval_reference: str | None = None


class CandidateEvaluation(BaseModel):
    contract_version: int
    change_class: ChangeClass
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    comparisons: list[MetricComparison]
    required_gates: list[str]
    missing_gates: list[str]
    objective_improved: bool
    guardrails_passed: bool
    promotion_eligible: bool
    decision: Literal["promote_candidate", "reject_candidate", "needs_more_evidence", "manual_only"]
    reasons: list[str]
