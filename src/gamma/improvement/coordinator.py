from __future__ import annotations

import json
import math
import os
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Iterator, Literal

from pydantic import BaseModel, Field, model_validator

from ..config import PROJECT_ROOT
from .candidates import (
    CandidateAttemptFeedback,
    CandidateDraftGenerator,
    CandidateTestFeedback,
    apply_candidate_draft,
    candidate_plan_sha256,
)
from .contract import ImprovementContract
from .experiments import (
    ExperimentManifest,
    ExperimentStore,
    ExperimentWorkspaceManager,
    _file_sha256,
    _run_git,
    normalize_experiment_path,
)
from .grounded_plans import GroundedPlan, _grounding_sha256, validate_grounded_plan
from .grounding import SourceGroundingReport
from .models import ChangeClass
from .semantic_review import CandidateSemanticReviewer
from .validation import CandidateValidationReport, CandidateValidator


_SERIES_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{5,74}$")
class ExperimentAttemptRecord(BaseModel):
    attempt_number: int = Field(ge=1, le=10)
    experiment_id: str
    requested_model: str = Field(min_length=1, max_length=200)
    actual_provider: str | None = Field(default=None, max_length=80)
    actual_model: str | None = Field(default=None, max_length=200)
    outcome: Literal[
        "draft_rejected",
        "needs_more_source",
        "validation_failed",
        "fixed_tests_passed",
        "semantic_review_rejected",
        "ready_for_holdout",
        "deadline_exhausted",
        "infrastructure_error",
    ]
    rejection_codes: tuple[str, ...] = ()
    candidate_artifact: str | None = None
    candidate_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    receipt_artifact: str | None = None
    receipt_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    validation_artifact: str | None = None
    validation_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    semantic_review_artifact: str | None = None
    semantic_review_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    started_at: str
    completed_at: str

    @model_validator(mode="after")
    def validate_record(self) -> "ExperimentAttemptRecord":
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{5,79}", self.experiment_id):
            raise ValueError("invalid attempt experiment id")
        if len(self.rejection_codes) > 12 or any(
            not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", code)
            for code in self.rejection_codes
        ):
            raise ValueError("invalid or excessive attempt rejection codes")
        for path_field, digest_field in (
            ("candidate_artifact", "candidate_sha256"),
            ("receipt_artifact", "receipt_sha256"),
            ("validation_artifact", "validation_sha256"),
            ("semantic_review_artifact", "semantic_review_sha256"),
        ):
            artifact = getattr(self, path_field)
            digest = getattr(self, digest_field)
            if (artifact is None) != (digest is None):
                raise ValueError(f"{path_field} and {digest_field} must appear together")
            if artifact is None:
                continue
            path = PurePosixPath(artifact)
            expected_prefix = PurePosixPath("attempts") / self.experiment_id
            if path.is_absolute() or ".." in path.parts or path.parent != expected_prefix:
                raise ValueError("attempt artifact path escaped its experiment directory")
        return self


