from __future__ import annotations

import json
import math
from collections import deque
from pathlib import Path
from typing import Any, Callable, Iterable

from .contract import ImprovementContract, MetricContract
from .models import (
    CandidateEvaluation,
    ChangeClass,
    CohortSummary,
    DistributionSummary,
    ImprovementOpportunity,
    MetricComparison,
    MetricSnapshot,
    MetricState,
    ObservationReport,
    ValidationEvidence,
)


_SOURCE_FILES = {
    "conversation": "conversation.timings.jsonl",
    "llm_routes": "llm.routes.jsonl",
    "fixtures": "fixture.results.jsonl",
    "live_voice": "live_jobs/history.current.jsonl",
}
_STAGE_METRICS = (
    "conversation.draft_reply_ms",
    "conversation.metadata_ms",
    "conversation.memory_persist_ms",
    "conversation.tool_exec_ms",
    "conversation.finalizer_ms",
    "conversation.tts_ms",
)


class ImprovementEvaluator:
    """Analyze aggregate runtime metrics without mutating Gamma or exposing turn text."""

    def __init__(self, contract: ImprovementContract) -> None:
        self.contract = contract

    def observe(self, runtime_dir: Path) -> ObservationReport:
        records = self._load_runtime_records(runtime_dir)
        metrics = [self._snapshot(metric, records[metric.source]) for metric in self.contract.metrics]
        cohorts = [
            *_route_cohorts(records["llm_routes"]),
            *_conversation_cohorts(records["conversation"]),
        ]
        warnings: list[str] = []
        for source, source_records in records.items():
            if not source_records:
                warnings.append(f"No readable {_SOURCE_FILES[source]} records were found.")
        return ObservationReport(
            contract_version=self.contract.version,
            runtime_dir=str(runtime_dir.resolve()),
            source_record_counts={source: len(items) for source, items in records.items()},
            metrics=metrics,
            cohorts=cohorts,
            opportunities=self._opportunities(metrics, records, cohorts),
            warnings=warnings,
        )

    def compare(
        self,
        *,
        baseline_runtime_dir: Path,
        candidate_runtime_dir: Path,
        change_class: ChangeClass,
        evidence: ValidationEvidence | None = None,
    ) -> CandidateEvaluation:
        baseline = self.observe(baseline_runtime_dir)
        candidate = self.observe(candidate_runtime_dir)
        baseline_by_id = {metric.metric_id: metric for metric in baseline.metrics}
        candidate_by_id = {metric.metric_id: metric for metric in candidate.metrics}
        contract_by_id = {metric.id: metric for metric in self.contract.metrics}
        comparisons = [
            self._compare_metric(contract_by_id[metric_id], baseline_by_id[metric_id], candidate_by_id[metric_id])
            for metric_id in contract_by_id
        ]
        evidence = evidence or ValidationEvidence()
        required_gates = list(self.contract.policy.gates_for(change_class))
        non_approval_gates = set(required_gates) - {"human_approval"}
        missing_gates = sorted(non_approval_gates - evidence.passed_gates)
        if "human_approval" in required_gates and not evidence.human_approved:
            missing_gates.append("human_approval")
        missing_gates = sorted(set(missing_gates))

        objective_improved = any(
            item.role == "objective" and item.state == MetricState.IMPROVED for item in comparisons
        )
        guardrails_passed = not any(
            item.role in {"objective", "guardrail"}
            and item.state in {MetricState.INSUFFICIENT, MetricState.REGRESSED}
            for item in comparisons
        )
        reasons: list[str] = []
        manual_only = change_class == ChangeClass.RESTRICTED_OPERATION
        if manual_only:
            reasons.append("Restricted operations are never eligible for automatic promotion.")
        if missing_gates:
            reasons.append("Missing required evidence: " + ", ".join(missing_gates) + ".")
        if not objective_improved:
            reasons.append("No objective metric achieved its minimum practical improvement.")
        if not guardrails_passed:
            reasons.append("One or more required objective or guardrail metrics regressed or lack enough samples.")

        promotion_eligible = not manual_only and not missing_gates and objective_improved and guardrails_passed
        if manual_only:
            decision = "manual_only"
        elif promotion_eligible:
            decision = "promote_candidate"
        elif any(item.state == MetricState.REGRESSED for item in comparisons):
            decision = "reject_candidate"
        else:
            decision = "needs_more_evidence"
        return CandidateEvaluation(
            contract_version=self.contract.version,
            change_class=change_class,
            comparisons=comparisons,
            required_gates=required_gates,
            missing_gates=missing_gates,
            objective_improved=objective_improved,
            guardrails_passed=guardrails_passed,
            promotion_eligible=promotion_eligible,
            decision=decision,
            reasons=reasons,
        )

    def _load_runtime_records(self, runtime_dir: Path) -> dict[str, list[dict[str, Any]]]:
        return {
            source: _read_jsonl(
                runtime_dir / filename,
                maximum_records=self.contract.policy.maximum_records_per_source,
                record_filter=_production_route_record if source == "llm_routes" else None,
            )
            for source, filename in _SOURCE_FILES.items()
        }

    def _snapshot(self, metric: MetricContract, records: list[dict[str, Any]]) -> MetricSnapshot:
        values = _metric_values(metric, records)
        summary = summarize(values)
        selected_value = getattr(summary, metric.statistic)
        return MetricSnapshot(
            metric_id=metric.id,
            source=metric.source,
            role=metric.role,
            unit=metric.unit,
            statistic=metric.statistic,
            summary=summary,
            selected_value=selected_value,
            sufficient_data=summary.count >= metric.minimum_samples,
        )

    def _compare_metric(
        self,
        metric: MetricContract,
        baseline: MetricSnapshot,
        candidate: MetricSnapshot,
    ) -> MetricComparison:
        if not baseline.sufficient_data or not candidate.sufficient_data:
            return MetricComparison(
                metric_id=metric.id,
                role=metric.role,
                baseline=baseline,
                candidate=candidate,
                state=MetricState.INSUFFICIENT,
                reason=f"Both snapshots require at least {metric.minimum_samples} samples.",
            )
        baseline_value = baseline.selected_value
        candidate_value = candidate.selected_value
        if baseline_value is None or candidate_value is None:
            return MetricComparison(
                metric_id=metric.id,
                role=metric.role,
                baseline=baseline,
                candidate=candidate,
                state=MetricState.INSUFFICIENT,
                reason="The selected statistic could not be calculated.",
            )
        directional_change = _directional_change_percent(
            baseline_value,
            candidate_value,
            direction=metric.direction,
        )
        if directional_change >= metric.minimum_practical_change_percent:
            state = MetricState.IMPROVED
            reason = "Candidate met the configured minimum practical improvement."
        elif directional_change < -metric.maximum_regression_percent:
            state = MetricState.REGRESSED
            reason = "Candidate exceeded the configured regression budget."
        else:
            state = MetricState.STABLE
            reason = "Candidate remained inside the configured noise/regression band."
        return MetricComparison(
            metric_id=metric.id,
            role=metric.role,
            baseline=baseline,
            candidate=candidate,
            directional_change_percent=round(directional_change, 3),
            state=state,
            reason=reason,
        )

    def _opportunities(
        self,
        metrics: list[MetricSnapshot],
        records: dict[str, list[dict[str, Any]]],
        cohorts: list[CohortSummary],
    ) -> list[ImprovementOpportunity]:
        by_id = {metric.metric_id: metric for metric in metrics}
        opportunities: list[ImprovementOpportunity] = []
        total = by_id.get("conversation.total_ms")
        if total and not total.sufficient_data:
            opportunities.append(
                ImprovementOpportunity(
                    domain="evaluation",
                    priority="low",
                    kind="collect_baseline",
                    evidence=f"Only {total.summary.count} conversation timing samples are available.",
                    suggested_next_step="Collect representative warm and cold everyday conversation and voice runs before tuning.",
                )
            )
        total_p50 = total.summary.p50 if total else None
        if total_p50 and total and total.summary.p99 and total.summary.p99 >= total_p50 * 2.5:
            opportunities.append(
                ImprovementOpportunity(
                    domain="conversation",
                    priority="high",
                    kind="tail_latency_variance",
                    evidence=f"Conversation p99 is {total.summary.p99 / total_p50:.2f}x the median.",
                    suggested_next_step="Separate cold/warm, route family, provider, model, and voice/text cohorts before optimizing the mean.",
                )
            )
        if total_p50 and total_p50 > 0:
            for metric_id in _STAGE_METRICS:
                stage = by_id.get(metric_id)
                if stage is None or stage.summary.p50 is None:
                    continue
                share = 100.0 * stage.summary.p50 / total_p50
                if share >= self.contract.policy.dominant_stage_share_percent:
                    opportunities.append(
                        ImprovementOpportunity(
                            domain=metric_id.split(".", 1)[0],
                            priority="medium",
                            kind="dominant_latency_stage",
                            evidence=f"{metric_id} median is {share:.1f}% of median total latency.",
                            suggested_next_step=f"Profile {metric_id} with a paired baseline before changing its implementation.",
                        )
                    )
        draft_total = by_id.get("conversation.draft_reply_ms")
        if draft_total and draft_total.summary.p50 and draft_total.summary.p50 > 0:
            draft_components = [
                by_id.get("conversation.draft_request_build_ms"),
                by_id.get("conversation.draft_llm_ms"),
            ]
            available_components = [
                component
                for component in draft_components
                if component is not None and component.summary.p50 is not None
            ]
            if available_components:
                dominant = max(
                    available_components,
                    key=lambda component: float(component.summary.p50 or 0.0),
                )
                share = 100.0 * float(dominant.summary.p50 or 0.0) / draft_total.summary.p50
                if share >= self.contract.policy.dominant_stage_share_percent:
                    opportunities.append(
                        ImprovementOpportunity(
                            domain="conversation",
                            priority="medium",
                            kind="draft_substage_dominance",
                            evidence=(
                                f"{dominant.metric_id} median is {share:.1f}% of median "
                                "conversation.draft_reply_ms."
                            ),
                            suggested_next_step=(
                                "Use paired route and conversation fixtures to separate model execution "
                                "from Gamma request-building overhead before changing behavior."
                            ),
                        )
                    )
        route_failures = by_id.get("llm_routes.failure_rate")
        if route_failures and route_failures.summary.mean and route_failures.summary.mean > 0:
            failure_groups = _route_failure_groups(records.get("llm_routes", []))
            group_evidence = "; ".join(
                f"{group}={count}" for group, count in failure_groups[:3]
            ) or "failure grouping unavailable"
            opportunities.append(
                ImprovementOpportunity(
                    domain="llm_router",
                    priority="high",
                    kind="route_failures",
                    evidence=f"Observed route failure rate is {route_failures.summary.mean:.2f}%; {group_evidence}.",
                    suggested_next_step="Group failures by provider, model, route family, and error class before proposing a routing change.",
                )
            )
        route_records = records.get("llm_routes", [])
        fallback_count = sum(
            isinstance(item.get("fallback_index"), int) and int(item["fallback_index"]) > 0
            for item in route_records
        )
        if route_records and fallback_count:
            opportunities.append(
                ImprovementOpportunity(
                    domain="llm_router",
                    priority="medium",
                    kind="fallback_usage",
                    evidence=f"{fallback_count} of {len(route_records)} routed calls used a fallback candidate.",
                    suggested_next_step="Compare fallback causes and successful fallback latency before changing route order or backoff policy.",
                )
            )
        eligible_route_cohorts = [
            cohort
            for cohort in cohorts
            if cohort.source == "llm_routes" and cohort.duration.p95 is not None and cohort.record_count >= 5
        ]
        if eligible_route_cohorts:
            slowest = max(eligible_route_cohorts, key=lambda item: float(item.duration.p95 or 0.0))
            label = "/".join(
                slowest.dimensions.get(key, "unknown")
                for key in ("provider", "model", "route_family")
            )
            opportunities.append(
                ImprovementOpportunity(
                    domain="llm_router",
                    priority="medium",
                    kind="slow_route_cohort",
                    evidence=f"Slowest sufficiently sampled successful route cohort is {label} at p95 {slowest.duration.p95:.1f} ms across {slowest.success_count} successes.",
                    suggested_next_step="Compare this cohort against a task-equivalent alternative using paired fixtures; do not infer model causation from mixed production traffic.",
                )
            )
        fixture_records = records.get("fixtures", [])
        violation_counts: dict[str, int] = {}
        for record in fixture_records:
            for violation in record.get("violations", []) if isinstance(record.get("violations"), list) else []:
                key = str(violation)
                violation_counts[key] = violation_counts.get(key, 0) + 1
        if violation_counts:
            ranked = sorted(violation_counts.items(), key=lambda item: (-item[1], item[0]))
            opportunities.append(
                ImprovementOpportunity(
                    domain="fixture_quality",
                    priority="high",
                    kind="fixture_regressions",
                    evidence="; ".join(f"{name}={count}" for name, count in ranked[:5]) + ".",
                    suggested_next_step="Reproduce the highest-frequency invariant failure before allowing any performance candidate to advance.",
                )
            )
        thermal_states = {
            str(record.get("thermal_state"))
            for record in fixture_records
            if str(record.get("thermal_state") or "") in {"cold", "warm"}
        }
        if fixture_records and thermal_states != {"cold", "warm"}:
            opportunities.append(
                ImprovementOpportunity(
                    domain="evaluation",
                    priority="low",
                    kind="thermal_coverage_gap",
                    evidence="Fixture artifacts do not yet include both explicitly controlled cold and warm runs.",
                    suggested_next_step="Capture separate cold and warm snapshots after externally controlling model residency.",
                )
            )
        live_total = by_id.get("live_voice.total_ms")
        if live_total and live_total.summary.p50 and live_total.summary.p99:
            ratio = live_total.summary.p99 / live_total.summary.p50
            if ratio >= 2.5:
                opportunities.append(
                    ImprovementOpportunity(
                        domain="live_voice",
                        priority="high",
                        kind="voice_tail_latency_variance",
                        evidence=f"Live-voice p99 total latency is {ratio:.2f}x its median.",
                        suggested_next_step="Separate response mode, TTS enabled state, cold/warm model residency, and cancellation cohorts.",
                    )
                )
        if live_total and live_total.summary.p50 and live_total.summary.p50 > 0:
            live_stages = [
                by_id.get("live_voice.stt_ms"),
                by_id.get("live_voice.conversation_ms"),
                by_id.get("live_voice.tts_ms"),
            ]
            available_live_stages = [
                stage for stage in live_stages if stage is not None and stage.summary.p50 is not None
            ]
            if available_live_stages:
                dominant_live = max(
                    available_live_stages,
                    key=lambda stage: float(stage.summary.p50 or 0.0),
                )
                share = 100.0 * float(dominant_live.summary.p50 or 0.0) / live_total.summary.p50
                if share >= self.contract.policy.dominant_stage_share_percent:
                    opportunities.append(
                        ImprovementOpportunity(
                            domain="live_voice",
                            priority="high",
                            kind="voice_dominant_latency_stage",
                            evidence=(
                                f"{dominant_live.metric_id} median is {share:.1f}% of median "
                                "live_voice.total_ms."
                            ),
                            suggested_next_step=(
                                "Profile that stage with controlled warm and cold voice fixtures before "
                                "changing STT, routing, or TTS policy."
                            ),
                        )
                    )
        first_audio = by_id.get("live_voice.time_to_first_chunk_audio_ms")
        if (
            first_audio
            and first_audio.sufficient_data
            and first_audio.summary.p95 is not None
            and first_audio.summary.p95 >= self.contract.policy.first_audio_warning_ms
        ):
            opportunities.append(
                ImprovementOpportunity(
                    domain="live_voice",
                    priority="high",
                    kind="slow_time_to_first_audio",
                    evidence=(
                        f"Live-voice p95 time to first audio is {first_audio.summary.p95:.1f} ms, "
                        f"above the {self.contract.policy.first_audio_warning_ms:.1f} ms observation threshold."
                    ),
                    suggested_next_step=(
                        "Compare planner, first-sentence generation, and first-chunk TTS timings on paired "
                        "warm fixtures; optimize the largest pre-audio stage first."
                    ),
                )
            )
        completed_live = sum(
            str((record.get("job") or {}).get("status") or "") == "completed"
            for record in records.get("live_voice", [])
            if isinstance(record.get("job"), dict)
        )
        if completed_live and (first_audio is None or first_audio.summary.count < completed_live):
            observed = first_audio.summary.count if first_audio else 0
            opportunities.append(
                ImprovementOpportunity(
                    domain="live_voice",
                    priority="medium",
                    kind="first_audio_coverage_gap",
                    evidence=f"First-audio timing exists for {observed} of {completed_live} completed live turns.",
                    suggested_next_step="Preserve time-to-first-audio on every completed speech-producing live turn before tuning total latency.",
                )
            )
        return opportunities


