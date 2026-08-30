from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import PROJECT_ROOT, settings
from ..errors import GammaError
from ..llm.factory import build_llm_adapter
from .candidates import CandidateDraftGenerator
from .contract import load_improvement_contract
from .coordinator import BoundedExperimentCoordinator, ExperimentSeriesStore, build_series_manifest
from .evaluator import ImprovementEvaluator
from .experiments import _file_sha256
from .grounded_plans import GroundedPlan, GroundedPlanGenerator
from .grounding import SourceGroundingReport, build_source_grounding
from .proposals import ImprovementProposal, ImprovementProposalGenerator, ProposalBatch, require_local_proposal_destination
from .review import ProposalReviewReport, review_proposal_batches
from .semantic_review import CandidateSemanticReviewer
from .validation import CandidateValidator
from .work_queue import ImprovementWorkRequest, ImprovementWorkStore, WorkEvent, utc_now


class _WorkDeferred(Exception):
    pass


class _WorkStopped(Exception):
    pass


class AutonomousImprovementRunner:
    """Turn one owner request into bounded evidence, grounding, and isolated candidates."""

    def __init__(self, *, project_root: Path = PROJECT_ROOT, data_root: Path | None = None) -> None:
        self.project_root = project_root.resolve()
        self.data_root = data_root or settings.data_dir / "improvement"
        self.control_root = self.data_root / "control"
        self.store = ImprovementWorkStore(self.control_root)
        self.contract_path = self.project_root / "config" / "improvement.toml"
        self.fixture_path = self.project_root / "evaluations" / "improvement" / "conversation.toml"
        self.runtime_dir = settings.data_dir / "runtime"

    def run(self, request_id: str) -> ImprovementWorkRequest:
        request = self.store.load(request_id)
        if request.status not in {"queued", "running"}:
            return request
        self._start(request_id)
        try:
            self._checkpoint(request_id)
            self._verify_live_checkout()
            require_local_proposal_destination()
            while True:
                request = self._checkpoint(request_id)
                if request.cycle_count >= request.maximum_cycles:
                    return self._finish(
                        request_id,
                        status="exhausted",
                        summary="The bounded discovery cycles finished without a review-ready candidate.",
                        reason="cycle_limit",
                    )
                remaining_minutes = self._remaining_minutes(request)
                if remaining_minutes < 5:
                    return self._finish(
                        request_id,
                        status="exhausted",
                        summary="The authorized wall-clock budget ended without a review-ready candidate.",
                        reason="work_budget_exhausted",
                    )
                cycle = request.cycle_count + 1
                self._update(
                    request_id,
                    stage="observing",
                    message=f"Cycle {cycle}: measuring the current aggregate baseline.",
                    cycle_count=cycle,
                )
                result = self._run_cycle(request_id, cycle=cycle)
                if result is not None:
                    return result
        except _WorkDeferred:
            return self.store.load(request_id)
        except _WorkStopped:
            return self.store.load(request_id)
        except Exception as exc:
            print(f"Improvement worker stopped safely: {_safe_error_code(exc)}", file=sys.stderr, flush=True)
            return self._finish(
                request_id,
                status="failed",
                summary="The autonomous worker stopped safely after an infrastructure or validation error.",
                reason=_safe_error_code(exc),
            )

    def _run_cycle(self, request_id: str, *, cycle: int) -> ImprovementWorkRequest | None:
        request = self._checkpoint(request_id)
        contract = load_improvement_contract(self.contract_path)
        report = ImprovementEvaluator(contract).observe(self.runtime_dir)
        cycle_root = self.data_root / "work" / request.id / f"cycle-{cycle:02d}"
        cycle_root.mkdir(parents=True, exist_ok=False)
        _write_json(cycle_root / "observation.json", report.model_dump(mode="json"))

        self._checkpoint(request_id)
        prior_feedback = _previous_proposal_feedback(
            self.data_root / "work" / request.id,
            cycle=cycle,
            series_root=self.data_root / "series",
            request_id=request.id,
        )
        _write_json(
            cycle_root / "prior-feedback.json",
            {
                "authority": "failure_lessons_only",
                "lesson_count": len(prior_feedback),
                "lessons": prior_feedback,
            },
        )
        feedback_detail = (
            f" while applying {len(prior_feedback)} bounded prior failure lessons"
            if prior_feedback
            else ""
        )
        self._update(
            request_id,
            stage="proposing",
            message=(
                f"Cycle {cycle}: asking {len(request.models)} local models for evidence-bound "
                f"hypotheses{feedback_detail}."
            ),
        )
        llm = build_llm_adapter()
        excluded_hypotheses = {
            _hypothesis_digest(str(item["hypothesis"]))
            for item in prior_feedback
            if item.get("hypothesis")
        }
        proposal_batches: list[ProposalBatch] = []
        available_models: list[str] = []
        for model in request.models:
            try:
                batch = ImprovementProposalGenerator(llm).generate(
                    report=report,
                    contract=contract,
                    maximum_proposals=3,
                    model_override=model,
                    operator_goal=request.goal,
                    focus_domains=request.focus_domains,
                    prior_cycle_feedback=prior_feedback,
                )
            except GammaError:
                self._update(
                    request_id,
                    stage="proposing",
                    message=f"Cycle {cycle}: model {model} was unavailable; continuing when independent review remains possible.",
                    code="proposal_model_unavailable",
                )
                continue
            proposal_batches.append(batch)
            available_models.append(model)
        _write_json(
            cycle_root / "proposals.json",
            {"authority": "proposal_only", "batches": [batch.model_dump(mode="json") for batch in proposal_batches]},
        )
        if len(available_models) < 2:
            return self._finish(
                request_id,
                status="failed",
                summary="Fewer than two configured local models were available, so Gamma stopped before autonomous candidate work.",
                reason="insufficient_available_models",
            )

        self._checkpoint(request_id)
        self._update(request_id, stage="reviewing", message=f"Cycle {cycle}: screening proposals and ranking independent agreement.")
        review = review_proposal_batches(proposal_batches, report)
        _write_json(cycle_root / "proposal-review.json", review.model_dump(mode="json"))
        ranked = _rank_proposals(
            proposal_batches,
            review,
            request.focus_domains,
            excluded_hypothesis_sha256s=excluded_hypotheses,
        )
        if not ranked:
            reason = (
                "no_novel_screened_proposal"
                if excluded_hypotheses and any(batch.proposals for batch in proposal_batches)
                else "no_screened_proposal"
            )
            self._update(
                request_id,
                stage="cycle_complete",
                message=(
                    f"Cycle {cycle}: no novel proposal passed deterministic screening; Gamma will re-observe with prior-cycle feedback if budget remains."
                    if reason == "no_novel_screened_proposal"
                    else f"Cycle {cycle}: no proposal passed deterministic screening; Gamma will re-observe if budget remains."
                ),
                code=reason,
            )
            return None

        for proposal in ranked[:5]:
            self._checkpoint(request_id)
            self._update(request_id, stage="grounding", message=f"Cycle {cycle}: verifying a {proposal.domain} hypothesis against pinned source.")
            try:
                grounding = build_source_grounding(
                    paths=proposal.allowed_paths,
                    target_metrics=proposal.target_metrics,
                    project_root=self.project_root,
                )
            except ValueError:
                continue
            grounding_path = cycle_root / f"grounding-{_short_hash(proposal.hypothesis)}.json"
            _write_json(grounding_path, grounding.model_dump(mode="json"))
            plan_batches = []
            for model in available_models:
                try:
                    plan_batches.append(
                        GroundedPlanGenerator(llm).generate(
                            proposal=proposal,
                            grounding=grounding,
                            observation=report,
                            model_override=model,
                            project_root=self.project_root,
                        )
                    )
                except GammaError:
                    self._update(
                        request_id,
                        stage="grounding",
                        message=f"Cycle {cycle}: model {model} could not complete source grounding; other validated results remain eligible.",
                        code="grounding_model_unavailable",
                    )
            _write_json(
                grounding_path.with_name(grounding_path.stem + "-plans.json"),
                {"authority": "grounding_only", "batches": [batch.model_dump(mode="json") for batch in plan_batches]},
            )
            grounded = sorted(
                (plan for batch in plan_batches for plan in batch.plans if plan.status == "grounded_plan"),
                key=lambda plan: plan.confidence,
                reverse=True,
            )
            if not grounded:
                continue
            return self._run_series(
                request_id,
                cycle=cycle,
                proposal=proposal,
                grounding=grounding,
                plan=grounded[0],
                contract=contract,
                llm=llm,
                models=tuple(available_models),
            )

        self._update(
            request_id,
            stage="cycle_complete",
            message=f"Cycle {cycle}: source grounding did not support an actionable mechanism.",
            code="no_grounded_plan",
        )
        return None

    def _run_series(
        self,
        request_id: str,
        *,
        cycle: int,
        proposal: ImprovementProposal,
        grounding: SourceGroundingReport,
        plan: GroundedPlan,
        contract,
        llm,
        models: tuple[str, ...],
    ) -> ImprovementWorkRequest | None:
        request = self._checkpoint(request_id)
        remaining_minutes = self._remaining_minutes(request)
        series_minutes = max(5, min(240, math.floor(remaining_minutes)))
        baseline_commit = self._git("rev-parse", "HEAD")
        series_id = f"{request.id[:48]}-c{cycle}-{_short_hash(proposal.hypothesis)}"
        series_store = ExperimentSeriesStore(self.data_root / "series")
        manifest = build_series_manifest(
            series_id=series_id,
            hypothesis=plan.mechanism_hypothesis,
            domain=proposal.domain,
            change_class=proposal.change_class,
            baseline_commit=baseline_commit,
            plan=plan,
            grounding=grounding,
            models=models,
            maximum_changed_files=min(12, max(1, len(plan.allowed_paths))),
            maximum_attempts=request.maximum_attempts_per_series,
            maximum_wall_clock_minutes=series_minutes,
            contract_path=self.contract_path,
            fixture_catalog_path=self.fixture_path,
            project_root=self.project_root,
        )
        series_store.create(manifest)
        self._update(
            request_id,
            stage="testing",
            message=f"Cycle {cycle}: running isolated candidate attempts for {proposal.domain}.",
            current_series_id=series_id,
        )
        configured_root = Path(contract.policy.experiment_worktree_root)
        if not configured_root.is_absolute():
            configured_root = self.project_root / configured_root
        result = BoundedExperimentCoordinator(
            candidate_generator=CandidateDraftGenerator(llm),
            candidate_validator=CandidateValidator(),
            semantic_reviewer=CandidateSemanticReviewer(llm),
        ).run(
            store=series_store,
            series_id=series_id,
            plan=plan,
            grounding=grounding,
            contract=contract,
            current_contract_sha256=_file_sha256(self.contract_path),
            current_fixture_catalog_sha256=_file_sha256(self.fixture_path),
            project_root=self.project_root,
            worktree_root=configured_root,
            control_check=lambda: _coordinator_control(self.store.load(request_id)),
        )
        if result.status in {"fixed_tests_passed", "ready_for_holdout"}:
            return self._finish(
                request_id,
                status="review_ready",
                summary="An isolated candidate passed fixed validation and is ready for additional evidence and owner review.",
                reason=result.status,
                current_series_id=series_id,
            )
        self._update(
            request_id,
            stage="cycle_complete",
            message=f"Cycle {cycle}: the isolated series ended as {result.status}; no live files were changed.",
            code=result.terminal_reason or result.status,
            current_series_id=series_id,
        )
        return None

    def _start(self, request_id: str) -> None:
        def apply(request: ImprovementWorkRequest) -> ImprovementWorkRequest:
            request.status = "running"
            request.stage = "starting"
            request.started_at = request.started_at or utc_now()
            request.events = (*request.events, WorkEvent(stage="starting", message="Autonomous improvement worker started."))[-50:]
            return request

        self.store.mutate(request_id, apply)

    def _checkpoint(self, request_id: str) -> ImprovementWorkRequest:
        request = self.store.load(request_id)
        if request.desired_state == "paused":
            def pause(item: ImprovementWorkRequest) -> ImprovementWorkRequest:
                item.status = "paused"
                item.stage = "paused"
                item.events = (*item.events, WorkEvent(stage="paused", message="Worker paused at a safe stage boundary."))[-50:]
                return item

            self.store.mutate(request_id, pause)
            raise _WorkDeferred()
        if request.desired_state == "stopped":
            self._finish(
                request_id,
                status="stopped",
                summary="The owner stopped this work request at a safe stage boundary.",
                reason="owner_stopped",
            )
            raise _WorkStopped()
        if self._remaining_minutes(request) <= 0:
            self._finish(
                request_id,
                status="exhausted",
                summary="The authorized wall-clock budget ended.",
                reason="work_budget_exhausted",
            )
            raise _WorkStopped()
        return request

    def _update(
        self,
        request_id: str,
        *,
        stage: str,
        message: str,
        code: str | None = None,
        cycle_count: int | None = None,
        current_series_id: str | None = None,
    ) -> ImprovementWorkRequest:
        def apply(request: ImprovementWorkRequest) -> ImprovementWorkRequest:
            request.stage = stage
            if cycle_count is not None:
                request.cycle_count = cycle_count
            if current_series_id is not None:
                request.current_series_id = current_series_id
            request.events = (*request.events, WorkEvent(stage=stage, message=message, code=code))[-50:]
            if code:
                request.reason_codes = tuple(dict.fromkeys((*request.reason_codes, code)))[-20:]
            return request

        return self.store.mutate(request_id, apply)

    def _finish(
        self,
        request_id: str,
        *,
        status: str,
        summary: str,
        reason: str,
        current_series_id: str | None = None,
    ) -> ImprovementWorkRequest:
        def apply(request: ImprovementWorkRequest) -> ImprovementWorkRequest:
            request.status = status  # type: ignore[assignment]
            request.stage = status
            request.result_summary = summary
            request.completed_at = utc_now()
            if current_series_id is not None:
                request.current_series_id = current_series_id
            request.reason_codes = tuple(dict.fromkeys((*request.reason_codes, reason)))[-20:]
            request.events = (*request.events, WorkEvent(stage=status, message=summary, code=reason))[-50:]
            return request

        return self.store.mutate(request_id, apply)

    def _remaining_minutes(self, request: ImprovementWorkRequest) -> float:
        if not request.started_at:
            return float(request.budget_minutes)
        started = datetime.fromisoformat(request.started_at.replace("Z", "+00:00"))
        elapsed = max(0.0, time.time() - started.timestamp())
        return max(0.0, request.budget_minutes - elapsed / 60.0)

    def _verify_live_checkout(self) -> None:
        if self._git("status", "--porcelain", "--untracked-files=no"):
            raise RuntimeError("live_checkout_dirty")
        if any((self.data_root / "series").glob("*/run.lock")):
            raise RuntimeError("stale_series_lock_present")
        head = self._git("rev-parse", "HEAD")
        if len(head) != 40:
            raise RuntimeError("baseline_commit_unavailable")

    def _git(self, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return completed.stdout.strip()


def run_worker(*, once: bool = False, idle_timeout_seconds: int = 300) -> int:
    runner = AutonomousImprovementRunner()
    idle_started = time.monotonic()
    while True:
        request = runner.store.next_queued()
        if request is not None:
            idle_started = time.monotonic()
            runner.run(request.id)
            if once:
                return 0
            continue
        if once or time.monotonic() - idle_started >= idle_timeout_seconds:
            return 0
        time.sleep(2.0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run bounded queued Gamma improvement work.")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--idle-timeout-seconds", type=int, default=300)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 5 <= args.idle_timeout_seconds <= 3600:
        raise ValueError("idle timeout must be between 5 and 3600 seconds")
    return run_worker(once=args.once, idle_timeout_seconds=args.idle_timeout_seconds)


def _rank_proposals(
    batches: list[ProposalBatch],
    review: ProposalReviewReport,
    focus_domains: tuple[str, ...],
    *,
    excluded_hypothesis_sha256s: set[str] | None = None,
) -> list[ImprovementProposal]:
    proposals = [proposal for batch in batches for proposal in batch.proposals]
    excluded = excluded_hypothesis_sha256s or set()
    support: dict[str, int] = {}
    for consensus in review.consensus:
        for digest in consensus.proposal_hashes:
            support[digest] = max(support.get(digest, 0), consensus.support_count)
    eligible: list[tuple[int, int, float, ImprovementProposal]] = []
    for item in review.reviews:
        if item.proposal_index >= len(proposals):
            continue
        if item.state == "rejected":
            continue
        if item.state == "needs_revision" and set(item.reasons) != {"aggregate_only_proposal_requires_code_grounding"}:
            continue
        proposal = proposals[item.proposal_index]
        review_digest = hashlib.sha256(proposal.hypothesis.encode("utf-8")).hexdigest()
        if _hypothesis_digest(proposal.hypothesis) in excluded:
            continue
        focus_score = int(
            not focus_domains
            or any(domain in proposal.domain.lower() or proposal.domain.lower() in domain for domain in focus_domains)
        )
        eligible.append((focus_score, support.get(review_digest, 0), proposal.confidence, proposal))
    eligible.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return [item[3] for item in eligible]


def _previous_proposal_feedback(
    request_work_root: Path,
    *,
    cycle: int,
    series_root: Path | None = None,
    request_id: str | None = None,
) -> tuple[dict[str, Any], ...]:
    """Build bounded lessons from proposal, grounding, and candidate failures."""
    feedback: list[dict[str, Any]] = []
    seen: set[str] = set()

    def append(item: dict[str, Any]) -> None:
        normalized = json.dumps(item, ensure_ascii=True, sort_keys=True)
        if normalized in seen:
            return
        seen.add(normalized)
        feedback.append(item)

    for previous_cycle in range(1, max(1, cycle)):
        cycle_root = request_work_root / f"cycle-{previous_cycle:02d}"
        payload = _read_feedback_json(cycle_root / "proposals.json")
        if payload is None:
            continue
        batches = payload.get("batches") if isinstance(payload, dict) else None
        if not isinstance(batches, list):
            continue
        for batch in batches:
            rejections = batch.get("rejections") if isinstance(batch, dict) else None
            if isinstance(rejections, list):
                for rejection in rejections[:10]:
                    if not isinstance(rejection, dict):
                        continue
                    codes = _feedback_codes(
                        [rejection.get("code"), *(rejection.get("issues") or [])]
                    )
                    if not codes:
                        continue
                    append(
                        {
                            "stage": "proposal_validation",
                            "outcome": "proposal_rejected",
                            "reason_codes": codes,
                            "lesson": _failure_lesson(codes, "proposal_rejected"),
                        }
                    )
            proposals = batch.get("proposals") if isinstance(batch, dict) else None
            if not isinstance(proposals, list):
                continue
            for proposal in proposals:
                if not isinstance(proposal, dict):
                    continue
                hypothesis = " ".join(str(proposal.get("hypothesis") or "").split())[:1000]
                if len(hypothesis) < 12:
                    continue
                outcome, codes = _grounding_feedback(cycle_root, hypothesis)
                append(
                    {
                        "hypothesis": hypothesis,
                        "domain": " ".join(str(proposal.get("domain") or "unknown").split())[:80],
                        "stage": "source_grounding",
                        "outcome": outcome,
                        "reason_codes": codes,
                        "lesson": _failure_lesson(codes, outcome),
                    }
                )
        if series_root is not None and request_id:
            prefix = f"{request_id[:48]}-c{previous_cycle}-"
            for manifest_path in sorted(series_root.glob(f"{prefix}*/manifest.json")):
                manifest = _read_feedback_json(manifest_path)
                if manifest is None:
                    continue
                codes: list[Any] = [manifest.get("terminal_reason"), manifest.get("status")]
                attempts = manifest.get("attempts")
                if isinstance(attempts, list):
                    for attempt in attempts[-6:]:
                        if not isinstance(attempt, dict):
                            continue
                        codes.append(attempt.get("outcome"))
                        codes.extend(attempt.get("rejection_codes") or [])
                bounded_codes = _feedback_codes(codes)
                outcome = f"candidate_series_{str(manifest.get('status') or 'ended')[:40]}"
                append(
                    {
                        "hypothesis": " ".join(
                            str(manifest.get("hypothesis") or "").split()
                        )[:1000],
                        "domain": " ".join(str(manifest.get("domain") or "unknown").split())[:80],
                        "stage": "candidate_validation",
                        "outcome": outcome,
                        "reason_codes": bounded_codes,
                        "lesson": _failure_lesson(bounded_codes, outcome),
                    }
                )
    return tuple(feedback[-30:])


def _read_feedback_json(path: Path) -> dict[str, Any] | None:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 2_000_000:
            return None
        payload = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _grounding_feedback(cycle_root: Path, hypothesis: str) -> tuple[str, list[str]]:
    payload = _read_feedback_json(
        cycle_root / f"grounding-{_short_hash(hypothesis)}-plans.json"
    )
    if payload is None:
        return "not_selected_for_grounding", ["not_selected_for_grounding"]
    statuses: list[Any] = []
    codes: list[Any] = []
    batches = payload.get("batches")
    if isinstance(batches, list):
        for batch in batches:
            if not isinstance(batch, dict):
                continue
            for plan in batch.get("plans") or []:
                if isinstance(plan, dict):
                    statuses.append(plan.get("status"))
            for rejection in batch.get("rejections") or []:
                if isinstance(rejection, dict):
                    codes.append(rejection.get("code"))
    bounded_codes = _feedback_codes([*statuses, *codes])
    if "grounded_plan" in statuses:
        outcome = "grounded_for_candidate_work"
    elif "refuted" in statuses:
        outcome = "refuted_by_verified_source"
    elif "needs_more_source" in statuses:
        outcome = "insufficient_verified_source"
    else:
        outcome = "grounding_rejected"
    return outcome, bounded_codes


def _feedback_codes(values: list[Any]) -> list[str]:
    codes: list[str] = []
    for value in values:
        code = re.sub(r"[^a-z0-9_.:-]+", "_", str(value or "").strip().lower())[:100]
        if code and code not in codes:
            codes.append(code)
    return codes[:12]


def _failure_lesson(codes: list[str], outcome: str) -> str:
    joined = " ".join([outcome, *codes])
    if "schema" in joined or "unparseable" in joined:
        return "Return the exact required JSON container and fields before proposing a new mechanism."
    if "change_class_scope" in joined:
        return "Use behavior_or_code for source edits and keep paths within the grounded source scope."
    if "refuted" in joined:
        return "Do not repeat a mechanism contradicted by verified source; choose a different measured cause."
    if "citation" in joined or "needs_more_source" in joined or "insufficient_verified_source" in joined:
        return "Narrow the claim to the verified excerpts and cite only exact available symbols and lines."
    if "syntax" in joined or "ambiguous_candidate_edit" in joined:
        return "Produce one syntactically valid, exact, uniquely matching source replacement."
    if "validation_failed" in joined or "fixed_tests" in joined:
        return "Change the mechanism in response to validation codes instead of repeating the failed edit."
    if "semantic_review" in joined:
        return "Preserve intended behavior and address the semantic review rejection before retrying."
    if "observability" in joined:
        return "Use Gamma's structured metrics paths and never add unstructured runtime stdout."
    return "Materially revise the mechanism or choose a different measured opportunity before retrying."


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = path.open("x", encoding="utf-8", errors="strict")
    with descriptor:
        json.dump(payload, descriptor, indent=2, sort_keys=True)
        descriptor.write("\n")


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def _hypothesis_digest(value: str) -> str:
    normalized = " ".join(value.lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _coordinator_control(request: ImprovementWorkRequest) -> str:
    if request.desired_state == "paused":
        return "pause"
    if request.desired_state == "stopped":
        return "stop"
    return "continue"


def _safe_error_code(exc: Exception) -> str:
    detail = str(exc).lower()
    known = (
        "live_checkout_dirty",
        "baseline_commit_unavailable",
        "isolated candidate work is disabled",
        "local model",
        "stale",
        "timeout",
    )
    for marker in known:
        if marker in detail:
            return marker.replace(" ", "_")[:80]
    return f"{type(exc).__name__.lower()}_during_{'worker'}"[:80]


if __name__ == "__main__":
    raise SystemExit(main())