class ExperimentSeriesManifest(BaseModel):
    version: int = 1
    id: str
    hypothesis: str = Field(min_length=12, max_length=2000)
    domain: str = Field(min_length=2, max_length=80)
    change_class: ChangeClass
    baseline_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fixture_catalog_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    grounding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    allowed_paths: tuple[str, ...]
    models: tuple[str, ...]
    maximum_changed_files: int = Field(default=12, ge=1, le=100)
    maximum_attempts: int = Field(default=3, ge=1, le=10)
    maximum_wall_clock_minutes: int = Field(default=60, ge=5, le=240)
    status: Literal[
        "planned",
        "running",
        "fixed_tests_passed",
        "ready_for_holdout",
        "exhausted",
        "failed",
        "abandoned",
    ] = "planned"
    attempts: tuple[ExperimentAttemptRecord, ...] = ()
    successful_experiment_id: str | None = None
    terminal_reason: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_-]{0,119}$")
    created_at: str = Field(default_factory=lambda: _utc_now())
    started_at: str | None = None
    completed_at: str | None = None
    authorization: Literal["model_authored_isolated_candidates_only"] = (
        "model_authored_isolated_candidates_only"
    )

    @model_validator(mode="after")
    def validate_series(self) -> "ExperimentSeriesManifest":
        if not _SERIES_ID_RE.fullmatch(self.id):
            raise ValueError("invalid experiment series id")
        normalized = tuple(normalize_experiment_path(path) for path in self.allowed_paths)
        if not normalized or len(normalized) != len(set(normalized)):
            raise ValueError("series allowed paths must be non-empty and unique")
        self.allowed_paths = normalized
        models = tuple(model.strip() for model in self.models if model.strip())
        if not models or len(models) > 3 or len(models) != len(set(models)):
            raise ValueError("series requires one to three unique explicit models")
        self.models = models
        if len(self.attempts) > self.maximum_attempts:
            raise ValueError("series attempt count exceeds its maximum")
        expected = tuple(range(1, len(self.attempts) + 1))
        if tuple(item.attempt_number for item in self.attempts) != expected:
            raise ValueError("series attempt records must be contiguous and ordered")
        if any(
            item.experiment_id != f"{self.id}-a{item.attempt_number:02d}"
            for item in self.attempts
        ):
            raise ValueError("series attempt id does not match its series and ordinal")
        if self.status in {"fixed_tests_passed", "ready_for_holdout"} and not self.successful_experiment_id:
            raise ValueError(f"{self.status} series requires a successful experiment id")
        if self.successful_experiment_id and not any(
            item.experiment_id == self.successful_experiment_id
            and item.outcome == self.status
            for item in self.attempts
        ):
            raise ValueError("successful experiment id does not match the terminal series state")
        if self.status == "running" and not self.started_at:
            raise ValueError("running series requires started_at")
        if self.status in {"fixed_tests_passed", "ready_for_holdout", "exhausted", "failed", "abandoned"}:
            if not self.started_at or not self.completed_at or not self.terminal_reason:
                raise ValueError("terminal series requires start, completion, and reason fields")
        return self