def _route_failure_groups(records: Iterable[dict[str, Any]]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for record in records:
        status = str(record.get("status") or "")
        if status not in {"error", "context_overflow"}:
            continue
        provider = str(record.get("provider") or "unknown")
        model = str(record.get("model") or "default")
        family = str(record.get("route_family") or "unknown")
        error_class = str(record.get("error_class") or status)
        key = f"{provider}/{model}/{family}/{error_class}"
        counts[key] = counts.get(key, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def _route_cohorts(records: Iterable[dict[str, Any]]) -> list[CohortSummary]:
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for record in records:
        key = (
            str(record.get("provider") or "unknown"),
            str(record.get("model") or "default"),
            str(record.get("route_family") or "unknown"),
        )
        group = groups.setdefault(key, {"durations": [], "records": 0, "successes": 0, "failures": 0})
        group["records"] += 1
        status = str(record.get("status") or "")
        if status == "ok":
            group["successes"] += 1
            duration = record.get("duration_ms")
            if isinstance(duration, (int, float)) and not isinstance(duration, bool):
                group["durations"].append(float(duration))
        elif status in {"error", "context_overflow"}:
            group["failures"] += 1
    cohorts = [
        CohortSummary(
            source="llm_routes",
            dimensions={"provider": key[0], "model": key[1], "route_family": key[2]},
            record_count=group["records"],
            success_count=group["successes"],
            failure_count=group["failures"],
            duration=summarize(group["durations"]),
        )
        for key, group in groups.items()
    ]
    return sorted(cohorts, key=lambda item: (-item.record_count, sorted(item.dimensions.items())))[:50]


def _conversation_cohorts(records: Iterable[dict[str, Any]]) -> list[CohortSummary]:
    groups: dict[tuple[str, str, str, str], list[float]] = {}
    for record in records:
        route_events = record.get("route_events") if isinstance(record.get("route_events"), list) else []
        successful = next(
            (item for item in route_events if isinstance(item, dict) and str(item.get("status")) in {"ok", "blocked"}),
            {},
        )
        key = (
            str(successful.get("provider") or "unknown"),
            str(successful.get("model") or "default"),
            str(successful.get("route_family") or "unknown"),
            "true" if bool(successful.get("fast_mode")) else "false",
        )
        timing = record.get("timing_ms") if isinstance(record.get("timing_ms"), dict) else {}
        total = timing.get("total_ms")
        if isinstance(total, (int, float)) and not isinstance(total, bool):
            groups.setdefault(key, []).append(float(total))
    cohorts = [
        CohortSummary(
            source="conversation",
            dimensions={
                "provider": key[0],
                "model": key[1],
                "route_family": key[2],
                "fast_mode": key[3],
            },
            record_count=len(durations),
            success_count=len(durations),
            failure_count=0,
            duration=summarize(durations),
        )
        for key, durations in groups.items()
    ]
    return sorted(cohorts, key=lambda item: (-item.record_count, sorted(item.dimensions.items())))[:50]


def _read_jsonl(
    path: Path,
    *,
    maximum_records: int,
    record_filter: Callable[[dict[str, Any]], bool] | None = None,
) -> list[dict[str, Any]]:
    if not path.exists() or not path.is_file():
        return []
    records: deque[dict[str, Any]] = deque(maxlen=maximum_records)
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and (record_filter is None or record_filter(payload)):
                records.append(payload)
    return list(records)


def _production_route_record(record: dict[str, Any]) -> bool:
    return str(record.get("interaction_mode") or "").strip().lower() not in {
        "evaluation",
        "improvement",
    }


def _metric_values(metric: MetricContract, records: Iterable[dict[str, Any]]) -> list[float]:
    values: list[float] = []
    for record in records:
        value: Any = record
        for component in metric.path.split("."):
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(component)
        if metric.aggregation == "rate":
            values.append(100.0 if str(value) in metric.match_values else 0.0)
        elif isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
            values.append(float(value))
    return values


def summarize(values: Iterable[float]) -> DistributionSummary:
    cleaned = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not cleaned:
        return DistributionSummary()
    return DistributionSummary(
        count=len(cleaned),
        mean=round(sum(cleaned) / len(cleaned), 3),
        p50=round(_percentile(cleaned, 0.50), 3),
        p95=round(_percentile(cleaned, 0.95), 3),
        p99=round(_percentile(cleaned, 0.99), 3),
        minimum=round(cleaned[0], 3),
        maximum=round(cleaned[-1], 3),
    )


def _percentile(sorted_values: list[float], quantile: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    fraction = position - lower
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * fraction


def _directional_change_percent(baseline: float, candidate: float, *, direction: str) -> float:
    if baseline == 0:
        if candidate == 0:
            return 0.0
        return -100.0 if direction == "lower" else 100.0
    raw_change = 100.0 * (candidate - baseline) / abs(baseline)
    return -raw_change if direction == "lower" else raw_change