class ExperimentSeriesStore:
    """Crash-visible series state with append-only audit events and a single-run lock."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def create(self, manifest: ExperimentSeriesManifest) -> Path:
        series_dir = self.root / manifest.id
        series_dir.mkdir(parents=True, exist_ok=False)
        (series_dir / "attempts").mkdir()
        path = series_dir / "manifest.json"
        _write_json(path, manifest.model_dump(mode="json"), exclusive=True)
        self._audit(manifest.id, event="created", details={"status": manifest.status})
        return path

    def read(self, series_id: str) -> ExperimentSeriesManifest:
        self._validate_id(series_id)
        return ExperimentSeriesManifest.model_validate_json(
            (self.root / series_id / "manifest.json").read_text(encoding="utf-8")
        )

    def start(self, series_id: str) -> ExperimentSeriesManifest:
        manifest = self.read(series_id)
        if manifest.status == "running":
            return manifest
        if manifest.status != "planned":
            raise ValueError(f"series cannot run from terminal status:{manifest.status}")
        manifest.status = "running"
        manifest.started_at = _utc_now()
        self._replace(manifest)
        self._audit(series_id, event="started", details={"status": manifest.status})
        return manifest

    def record_attempt(
        self,
        series_id: str,
        record: ExperimentAttemptRecord,
    ) -> ExperimentSeriesManifest:
        manifest = self.read(series_id)
        if manifest.status != "running":
            raise ValueError("attempts may only be recorded for a running series")
        if record.attempt_number != len(manifest.attempts) + 1:
            raise ValueError("attempt record is not the next contiguous attempt")
        if record.attempt_number > manifest.maximum_attempts:
            raise ValueError("attempt record exceeds the series maximum")
        manifest.attempts = (*manifest.attempts, record)
        self._replace(manifest)
        self._audit(
            series_id,
            event="attempt_completed",
            details={
                "attempt_number": record.attempt_number,
                "experiment_id": record.experiment_id,
                "model": record.requested_model,
                "outcome": record.outcome,
                "candidate_sha256": record.candidate_sha256,
                "receipt_sha256": record.receipt_sha256,
                "validation_sha256": record.validation_sha256,
                "semantic_review_sha256": record.semantic_review_sha256,
            },
        )
        return manifest

    def finish(
        self,
        series_id: str,
        *,
        status: Literal[
            "fixed_tests_passed",
            "ready_for_holdout",
            "exhausted",
            "failed",
            "abandoned",
        ],
        reason: str,
        successful_experiment_id: str | None = None,
    ) -> ExperimentSeriesManifest:
        manifest = self.read(series_id)
        if manifest.status != "running":
            raise ValueError("only a running series may be finished")
        if status in {"fixed_tests_passed", "ready_for_holdout"} and not successful_experiment_id:
            raise ValueError("successful experiment id is required")
        manifest.status = status
        manifest.terminal_reason = reason
        manifest.successful_experiment_id = successful_experiment_id
        manifest.completed_at = _utc_now()
        self._replace(manifest)
        self._audit(
            series_id,
            event="finished",
            details={
                "status": status,
                "reason": reason,
                "successful_experiment_id": successful_experiment_id,
            },
        )
        return manifest

    @contextmanager
    def run_lock(self, series_id: str) -> Iterator[None]:
        self._validate_id(series_id)
        path = self.root / series_id / "run.lock"
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise RuntimeError("experiment series already has an active or unrecovered run lock") from exc
        try:
            os.write(
                descriptor,
                (json.dumps({"pid": os.getpid(), "created_at": _utc_now()}) + "\n").encode("utf-8"),
            )
            os.close(descriptor)
            descriptor = -1
            yield
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            path.unlink(missing_ok=True)

    def series_dir(self, series_id: str) -> Path:
        self._validate_id(series_id)
        return self.root / series_id

    def _replace(self, manifest: ExperimentSeriesManifest) -> None:
        _write_json(
            self.root / manifest.id / "manifest.json",
            manifest.model_dump(mode="json"),
            exclusive=False,
        )

    def _audit(self, series_id: str, *, event: str, details: dict[str, object]) -> None:
        with (self.root / series_id / "audit.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps({"timestamp": _utc_now(), "event": event, **details}, sort_keys=True)
                + "\n"
            )

    @staticmethod
    def _validate_id(series_id: str) -> None:
        if not _SERIES_ID_RE.fullmatch(series_id):
            raise ValueError("invalid experiment series id")


class BoundedExperimentCoordinator:
    """Run independent candidate attempts without live mutation or promotion authority."""

    def __init__(
        self,
        *,
        candidate_generator: CandidateDraftGenerator,
        candidate_validator: CandidateValidator,
        semantic_reviewer: CandidateSemanticReviewer | None = None,
    ) -> None:
        self.candidate_generator = candidate_generator
        self.candidate_validator = candidate_validator
        self.semantic_reviewer = semantic_reviewer

    def run(
        self,
        *,
        store: ExperimentSeriesStore,
        series_id: str,
        plan: GroundedPlan,
        grounding: SourceGroundingReport,
        contract: ImprovementContract,
        current_contract_sha256: str,
        current_fixture_catalog_sha256: str,
        project_root: Path = PROJECT_ROOT,
        worktree_root: Path,
    ) -> ExperimentSeriesManifest:
        if not contract.policy.isolated_experiments_enabled:
            raise PermissionError("isolated candidate work is disabled by the improvement contract")
        with store.run_lock(series_id):
            series = store.start(series_id)
            self._verify_series_inputs(
                series,
                plan=plan,
                grounding=grounding,
                current_contract_sha256=current_contract_sha256,
                current_fixture_catalog_sha256=current_fixture_catalog_sha256,
                project_root=project_root,
            )
            deadline = _parse_utc(series.started_at or series.created_at) + timedelta(
                minutes=series.maximum_wall_clock_minutes
            )
            feedback = self._load_feedback(store, series)
            attempt_store = ExperimentStore(store.series_dir(series.id) / "attempts")
            manager = ExperimentWorkspaceManager(
                project_root=project_root,
                worktree_root=worktree_root,
            )

            for attempt_number in range(len(series.attempts) + 1, series.maximum_attempts + 1):
                if _remaining_seconds(deadline) <= 1.0:
                    return store.finish(series.id, status="exhausted", reason="wall_clock_limit")
                requested_model = series.models[(attempt_number - 1) % len(series.models)]
                experiment_id = f"{series.id}-a{attempt_number:02d}"
                started_at = _utc_now()
                candidate_path = store.series_dir(series.id) / "attempts" / experiment_id / "candidate.json"
                receipt_path = candidate_path.with_name("receipt.json")
                validation_path = candidate_path.with_name("validation.json")
                review_path = candidate_path.with_name("semantic-review.json")
                manifest = ExperimentManifest(
                    id=experiment_id,
                    hypothesis=series.hypothesis,
                    domain=series.domain,
                    change_class=series.change_class,
                    baseline_commit=series.baseline_commit,
                    contract_sha256=series.contract_sha256,
                    fixture_catalog_sha256=series.fixture_catalog_sha256,
                    allowed_paths=series.allowed_paths,
                    maximum_changed_files=series.maximum_changed_files,
                    maximum_attempts=1,
                    maximum_wall_clock_minutes=max(
                        1,
                        min(240, math.ceil(_remaining_seconds(deadline) / 60.0)),
                    ),
                )
                try:
                    attempt_store.create(manifest)
                    workspace = manager.create(manifest, enabled=True)
                    manifest = attempt_store.transition(experiment_id, "workspace_ready")
                    batch = self.candidate_generator.generate(
                        manifest=manifest,
                        plan=plan,
                        grounding=grounding,
                        workspace=workspace,
                        model_override=requested_model,
                        prior_attempt_feedback=tuple(feedback),
                    )
                    candidate_payload = {
                        "authority": "candidate_draft_only",
                        "attempt_number": attempt_number,
                        "requested_model": requested_model,
                        "prior_attempt_feedback": [item.model_dump(mode="json") for item in feedback],
                        "batch": batch.model_dump(mode="json"),
                    }
                    _write_json(candidate_path, candidate_payload, exclusive=True)
                    draft = batch.draft
                    actual_provider = draft.provider if draft else None
                    actual_model = draft.model if draft else None
                    if draft is None:
                        attempt_store.transition(experiment_id, "rejected")
                        record = self._record(
                            store,
                            series,
                            attempt_number=attempt_number,
                            experiment_id=experiment_id,
                            requested_model=requested_model,
                            actual_provider=actual_provider,
                            actual_model=actual_model,
                            outcome="draft_rejected",
                            rejection_codes=tuple(item.code for item in batch.rejections),
                            candidate_path=candidate_path,
                            started_at=started_at,
                        )
                        feedback.append(_feedback_from_record(record, None))
                        series = store.read(series.id)
                        continue
                    if draft.status == "needs_more_source":
                        attempt_store.transition(experiment_id, "rejected")
                        record = self._record(
                            store,
                            series,
                            attempt_number=attempt_number,
                            experiment_id=experiment_id,
                            requested_model=requested_model,
                            actual_provider=actual_provider,
                            actual_model=actual_model,
                            outcome="needs_more_source",
                            rejection_codes=("needs_more_source",),
                            candidate_path=candidate_path,
                            started_at=started_at,
                        )
                        feedback.append(_feedback_from_record(record, None))
                        series = store.read(series.id)
                        continue

                    receipt = apply_candidate_draft(
                        draft,
                        manifest=manifest,
                        plan=plan,
                        grounding=grounding,
                        workspace=workspace,
                    )
                    _write_json(receipt_path, receipt.model_dump(mode="json"), exclusive=True)
                    manifest = attempt_store.transition(experiment_id, "candidate_ready")
                    remaining = _remaining_seconds(deadline)
                    if remaining <= 1.0:
                        attempt_store.transition(experiment_id, "rejected")
                        self._record(
                            store,
                            series,
                            attempt_number=attempt_number,
                            experiment_id=experiment_id,
                            requested_model=requested_model,
                            actual_provider=actual_provider,
                            actual_model=actual_model,
                            outcome="deadline_exhausted",
                            rejection_codes=("wall_clock_limit",),
                            candidate_path=candidate_path,
                            receipt_path=receipt_path,
                            started_at=started_at,
                        )
                        return store.finish(series.id, status="exhausted", reason="wall_clock_limit")
                    validation = self.candidate_validator.validate(
                        manifest=manifest,
                        receipt=receipt,
                        workspace=workspace,
                        required_gates=contract.policy.gates_for(series.change_class),
                        maximum_duration_seconds=remaining,
                    )
                    _write_json(validation_path, validation.model_dump(mode="json"), exclusive=True)
                    if not validation.passed:
                        attempt_store.transition(experiment_id, "rejected")
                        record = self._record(
                            store,
                            series,
                            attempt_number=attempt_number,
                            experiment_id=experiment_id,
                            requested_model=requested_model,
                            actual_provider=actual_provider,
                            actual_model=actual_model,
                            outcome="validation_failed",
                            rejection_codes=("fixed_validation_failed",),
                            candidate_path=candidate_path,
                            receipt_path=receipt_path,
                            validation_path=validation_path,
                            started_at=started_at,
                        )
                        feedback.append(_feedback_from_record(record, validation))
                        series = store.read(series.id)
                        continue

                    reviewer_models = tuple(
                        model for model in series.models if model != requested_model
                    )
                    if self.semantic_reviewer is None or not reviewer_models:
                        self._record(
                            store,
                            series,
                            attempt_number=attempt_number,
                            experiment_id=experiment_id,
                            requested_model=requested_model,
                            actual_provider=actual_provider,
                            actual_model=actual_model,
                            outcome="fixed_tests_passed",
                            rejection_codes=("independent_semantic_review_required",),
                            candidate_path=candidate_path,
                            receipt_path=receipt_path,
                            validation_path=validation_path,
                            started_at=started_at,
                        )
                        return store.finish(
                            series.id,
                            status="fixed_tests_passed",
                            reason="independent_semantic_review_required",
                            successful_experiment_id=experiment_id,
                        )

                    review_batches = [
                        self.semantic_reviewer.review(
                            manifest_id=experiment_id,
                            plan=plan,
                            draft=draft,
                            validation=validation,
                            model_override=model,
                        )
                        for model in reviewer_models
                    ]
                    _write_json(
                        review_path,
                        {
                            "authority": "review_advice_only",
                            "requested_reviewers": reviewer_models,
                            "batches": [item.model_dump(mode="json") for item in review_batches],
                        },
                        exclusive=True,
                    )
                    review_codes = tuple(
                        dict.fromkeys(
                            code
                            for batch in review_batches
                            for code in (
                                (batch.rejection_code,)
                                if batch.review is None
                                else batch.review.reasons
                            )
                            if code
                        )
                    )
                    review_passed = all(
                        batch.review is not None
                        and batch.review.decision == "ready_for_holdout"
                        for batch in review_batches
                    )
                    if not review_passed:
                        attempt_store.transition(experiment_id, "rejected")
                        record = self._record(
                            store,
                            series,
                            attempt_number=attempt_number,
                            experiment_id=experiment_id,
                            requested_model=requested_model,
                            actual_provider=actual_provider,
                            actual_model=actual_model,
                            outcome="semantic_review_rejected",
                            rejection_codes=review_codes or ("semantic_review_rejected",),
                            candidate_path=candidate_path,
                            receipt_path=receipt_path,
                            validation_path=validation_path,
                            review_path=review_path,
                            started_at=started_at,
                        )
                        feedback.append(_feedback_from_record(record, validation))
                        series = store.read(series.id)
                        continue

                    self._record(
                        store,
                        series,
                        attempt_number=attempt_number,
                        experiment_id=experiment_id,
                        requested_model=requested_model,
                        actual_provider=actual_provider,
                        actual_model=actual_model,
                        outcome="ready_for_holdout",
                        rejection_codes=(),
                        candidate_path=candidate_path,
                        receipt_path=receipt_path,
                        validation_path=validation_path,
                        review_path=review_path,
                        started_at=started_at,
                    )
                    return store.finish(
                        series.id,
                        status="ready_for_holdout",
                        reason="independent_semantic_review_passed",
                        successful_experiment_id=experiment_id,
                    )
                except Exception as exc:
                    _abandon_attempt_safely(attempt_store, experiment_id)
                    error_code = _safe_error_code(exc)
                    self._record(
                        store,
                        series,
                        attempt_number=attempt_number,
                        experiment_id=experiment_id,
                        requested_model=requested_model,
                        outcome="infrastructure_error",
                        rejection_codes=(error_code,),
                        candidate_path=candidate_path if candidate_path.exists() else None,
                        receipt_path=receipt_path if receipt_path.exists() else None,
                        validation_path=validation_path if validation_path.exists() else None,
                        review_path=review_path if review_path.exists() else None,
                        started_at=started_at,
                    )
                    return store.finish(series.id, status="failed", reason=error_code)

            return store.finish(series.id, status="exhausted", reason="attempt_limit")

    @staticmethod
    def _verify_series_inputs(
        series: ExperimentSeriesManifest,
        *,
        plan: GroundedPlan,
        grounding: SourceGroundingReport,
        current_contract_sha256: str,
        current_fixture_catalog_sha256: str,
        project_root: Path,
    ) -> None:
        if series.contract_sha256 != current_contract_sha256:
            raise ValueError("series improvement contract is stale")
        if series.fixture_catalog_sha256 != current_fixture_catalog_sha256:
            raise ValueError("series fixture catalog is stale")
        if series.plan_sha256 != candidate_plan_sha256(plan):
            raise ValueError("series grounded plan is stale or mismatched")
        if series.grounding_sha256 != _grounding_sha256(grounding):
            raise ValueError("series source grounding is stale or mismatched")
        if tuple(plan.allowed_paths) != series.allowed_paths:
            raise ValueError("series path scope differs from its grounded plan")
        validate_grounded_plan(plan, grounding, project_root=project_root)

    @staticmethod
    def _load_feedback(
        store: ExperimentSeriesStore,
        series: ExperimentSeriesManifest,
    ) -> list[CandidateAttemptFeedback]:
        feedback: list[CandidateAttemptFeedback] = []
        for record in series.attempts[-3:]:
            validation = None
            if record.validation_artifact:
                validation = CandidateValidationReport.model_validate_json(
                    (store.series_dir(series.id) / record.validation_artifact).read_text(encoding="utf-8")
                )
            feedback.append(_feedback_from_record(record, validation))
        return feedback

    @staticmethod
    def _record(
        store: ExperimentSeriesStore,
        series: ExperimentSeriesManifest,
        *,
        attempt_number: int,
        experiment_id: str,
        requested_model: str,
        outcome: str,
        rejection_codes: tuple[str, ...],
        started_at: str,
        actual_provider: str | None = None,
        actual_model: str | None = None,
        candidate_path: Path | None = None,
        receipt_path: Path | None = None,
        validation_path: Path | None = None,
        review_path: Path | None = None,
    ) -> ExperimentAttemptRecord:
        series_dir = store.series_dir(series.id)
        record = ExperimentAttemptRecord(
            attempt_number=attempt_number,
            experiment_id=experiment_id,
            requested_model=requested_model,
            actual_provider=actual_provider,
            actual_model=actual_model,
            outcome=outcome,
            rejection_codes=tuple(sorted(set(rejection_codes))),
            candidate_artifact=_relative_artifact(series_dir, candidate_path),
            candidate_sha256=_optional_file_sha256(candidate_path),
            receipt_artifact=_relative_artifact(series_dir, receipt_path),
            receipt_sha256=_optional_file_sha256(receipt_path),
            validation_artifact=_relative_artifact(series_dir, validation_path),
            validation_sha256=_optional_file_sha256(validation_path),
            semantic_review_artifact=_relative_artifact(series_dir, review_path),
            semantic_review_sha256=_optional_file_sha256(review_path),
            started_at=started_at,
            completed_at=_utc_now(),
        )
        store.record_attempt(series.id, record)
        return record


def build_series_manifest(
    *,
    series_id: str,
    hypothesis: str,
    domain: str,
    change_class: ChangeClass,
    baseline_commit: str,
    plan: GroundedPlan,
    grounding: SourceGroundingReport,
    models: tuple[str, ...],
    maximum_changed_files: int = 12,
    maximum_attempts: int = 3,
    maximum_wall_clock_minutes: int = 60,
    contract_path: Path | None = None,
    fixture_catalog_path: Path | None = None,
    project_root: Path = PROJECT_ROOT,
) -> ExperimentSeriesManifest:
    if plan.status != "grounded_plan":
        raise ValueError(
            f"experiment series requires a grounded_plan; {plan.status} is not actionable"
        )
    if plan.grounding_sha256 != _grounding_sha256(grounding):
        raise ValueError("experiment series plan and source grounding digests differ")
    validate_grounded_plan(plan, grounding, project_root=project_root)
    _run_git(project_root.resolve(), ["cat-file", "-e", f"{baseline_commit}^{{commit}}"])
    contract_path = contract_path or PROJECT_ROOT / "config" / "improvement.toml"
    fixture_catalog_path = fixture_catalog_path or PROJECT_ROOT / "evaluations" / "improvement" / "conversation.toml"
    return ExperimentSeriesManifest(
        id=series_id,
        hypothesis=hypothesis,
        domain=domain,
        change_class=change_class,
        baseline_commit=baseline_commit,
        contract_sha256=_file_sha256(contract_path),
        fixture_catalog_sha256=_file_sha256(fixture_catalog_path),
        plan_sha256=candidate_plan_sha256(plan),
        grounding_sha256=_grounding_sha256(grounding),
        allowed_paths=plan.allowed_paths,
        models=models,
        maximum_changed_files=maximum_changed_files,
        maximum_attempts=maximum_attempts,
        maximum_wall_clock_minutes=maximum_wall_clock_minutes,
    )


def _feedback_from_record(
    record: ExperimentAttemptRecord,
    validation: CandidateValidationReport | None,
) -> CandidateAttemptFeedback:
    tests = ()
    if validation is not None:
        tests = tuple(
            CandidateTestFeedback(
                profile=item.profile,
                passed=item.passed,
                return_code=item.return_code,
                output_sha256=item.output_sha256,
                output_tail=item.output_tail[-4000:],
            )
            for item in validation.test_results
        )
    return CandidateAttemptFeedback(
        attempt_number=record.attempt_number,
        requested_model=record.requested_model,
        outcome=record.outcome,
        rejection_codes=record.rejection_codes,
        tests=tests,
    )


def _write_json(path: Path, payload: object, *, exclusive: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if exclusive:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(content)
        return
    replacement = path.with_suffix(path.suffix + ".next")
    replacement.write_text(content, encoding="utf-8")
    replacement.replace(path)


def _relative_artifact(series_dir: Path, path: Path | None) -> str | None:
    if path is None:
        return None
    resolved = path.resolve()
    try:
        return resolved.relative_to(series_dir.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError("series artifact escaped its state directory") from exc


def _optional_file_sha256(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    return _file_sha256(path)


def _abandon_attempt_safely(store: ExperimentStore, experiment_id: str) -> None:
    try:
        manifest = store.read(experiment_id)
    except (FileNotFoundError, ValueError):
        return
    if manifest.status not in {"rejected", "promoted", "abandoned"}:
        try:
            store.transition(experiment_id, "abandoned")
        except ValueError:
            return


def _safe_error_code(exc: Exception) -> str:
    if isinstance(exc, PermissionError):
        return "permission_denied"
    if isinstance(exc, TimeoutError):
        return "operation_timeout"
    if isinstance(exc, FileExistsError):
        return "artifact_or_workspace_collision"
    if isinstance(exc, (ValueError, SyntaxError)):
        return "deterministic_validation_error"
    return "coordinator_infrastructure_error"


def _remaining_seconds(deadline: datetime) -> float:
    return (deadline - datetime.now(timezone.utc)).total_seconds()


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
