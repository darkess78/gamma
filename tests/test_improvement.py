from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gamma.improvement.cli import run
from gamma.improvement.candidates import (
    CandidateDraft,
    CandidateDraftGenerator,
    _validate_candidate_metric_integrity,
    apply_candidate_draft,
    validate_candidate_draft,
)
from gamma.improvement.contract import load_improvement_contract
from gamma.improvement.coordinator import (
    BoundedExperimentCoordinator,
    ExperimentSeriesStore,
    build_series_manifest,
)
from gamma.improvement.evaluator import ImprovementEvaluator, summarize
from gamma.improvement.fixtures import FixtureRunner, load_fixture_catalog
from gamma.improvement.grounding import build_source_grounding
from gamma.improvement.grounded_plans import (
    GroundedPlan,
    GroundedPlanGenerator,
    SourceCitation,
    _grounding_sha256,
    validate_grounded_plan,
    validate_grounding_current,
)
from gamma.improvement.experiments import (
    ExperimentManifest,
    ExperimentStore,
    ExperimentWorkspaceManager,
    build_experiment_manifest,
    validate_candidate_scope,
)
from gamma.improvement.models import ChangeClass, MetricState, ValidationEvidence
from gamma.improvement.proposals import (
    ImprovementProposalGenerator,
    _parse_proposals,
    require_local_proposal_destination,
)
from gamma.improvement.review import review_proposal_batches
from gamma.improvement.semantic_review import CandidateSemanticReviewer
from gamma.improvement.validation import (
    CandidateValidator,
    SandboxedTestResult,
    run_sandboxed_test_profile,
)
from gamma.llm.base import LLMReply


ROOT = Path(__file__).resolve().parents[1]


class ImprovementEvaluatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = load_improvement_contract(ROOT / "config" / "improvement.toml")
        self.evaluator = ImprovementEvaluator(self.contract)

    def test_distribution_uses_interpolated_percentiles(self) -> None:
        summary = summarize([1, 2, 3, 4, 5])

        self.assertEqual(summary.count, 5)
        self.assertEqual(summary.p50, 3.0)
        self.assertEqual(summary.p95, 4.8)
        self.assertEqual(summary.p99, 4.96)

    def test_observer_reports_aggregates_without_turn_previews(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            _write_snapshot(runtime_dir, total_ms=1000.0, route_status="error", count=25)
            with (runtime_dir / "llm.routes.jsonl").open("a", encoding="utf-8") as handle:
                for interaction_mode in ("evaluation", "improvement"):
                    handle.write(
                        json.dumps(
                            {
                                "provider": "local",
                                "model": "analysis-model",
                                "route_family": "chat_light",
                                "status": "ok",
                                "duration_ms": 1.0,
                                "interaction_mode": interaction_mode,
                            }
                        )
                        + "\n"
                    )

            report = self.evaluator.observe(runtime_dir)
            rendered = report.model_dump_json()

        self.assertEqual(
            report.source_record_counts,
            {"conversation": 25, "llm_routes": 25, "fixtures": 25, "live_voice": 0},
        )
        self.assertNotIn("private owner request", rendered)
        self.assertNotIn("private assistant reply", rendered)
        self.assertTrue(any(item.kind == "route_failures" for item in report.opportunities))
        route_failure = next(item for item in report.opportunities if item.kind == "route_failures")
        self.assertIn("provider_error", route_failure.evidence)
        self.assertTrue(any(item.kind == "draft_substage_dominance" for item in report.opportunities))
        total = next(item for item in report.metrics if item.metric_id == "conversation.total_ms")
        self.assertEqual(total.summary.p95, 1000.0)
        self.assertTrue(total.sufficient_data)

    def test_bounded_reader_keeps_only_latest_configured_records(self) -> None:
        payload = self.contract.model_dump()
        payload["policy"]["maximum_records_per_source"] = 3
        evaluator = ImprovementEvaluator(type(self.contract).model_validate(payload))
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            _write_snapshot(runtime_dir, total_ms=1000.0, route_status="ok", count=5)

            report = evaluator.observe(runtime_dir)

        self.assertEqual(
            report.source_record_counts,
            {"conversation": 3, "llm_routes": 3, "fixtures": 3, "live_voice": 0},
        )

    def test_live_voice_observation_does_not_emit_transcripts_or_replies(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            _write_snapshot(runtime_dir, total_ms=1000.0, route_status="ok", count=25)
            live_dir = runtime_dir / "live_jobs"
            live_dir.mkdir()
            (live_dir / "history.current.jsonl").write_text(
                json.dumps(
                    {
                        "transcript": "private spoken fixture",
                        "reply_text": "private reply fixture",
                        "timing_ms": {
                            "total_ms": 1200.0,
                            "stt_ms": 200.0,
                            "conversation_ms": 700.0,
                            "tts_ms": 300.0,
                            "time_to_first_chunk_audio_ms": 900.0,
                        },
                        "job": {"status": "completed"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            report = self.evaluator.observe(runtime_dir)
            rendered = report.model_dump_json()

        self.assertEqual(report.source_record_counts["live_voice"], 1)
        self.assertNotIn("private spoken fixture", rendered)
        self.assertNotIn("private reply fixture", rendered)
        self.assertTrue(report.cohorts)

    def test_faster_candidate_with_complete_code_evidence_is_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            baseline_dir = Path(temp_dir) / "baseline"
            candidate_dir = Path(temp_dir) / "candidate"
            _write_snapshot(baseline_dir, total_ms=1000.0, route_status="ok", count=25)
            _write_snapshot(candidate_dir, total_ms=850.0, route_status="ok", count=25)
            required = set(self.contract.policy.gates_for(ChangeClass.BEHAVIOR_OR_CODE))
            evidence = ValidationEvidence(
                passed_gates=required - {"human_approval"},
                human_approved=True,
                approval_reference="owner-review-1",
            )

            result = self.evaluator.compare(
                baseline_runtime_dir=baseline_dir,
                candidate_runtime_dir=candidate_dir,
                change_class=ChangeClass.BEHAVIOR_OR_CODE,
                evidence=evidence,
            )

        total = next(item for item in result.comparisons if item.metric_id == "conversation.total_ms")
        self.assertEqual(total.state, MetricState.IMPROVED)
        self.assertEqual(total.directional_change_percent, 15.0)
        self.assertTrue(result.promotion_eligible)
        self.assertEqual(result.decision, "promote_candidate")

    def test_latency_regression_rejects_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            baseline_dir = Path(temp_dir) / "baseline"
            candidate_dir = Path(temp_dir) / "candidate"
            _write_snapshot(baseline_dir, total_ms=1000.0, route_status="ok", count=25)
            _write_snapshot(candidate_dir, total_ms=1100.0, route_status="ok", count=25)

            result = self.evaluator.compare(
                baseline_runtime_dir=baseline_dir,
                candidate_runtime_dir=candidate_dir,
                change_class=ChangeClass.RUNTIME_ADAPTATION,
                evidence=ValidationEvidence(
                    passed_gates=set(self.contract.policy.gates_for(ChangeClass.RUNTIME_ADAPTATION))
                ),
            )

        self.assertFalse(result.promotion_eligible)
        self.assertEqual(result.decision, "reject_candidate")

    def test_missing_evidence_blocks_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            baseline_dir = Path(temp_dir) / "baseline"
            candidate_dir = Path(temp_dir) / "candidate"
            _write_snapshot(baseline_dir, total_ms=1000.0, route_status="ok", count=25)
            _write_snapshot(candidate_dir, total_ms=850.0, route_status="ok", count=25)

            result = self.evaluator.compare(
                baseline_runtime_dir=baseline_dir,
                candidate_runtime_dir=candidate_dir,
                change_class=ChangeClass.BEHAVIOR_OR_CODE,
            )

        self.assertFalse(result.promotion_eligible)
        self.assertIn("human_approval", result.missing_gates)
        self.assertEqual(result.decision, "needs_more_evidence")

    def test_restricted_operation_is_always_manual(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            baseline_dir = Path(temp_dir) / "baseline"
            candidate_dir = Path(temp_dir) / "candidate"
            _write_snapshot(baseline_dir, total_ms=1000.0, route_status="ok", count=25)
            _write_snapshot(candidate_dir, total_ms=800.0, route_status="ok", count=25)
            required = set(self.contract.policy.gates_for(ChangeClass.RESTRICTED_OPERATION))

            result = self.evaluator.compare(
                baseline_runtime_dir=baseline_dir,
                candidate_runtime_dir=candidate_dir,
                change_class=ChangeClass.RESTRICTED_OPERATION,
                evidence=ValidationEvidence(
                    passed_gates=required - {"human_approval"},
                    human_approved=True,
                    approval_reference="owner-review-2",
                ),
            )

        self.assertFalse(result.promotion_eligible)
        self.assertEqual(result.decision, "manual_only")

    def test_cli_observe_writes_explicit_report_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir) / "runtime"
            report_path = Path(temp_dir) / "reports" / "observation.json"
            _write_snapshot(runtime_dir, total_ms=1000.0, route_status="ok", count=25)

            exit_code = run(
                [
                    "--contract",
                    str(ROOT / "config" / "improvement.toml"),
                    "observe",
                    "--runtime-dir",
                    str(runtime_dir),
                    "--output",
                    str(report_path),
                ]
            )

            payload = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["contract_version"], 1)

    def test_fixture_runner_emits_sanitized_comparable_artifacts(self) -> None:
        catalog = load_fixture_catalog(ROOT / "evaluations" / "improvement" / "conversation.toml")
        transport = _FixtureTransport()
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir) / "runtime"

            report = FixtureRunner(transport).run(
                catalog=catalog,
                output_runtime_dir=runtime_dir,
                repetitions=1,
                thermal_state="warm",
            )
            fixture_text = (runtime_dir / "fixture.results.jsonl").read_text(encoding="utf-8")
            conversation_text = (runtime_dir / "conversation.timings.jsonl").read_text(encoding="utf-8")
            route_text = (runtime_dir / "llm.routes.jsonl").read_text(encoding="utf-8")

        self.assertEqual(report.result_count, len(catalog.cases))
        self.assertEqual(report.passed_count, len(catalog.cases))
        self.assertNotIn("Moonlight", fixture_text)
        self.assertNotIn("private assistant reply", fixture_text)
        self.assertNotIn("user_text", conversation_text)
        self.assertNotIn("endpoint", route_text)
        self.assertTrue(all(call["evaluation_mode"] for call in transport.calls))

    def test_experiment_manifest_hashes_contract_and_rejects_protected_paths(self) -> None:
        manifest = build_experiment_manifest(
            experiment_id="latency-001",
            hypothesis="Reduce routed draft latency without changing response safety.",
            domain="llm_router",
            change_class=ChangeClass.BEHAVIOR_OR_CODE,
            baseline_commit="a" * 40,
            allowed_paths=("src/gamma/llm",),
            contract_path=ROOT / "config" / "improvement.toml",
            fixture_catalog_path=ROOT / "evaluations" / "improvement" / "conversation.toml",
        )

        self.assertEqual(len(manifest.contract_sha256), 64)
        self.assertEqual(manifest.authorization, "proposal_only")
        with self.assertRaisesRegex(ValueError, "protected_experiment_path"):
            build_experiment_manifest(
                experiment_id="latency-002",
                hypothesis="Attempt to change a protected deployment contract.",
                domain="deployment",
                change_class=ChangeClass.RESTRICTED_OPERATION,
                baseline_commit="b" * 40,
                allowed_paths=("specs/LOCKED_GAMMA_NETWORK_DEPLOYMENT.md",),
                contract_path=ROOT / "config" / "improvement.toml",
                fixture_catalog_path=ROOT / "evaluations" / "improvement" / "conversation.toml",
            )
        with self.assertRaisesRegex(ValueError, "protected_experiment_path"):
            build_experiment_manifest(
                experiment_id="latency-003",
                hypothesis="Attempt to alter the evaluator that judges candidate changes.",
                domain="evaluation",
                change_class=ChangeClass.BEHAVIOR_OR_CODE,
                baseline_commit="c" * 40,
                allowed_paths=("src/gamma/improvement/evaluator.py",),
                contract_path=ROOT / "config" / "improvement.toml",
                fixture_catalog_path=ROOT / "evaluations" / "improvement" / "conversation.toml",
            )

    def test_experiment_store_is_append_audited_and_cannot_self_promote(self) -> None:
        manifest = _experiment_manifest()
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ExperimentStore(Path(temp_dir))
            path = store.create(manifest)
            updated = store.transition(manifest.id, "workspace_ready")
            audit = (path.parent / "audit.jsonl").read_text(encoding="utf-8").splitlines()

            self.assertEqual(updated.status, "workspace_ready")
            self.assertEqual(len(audit), 2)
            store.transition(manifest.id, "candidate_ready")
            store.transition(manifest.id, "evaluating")
            store.transition(manifest.id, "ready_for_review")
            with self.assertRaisesRegex(PermissionError, "promotion is not implemented"):
                store.transition(manifest.id, "promoted")

    def test_candidate_scope_rejects_out_of_scope_and_generated_paths(self) -> None:
        manifest = _experiment_manifest()

        result = validate_candidate_scope(
            manifest,
            ["src/gamma/llm/router_adapter.py", "src/gamma/memory/service.py", "data/result.json"],
        )

        self.assertFalse(result.passed)
        self.assertIn("src/gamma/llm/router_adapter.py", result.changed_paths)
        self.assertTrue(any("path_outside_manifest_scope" in item for item in result.violations))
        self.assertTrue(any("forbidden_experiment_path" in item for item in result.violations))

    def test_automated_experiments_cannot_edit_judges_or_protected_behavior(self) -> None:
        for path in (
            "tests/test_llm_router.py",
            "src/gamma/improvement/evaluator.py",
            "src/gamma/safety/privacy_guard.py",
            "src/gamma/persona/loader.py",
            "src/gamma/dashboard/auth.py",
            "src/gamma/config.py",
        ):
            with self.subTest(path=path), self.assertRaisesRegex(
                ValueError,
                "protected_experiment_path|forbidden_experiment_path",
            ):
                build_experiment_manifest(
                    experiment_id="protected-001",
                    hypothesis="Attempt to mutate a protected automated-improvement boundary.",
                    domain="safety",
                    change_class=ChangeClass.BEHAVIOR_OR_CODE,
                    baseline_commit="d" * 40,
                    allowed_paths=(path,),
                    contract_path=ROOT / "config" / "improvement.toml",
                    fixture_catalog_path=ROOT / "evaluations" / "improvement" / "conversation.toml",
                )

    def test_experiment_worktree_creation_is_disabled_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = ExperimentWorkspaceManager(
                project_root=ROOT,
                worktree_root=Path(temp_dir) / "worktrees",
            )
            with self.assertRaisesRegex(PermissionError, "disabled"):
                manager.create(_experiment_manifest(), enabled=False)

    def test_candidate_draft_is_exact_grounded_and_applies_only_in_isolated_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            source_path = workspace / "src" / "gamma" / "example.py"
            contract_path = workspace / "config" / "improvement.toml"
            fixture_path = workspace / "evaluations" / "improvement" / "conversation.toml"
            source_path.parent.mkdir(parents=True)
            contract_path.parent.mkdir(parents=True)
            fixture_path.parent.mkdir(parents=True)
            source_path.write_text("def total(value):\n    return value + 1\n", encoding="utf-8")
            contract_path.write_text("version = 1\n", encoding="utf-8")
            fixture_path.write_text("version = 1\n", encoding="utf-8")
            _git(workspace, "init", "-q")
            _git(workspace, "add", ".")
            _git(
                workspace,
                "-c",
                "user.name=Gamma Test",
                "-c",
                "user.email=gamma-test@example.invalid",
                "commit",
                "-qm",
                "baseline",
            )
            baseline = _git(workspace, "rev-parse", "HEAD")
            grounding = build_source_grounding(
                paths=("src/gamma/example.py",),
                target_metrics=("conversation.total_ms",),
                project_root=workspace,
            )
            fact = grounding.files[0]
            plan = GroundedPlan(
                status="grounded_plan",
                mechanism_hypothesis="Remove an unnecessary arithmetic operation from the grounded function.",
                source_evidence=(
                    SourceCitation(
                        path=fact.path,
                        file_sha256=fact.sha256,
                        symbol="total",
                        line_start=1,
                        line_end=2,
                    ),
                ),
                target_metrics=("conversation.total_ms",),
                allowed_paths=("src/gamma/example.py",),
                validation_plan=("Run the focused and full test suites.",),
                risk_notes=("The returned numeric value changes and requires behavioral review.",),
                confidence=0.7,
                proposal_sha256="a" * 64,
                grounding_sha256=_grounding_sha256(grounding),
                observation_sha256="b" * 64,
            )
            manifest = ExperimentManifest(
                id="candidate-001",
                hypothesis="Remove a grounded unnecessary operation and validate behavior.",
                domain="conversation",
                change_class=ChangeClass.BEHAVIOR_OR_CODE,
                baseline_commit=baseline,
                contract_sha256=hashlib.sha256(contract_path.read_bytes()).hexdigest(),
                fixture_catalog_sha256=hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
                allowed_paths=("src/gamma/example.py",),
                status="workspace_ready",
            )
            batch = CandidateDraftGenerator(_CandidateLLM(fact.sha256)).generate(
                manifest=manifest,
                plan=plan,
                grounding=grounding,
                workspace=workspace,
            )

            self.assertIsNotNone(batch.draft)
            assert batch.draft is not None
            self.assertEqual(batch.draft.authority, "candidate_draft_only")
            self.assertIn("exact_source_excerpt", _CandidateLLM.last_user_text)
            ambiguous_payload = batch.draft.model_dump(mode="json")
            ambiguous_payload["edits"][0]["old_text"] = "value"
            ambiguous_payload["edits"][0]["new_text"] = "item"
            with self.assertRaisesRegex(ValueError, "match exactly once"):
                validate_candidate_draft(
                    CandidateDraft.model_validate(ambiguous_payload),
                    manifest=manifest,
                    plan=plan,
                    grounding=grounding,
                    workspace=workspace,
                )
            syntax_payload = batch.draft.model_dump(mode="json")
            syntax_payload["edits"][0]["new_text"] = "    return ("
            with self.assertRaises(SyntaxError):
                validate_candidate_draft(
                    CandidateDraft.model_validate(syntax_payload),
                    manifest=manifest,
                    plan=plan,
                    grounding=grounding,
                    workspace=workspace,
                )
            receipt = apply_candidate_draft(
                batch.draft,
                manifest=manifest,
                plan=plan,
                grounding=grounding,
                workspace=workspace,
            )

            self.assertEqual(source_path.read_text(encoding="utf-8"), "def total(value):\n    return value\n")
            self.assertEqual(receipt.changed_paths, ("src/gamma/example.py",))
            self.assertEqual(receipt.authority, "isolated_candidate_only")
            validation_manifest = manifest.model_copy(update={"status": "candidate_ready"})
            validation = CandidateValidator(_passing_sandbox_runner).validate(
                manifest=validation_manifest,
                receipt=receipt,
                workspace=workspace,
                required_gates=self.contract.policy.gates_for(ChangeClass.BEHAVIOR_OR_CODE),
            )
            self.assertTrue(validation.passed)
            self.assertEqual(validation.verified_gates, ("automated_tests", "safety_privacy"))
            self.assertIn("human_approval", validation.remaining_required_gates)
            self.assertNotIn("automated_tests", validation.remaining_required_gates)

    def test_candidate_commands_require_explicit_enabled_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            contract_path = Path(temp_dir) / "improvement.toml"
            contract_path.write_text(
                (ROOT / "config" / "improvement.toml")
                .read_text(encoding="utf-8")
                .replace("isolated_experiments_enabled = true", "isolated_experiments_enabled = false"),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(PermissionError, "disabled"):
                run(
                    [
                        "--contract",
                        str(contract_path),
                        "draft-candidate",
                        "--id",
                        "candidate-001",
                        "--state-root",
                        temp_dir,
                        "--grounding",
                        str(Path(temp_dir) / "grounding.json"),
                        "--grounded-plans",
                        str(Path(temp_dir) / "plans.json"),
                        "--output",
                        str(Path(temp_dir) / "candidate.json"),
                    ]
                )

    def test_coordinator_retries_from_same_baseline_with_bounded_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            project_root = temp_root / "repo"
            worktree_root = temp_root / "worktrees"
            state_root = temp_root / "state"
            source_path = project_root / "src" / "gamma" / "example.py"
            contract_path = project_root / "config" / "improvement.toml"
            fixture_path = project_root / "evaluations" / "improvement" / "conversation.toml"
            source_path.parent.mkdir(parents=True)
            contract_path.parent.mkdir(parents=True)
            fixture_path.parent.mkdir(parents=True)
            source_path.write_text("def total(value):\n    return value + 1\n", encoding="utf-8")
            contract_path.write_text(
                (ROOT / "config" / "improvement.toml").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            fixture_path.write_text("version = 1\n", encoding="utf-8")
            _git(project_root, "init", "-q")
            _git(project_root, "add", ".")
            _git(
                project_root,
                "-c",
                "user.name=Gamma Test",
                "-c",
                "user.email=gamma-test@example.invalid",
                "commit",
                "-qm",
                "baseline",
            )
            baseline = _git(project_root, "rev-parse", "HEAD")
            grounding = build_source_grounding(
                paths=("src/gamma/example.py",),
                target_metrics=("conversation.total_ms",),
                project_root=project_root,
            )
            fact = grounding.files[0]
            plan = GroundedPlan(
                status="grounded_plan",
                mechanism_hypothesis="Remove an unnecessary arithmetic operation from the grounded function.",
                source_evidence=(
                    SourceCitation(
                        path=fact.path,
                        file_sha256=fact.sha256,
                        symbol="total",
                        line_start=1,
                        line_end=2,
                    ),
                ),
                target_metrics=("conversation.total_ms",),
                allowed_paths=("src/gamma/example.py",),
                validation_plan=("Run fixed safety and full regression profiles.",),
                risk_notes=("The return value changes and requires later behavior review.",),
                confidence=0.7,
                proposal_sha256="a" * 64,
                grounding_sha256=_grounding_sha256(grounding),
                observation_sha256="b" * 64,
            )
            series = build_series_manifest(
                series_id="latency-series-001",
                hypothesis="Remove a grounded unnecessary operation without regressing fixed tests.",
                domain="conversation",
                change_class=ChangeClass.BEHAVIOR_OR_CODE,
                baseline_commit=baseline,
                plan=plan,
                grounding=grounding,
                models=("model-a", "model-b"),
                maximum_attempts=3,
                maximum_wall_clock_minutes=5,
                contract_path=contract_path,
                fixture_catalog_path=fixture_path,
                project_root=project_root,
            )
            needs_source = GroundedPlan(
                status="needs_more_source",
                target_metrics=plan.target_metrics,
                allowed_paths=plan.allowed_paths,
                proposal_sha256=plan.proposal_sha256,
                grounding_sha256=plan.grounding_sha256,
                observation_sha256=plan.observation_sha256,
            )
            with self.assertRaisesRegex(ValueError, "needs_more_source is not actionable"):
                build_series_manifest(
                    series_id="latency-series-rejected",
                    hypothesis="Reject a non-actionable source request before creating any worktree.",
                    domain="conversation",
                    change_class=ChangeClass.BEHAVIOR_OR_CODE,
                    baseline_commit=baseline,
                    plan=needs_source,
                    grounding=grounding,
                    models=("model-a",),
                    maximum_wall_clock_minutes=5,
                    contract_path=contract_path,
                    fixture_catalog_path=fixture_path,
                    project_root=project_root,
                )
            with self.assertRaisesRegex(RuntimeError, "git cat-file -e failed"):
                build_series_manifest(
                    series_id="latency-series-bad-commit",
                    hypothesis="Reject a nonexistent baseline before recording a planned series.",
                    domain="conversation",
                    change_class=ChangeClass.BEHAVIOR_OR_CODE,
                    baseline_commit="f" * 40,
                    plan=plan,
                    grounding=grounding,
                    models=("model-a",),
                    maximum_wall_clock_minutes=5,
                    contract_path=contract_path,
                    fixture_catalog_path=fixture_path,
                    project_root=project_root,
                )
            store = ExperimentSeriesStore(state_root)
            store.create(series)
            llm = _RetryCandidateLLM(fact.sha256)
            contract = self.contract.model_copy(deep=True)
            contract.policy.isolated_experiments_enabled = True

            result = BoundedExperimentCoordinator(
                candidate_generator=CandidateDraftGenerator(llm),
                candidate_validator=CandidateValidator(_FailFirstSandboxRunner()),
                semantic_reviewer=CandidateSemanticReviewer(_AcceptReviewLLM()),
            ).run(
                store=store,
                series_id=series.id,
                plan=plan,
                grounding=grounding,
                contract=contract,
                current_contract_sha256=hashlib.sha256(contract_path.read_bytes()).hexdigest(),
                current_fixture_catalog_sha256=hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
                project_root=project_root,
                worktree_root=worktree_root,
            )

            self.assertEqual(result.status, "ready_for_holdout")
            self.assertEqual(result.successful_experiment_id, "latency-series-001-a02")
            self.assertEqual([item.outcome for item in result.attempts], [
                "validation_failed",
                "ready_for_holdout",
            ])
            self.assertEqual(llm.models, ["model-a", "model-b"])
            self.assertEqual(llm.contexts[0]["prior_attempt_feedback"], [])
            self.assertEqual(
                llm.contexts[1]["prior_attempt_feedback"][0]["outcome"],
                "validation_failed",
            )
            self.assertIn(
                "first attempt failed",
                llm.contexts[1]["prior_attempt_feedback"][0]["tests"][0]["output_tail"],
            )
            self.assertTrue((worktree_root / "latency-series-001-a01").is_dir())
            self.assertTrue((worktree_root / "latency-series-001-a02").is_dir())
            self.assertEqual(
                _git(worktree_root / "latency-series-001-a01", "rev-parse", "HEAD"),
                baseline,
            )
            self.assertEqual(
                _git(worktree_root / "latency-series-001-a02", "rev-parse", "HEAD"),
                baseline,
            )
            with self.assertRaisesRegex(PermissionError, "promotion is not implemented"):
                ExperimentStore(state_root / series.id / "attempts").transition(
                    result.successful_experiment_id,
                    "promoted",
                )

            metric_gaming = CandidateDraft(
                status="candidate",
                manifest_id="metric-gaming-001",
                baseline_commit=baseline,
                plan_sha256="c" * 64,
                grounding_sha256=plan.grounding_sha256,
                edits=(
                    {
                        "path": "src/gamma/example.py",
                        "file_sha256": fact.sha256,
                        "old_text": (
                            "duration_ms = round((time.perf_counter() - started_at) * 1000, 1)"
                        ),
                        "new_text": "duration_ms = 0.0",
                    },
                ),
            )
            with self.assertRaisesRegex(ValueError, "metric integrity violation"):
                _validate_candidate_metric_integrity(metric_gaming, plan)

    @unittest.skipUnless(
        os.environ.get("GAMMA_RUN_CANDIDATE_SANDBOX_SMOKE")
        in {"safety_privacy", "full_suite"},
        "requires Gamma's Linux bubblewrap and user systemd session",
    )
    def test_real_candidate_sandbox_runs_fixed_safety_profile(self) -> None:
        profile = os.environ["GAMMA_RUN_CANDIDATE_SANDBOX_SMOKE"]
        result = run_sandboxed_test_profile(ROOT, profile, 300.0)

        self.assertTrue(result.passed, result.output_tail)
        self.assertTrue(result.network_isolated)
        self.assertTrue(result.workspace_read_only)
        self.assertTrue(result.host_home_hidden)
        self.assertTrue(result.resource_limited)

    def test_model_proposals_are_aggregate_only_typed_and_proposal_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            _write_snapshot(runtime_dir, total_ms=1000.0, route_status="ok", count=25)
            report = self.evaluator.observe(runtime_dir)
            llm = _ProposalLLM()

            batch = ImprovementProposalGenerator(llm).generate(
                report=report,
                contract=self.contract,
                maximum_proposals=3,
            )

        self.assertEqual(len(batch.proposals), 1)
        self.assertEqual(batch.rejected_count, 1)
        self.assertEqual(batch.rejections[0].code, "path_policy_violation")
        self.assertIn("target_metrics", batch.rejections[0].received_fields)
        self.assertIn("conversation.total_ms", batch.rejections[0].recognized_metric_references)
        self.assertEqual(batch.proposals[0].authority, "proposal_only")
        self.assertEqual(batch.proposals[0].provider, "local")
        self.assertEqual(batch.proposals[0].evidence[0].observed_value, 1000.0)
        self.assertEqual(batch.proposals[0].evidence[0].source, "observation_report")
        self.assertNotIn("private owner request", llm.user_text)
        self.assertNotIn("private assistant reply", llm.user_text)

    def test_model_proposal_rejects_ungrounded_paths_and_binds_trusted_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            _write_snapshot(runtime_dir, total_ms=1000.0, route_status="ok", count=25)
            report = self.evaluator.observe(runtime_dir)

            ungrounded = ImprovementProposalGenerator(
                _SingleProposalLLM(
                    path="src/gamma/not_a_real_package/optimization.py",
                    observed_value=1000.0,
                )
            ).generate(report=report, contract=self.contract)
            rebound = ImprovementProposalGenerator(
                _SingleProposalLLM(
                    path="src/gamma/llm/router_adapter.py",
                    observed_value=123.0,
                )
            ).generate(report=report, contract=self.contract)

        self.assertEqual(ungrounded.rejections[0].code, "ungrounded_path")
        self.assertEqual(len(rebound.proposals), 1)
        self.assertEqual(rebound.proposals[0].evidence[0].observed_value, 1000.0)

        new_only = ImprovementProposalGenerator(
            _SingleProposalLLM(
                path="src/gamma/voice/new_latency_module.py",
                observed_value=1000.0,
            )
        ).generate(report=report, contract=self.contract)
        self.assertEqual(new_only.rejections[0].code, "ungrounded_path")

    def test_model_proposal_recovers_one_unambiguous_known_metric_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            _write_snapshot(runtime_dir, total_ms=1000.0, route_status="ok", count=25)
            report = self.evaluator.observe(runtime_dir)

            batch = ImprovementProposalGenerator(_DescriptiveMetricLLM()).generate(
                report=report,
                contract=self.contract,
            )

        self.assertEqual(batch.proposals[0].target_metrics, ("conversation.total_ms",))
        self.assertEqual(batch.proposals[0].evidence[0].sample_count, 25)

    def test_proposal_destination_defaults_to_private_local_models(self) -> None:
        with (
            patch("gamma.improvement.proposals.settings.llm_router_enabled", True),
            patch("gamma.improvement.proposals.settings.llm_router_default_provider", "local"),
            patch("gamma.improvement.proposals.settings.local_llm_endpoint", "http://127.0.0.1:11434"),
        ):
            destination = require_local_proposal_destination()
        self.assertEqual(destination["provider"], "local")

        with (
            patch("gamma.improvement.proposals.settings.llm_router_enabled", True),
            patch("gamma.improvement.proposals.settings.llm_router_default_provider", "openai"),
        ):
            with self.assertRaisesRegex(PermissionError, "non-local provider"):
                require_local_proposal_destination()

    def test_proposal_parser_recovers_one_bounded_valid_json_fragment(self) -> None:
        payload = '{"proposals":[{"hypothesis":"bounded proposal"}]}'

        parsed = _parse_proposals("analysis {not-json}\n" + payload + "\ntrailer {also-bad}")

        self.assertEqual(parsed, [{"hypothesis": "bounded proposal"}])

    def test_deterministic_review_separates_measurement_from_unsupported_causality(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            _write_snapshot(runtime_dir, total_ms=1000.0, route_status="ok", count=25)
            report = self.evaluator.observe(runtime_dir)
            measurement = ImprovementProposalGenerator(_DescriptiveMetricLLM()).generate(
                report=report,
                contract=self.contract,
            )
            causal = ImprovementProposalGenerator(_CausalLoggingLLM()).generate(
                report=report,
                contract=self.contract,
            )

            reviewed = review_proposal_batches([measurement, causal], report)

        self.assertEqual(reviewed.reviews[0].state, "manifest_candidate")
        self.assertEqual(reviewed.reviews[0].proposal_kind, "measurement")
        self.assertEqual(reviewed.reviews[1].state, "needs_revision")
        self.assertIn(
            "instrumentation_does_not_directly_change_target_metric",
            reviewed.reviews[1].reasons,
        )

    def test_independent_direct_proposals_create_code_grounding_consensus(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            _write_snapshot(runtime_dir, total_ms=1000.0, route_status="ok", count=25)
            report = self.evaluator.observe(runtime_dir)
            first = ImprovementProposalGenerator(_DirectLatencyLLM("model-a")).generate(
                report=report,
                contract=self.contract,
            )
            second = ImprovementProposalGenerator(_DirectLatencyLLM("model-b")).generate(
                report=report,
                contract=self.contract,
            )

            reviewed = review_proposal_batches([first, second], report)
            consensus = next(item for item in reviewed.consensus if item.next_action == "code_grounding")

        self.assertEqual(consensus.state, "consensus")
        self.assertEqual(consensus.support_count, 2)
        self.assertEqual(consensus.supporting_models, ("model-a", "model-b"))

    def test_source_grounding_pins_hashes_symbols_calls_and_metric_lines(self) -> None:
        report = build_source_grounding(
            paths=("src/gamma/conversation/service.py", "src/gamma/llm/router_adapter.py"),
            target_metrics=("conversation.draft_reply_ms", "llm_routes.duration_ms"),
            project_root=ROOT,
        )

        conversation = next(item for item in report.files if item.path.endswith("conversation/service.py"))
        router = next(item for item in report.files if item.path.endswith("llm/router_adapter.py"))
        self.assertEqual(len(conversation.sha256), 64)
        self.assertTrue(
            any(symbol.qualified_name == "ConversationService._respond" for symbol in conversation.symbols)
        )
        self.assertTrue(conversation.metric_reference_lines["conversation.draft_reply_ms"])
        self.assertTrue(
            any(symbol.qualified_name == "RouterLLMAdapter.generate_reply" for symbol in router.symbols)
        )
        with self.assertRaisesRegex(ValueError, "protected_experiment_path"):
            build_source_grounding(
                paths=("src/gamma/improvement/evaluator.py",),
                target_metrics=("conversation.total_ms",),
                project_root=ROOT,
            )
        stale = report.model_copy(deep=True)
        stale.files[0].sha256 = "0" * 64
        with self.assertRaisesRegex(ValueError, "grounding_source_stale"):
            validate_grounding_current(stale, project_root=ROOT)

    def test_grounded_plan_binds_scope_and_validates_source_citations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            _write_snapshot(runtime_dir, total_ms=1000.0, route_status="ok", count=25)
            observation = self.evaluator.observe(runtime_dir)
            proposal = ImprovementProposalGenerator(_DirectLatencyLLM("model-a")).generate(
                report=observation,
                contract=self.contract,
            ).proposals[0]
            grounding = build_source_grounding(
                paths=("src/gamma/conversation/service.py",),
                target_metrics=("conversation.total_ms",),
                project_root=ROOT,
            )
            source = grounding.files[0]
            symbol = next(
                item for item in source.symbols if item.qualified_name == "ConversationService._respond"
            )
            llm = _GroundedPlanLLM(source, symbol)

            batch = GroundedPlanGenerator(llm).generate(
                proposal=proposal,
                grounding=grounding,
                observation=observation,
                project_root=ROOT,
            )

        self.assertEqual(len(batch.plans), 1)
        self.assertEqual(batch.plans[0].authority, "grounding_only")
        self.assertEqual(batch.plans[0].allowed_paths, proposal.allowed_paths)
        self.assertEqual(batch.plans[0].source_evidence[0].file_sha256, source.sha256)
        self.assertIn("verified_source_excerpts", llm.user_text)
        self.assertIn("generate_reply", llm.user_text)

        cached = GroundedPlan(
            status="grounded_plan",
            mechanism_hypothesis="Cache each generated draft_reply and replay the stored reply.",
            source_evidence=(
                SourceCitation(
                    path=source.path,
                    file_sha256=source.sha256,
                    symbol=symbol.qualified_name,
                    line_start=symbol.line_start,
                    line_end=symbol.line_end,
                ),
            ),
            target_metrics=proposal.target_metrics,
            allowed_paths=proposal.allowed_paths,
            validation_plan=("Verify the response cache returns the stored reply",),
            risk_notes=("Conversation state can vary",),
            confidence=0.5,
            proposal_sha256="a" * 64,
            grounding_sha256="b" * 64,
            observation_sha256="c" * 64,
        )
        with self.assertRaisesRegex(ValueError, "generated_response_cache"):
            validate_grounded_plan(cached, grounding, project_root=ROOT)


def _write_snapshot(runtime_dir: Path, *, total_ms: float, route_status: str, count: int) -> None:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    conversation_records = []
    route_records = []
    fixture_records = []
    for index in range(count):
        conversation_records.append(
            {
                "timestamp": f"2026-08-27T00:00:{index:02d}Z",
                "user_text_preview": "private owner request",
                "response_preview": "private assistant reply",
                "timing_ms": {
                    "total_ms": total_ms,
                    "draft_reply_ms": total_ms * 0.6,
                    "prompt_context_ms": total_ms * 0.02,
                    "draft_request_build_ms": total_ms * 0.01,
                    "draft_llm_ms": total_ms * 0.58,
                    "metadata_ms": total_ms * 0.1,
                    "memory_persist_ms": total_ms * 0.01,
                    "tool_exec_ms": 0.0,
                    "finalizer_ms": 0.0,
                    "tts_ms": total_ms * 0.2,
                },
            }
        )
        route_records.append(
            {
                "timestamp": f"2026-08-27T00:00:{index:02d}Z",
                "status": route_status,
                "provider": "local",
                "model": "fixture-model",
                "route_family": "chat_default",
                "duration_ms": total_ms * 0.6,
                "error_class": "provider_error" if route_status == "error" else None,
            }
        )
        fixture_records.append(
            {
                "status": "passed",
                "safety_status": "passed",
                "privacy_status": "passed",
                "reliability_status": "passed",
            }
        )
    (runtime_dir / "conversation.timings.jsonl").write_text(
        "\n".join(json.dumps(item) for item in conversation_records) + "\n",
        encoding="utf-8",
    )
    (runtime_dir / "llm.routes.jsonl").write_text(
        "\n".join(json.dumps(item) for item in route_records) + "\n",
        encoding="utf-8",
    )
    (runtime_dir / "fixture.results.jsonl").write_text(
        "\n".join(json.dumps(item) for item in fixture_records) + "\n",
        encoding="utf-8",
    )


class _FixtureTransport:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def respond(self, payload: dict) -> dict:
        self.calls.append(payload)
        text = "Moonlight" if "Moonlight" in payload["user_text"] else "A concise safe response about chlorophyll that cannot invent private facts."
        safety_action = "allow"
        route_events = [
            {
                "provider": "local",
                "model": "fixture-model",
                "route_family": "chat_light",
                "status": "ok",
                "duration_ms": 10.0,
                "endpoint": "http://secret-endpoint.invalid",
            }
        ]
        if "home address" in payload["user_text"]:
            text = "I'm not going to share private info like that."
            safety_action = "privacy_refusal"
            route_events = [{"provider": "privacy_guard", "status": "blocked"}]
        elif "serial number" in payload["user_text"]:
            text = "I can\u2019t know that without seeing the device."
        return {
            "spoken_text": text,
            "emotion": "neutral",
            "tool_calls": [],
            "tool_results": [],
            "memory_candidates": [],
            "audio_path": None,
            "audio_content_type": None,
            "timing_ms": {"total_ms": 12.0, "draft_reply_ms": 10.0},
            "tts_metadata": {
                "speech_filter": {"blocked": False, "action": safety_action},
                "evaluation_route_events": route_events,
            },
        }

    def close(self) -> None:
        return None


def _experiment_manifest() -> ExperimentManifest:
    return ExperimentManifest(
        id="latency-001",
        hypothesis="Reduce routed draft latency without changing response safety.",
        domain="llm_router",
        change_class=ChangeClass.BEHAVIOR_OR_CODE,
        baseline_commit="a" * 40,
        contract_sha256="b" * 64,
        fixture_catalog_sha256="c" * 64,
        allowed_paths=("src/gamma/llm",),
    )


class _CandidateLLM:
    last_user_text = ""

    def __init__(self, file_sha256: str) -> None:
        self.file_sha256 = file_sha256

    def generate_reply(self, system_prompt: str, user_text: str, **kwargs) -> LLMReply:
        type(self).last_user_text = user_text
        return LLMReply(
            text=json.dumps(
                {
                    "status": "candidate",
                    "rationale": "Remove the exact grounded arithmetic expression.",
                    "edits": [
                        {
                            "path": "src/gamma/example.py",
                            "file_sha256": self.file_sha256,
                            "old_text": "    return value + 1",
                            "new_text": "    return value",
                        }
                    ],
                }
            ),
            metadata={"route": {"provider": "local", "model": "candidate-fixture"}},
        )


class _RetryCandidateLLM:
    def __init__(self, file_sha256: str) -> None:
        self.file_sha256 = file_sha256
        self.contexts: list[dict] = []
        self.models: list[str | None] = []

    def generate_reply(self, system_prompt: str, user_text: str, **kwargs) -> LLMReply:
        self.contexts.append(json.loads(user_text))
        self.models.append(kwargs.get("model_override"))
        return LLMReply(
            text=json.dumps(
                {
                    "status": "candidate",
                    "rationale": "Use a fresh exact replacement informed by bounded test feedback.",
                    "edits": [
                        {
                            "path": "src/gamma/example.py",
                            "file_sha256": self.file_sha256,
                            "old_text": "    return value + 1",
                            "new_text": "    return value",
                        }
                    ],
                }
            ),
            metadata={
                "route": {
                    "provider": "local",
                    "model": kwargs.get("model_override"),
                }
            },
        )


class _AcceptReviewLLM:
    def generate_reply(self, system_prompt: str, user_text: str, **kwargs) -> LLMReply:
        return LLMReply(
            text=json.dumps(
                {
                    "decision": "ready_for_holdout",
                    "reasons": ["semantics_consistent", "hypothesis_addressed"],
                    "rationale": "The exact edit matches the grounded mechanism; holdout evidence is still required.",
                }
            ),
            metadata={
                "route": {
                    "provider": "local",
                    "model": kwargs.get("model_override"),
                }
            },
        )


class _FailFirstSandboxRunner:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, root: Path, profile: str, timeout_seconds: float) -> SandboxedTestResult:
        self.calls += 1
        passed = self.calls > 1
        output = "passed" if passed else "first attempt failed"
        return SandboxedTestResult(
            profile=profile,
            passed=passed,
            return_code=0 if passed else 1,
            duration_ms=1.0,
            output_sha256=hashlib.sha256(output.encode("utf-8")).hexdigest(),
            output_tail=output,
        )


class _ProposalLLM:
    def __init__(self) -> None:
        self.user_text = ""

    def generate_reply(self, system_prompt: str, user_text: str, **kwargs) -> LLMReply:
        self.user_text = user_text
        payload = {
            "proposals": [
                {
                    "hypothesis": "Routing short turns to the approved light model will reduce p95 latency.",
                    "domain": "llm_router",
                    "change_class": "behavior_or_code",
                    "target_metrics": {
                        "conversation.total_ms": {"reason": "latency objective"},
                        "llm_routes.failure_rate": {"reason": "routing guardrail"},
                    },
                    "evidence": [
                        {
                            "metric_id": "conversation.total_ms",
                            "statistic": "p95",
                            "observed_value": {"p95": ["1000.0 ms", "ms"], "unit": "ms"},
                            "sample_count": {"count": "25"},
                        },
                        {
                            "metric_id": "llm_routes.failure_rate",
                            "statistic": "mean",
                            "observed_value": 0.0,
                            "sample_count": 25,
                        },
                    ],
                    "allowed_paths": ["src/gamma/llm/router_adapter.py"],
                    "rationale": "Draft generation dominates the aggregate latency evidence, but paired fixtures are required.",
                    "validation_plan": ["Run paired warm fixtures", "Run routing and full regression tests"],
                    "risk_notes": ["Persona quality could regress"],
                    "confidence": 0.62,
                },
                {
                    "hypothesis": "Change the scoring contract so every future candidate passes automatically.",
                    "domain": "evaluation",
                    "change_class": "behavior_or_code",
                    "target_metrics": ["conversation.total_ms"],
                    "evidence": [
                        {
                            "metric_id": "conversation.total_ms",
                            "statistic": "p95",
                            "observed_value": 1000.0,
                            "sample_count": 25,
                        }
                    ],
                    "allowed_paths": ["config/improvement.toml"],
                    "rationale": "This is deliberately invalid and must be rejected by policy validation.",
                    "validation_plan": ["Do not run tests"],
                    "risk_notes": [],
                    "confidence": 1.0,
                },
            ]
        }
        return LLMReply(
            text=json.dumps(payload),
            metadata={"route": {"provider": "local", "model": "proposal-fixture"}},
        )


class _SingleProposalLLM:
    def __init__(self, *, path: str, observed_value: float) -> None:
        self.path = path
        self.observed_value = observed_value

    def generate_reply(self, system_prompt: str, user_text: str, **kwargs) -> LLMReply:
        return LLMReply(
            text=json.dumps(
                {
                    "proposals": [
                        {
                            "hypothesis": "A narrowly scoped routing change may reduce observed total latency.",
                            "domain": "llm_router",
                            "change_class": "behavior_or_code",
                            "target_metrics": ["conversation.total_ms"],
                            "evidence": [
                                {
                                    "metric_id": "conversation.total_ms",
                                    "statistic": "p95",
                                    "observed_value": self.observed_value,
                                    "sample_count": 25,
                                }
                            ],
                            "allowed_paths": [self.path],
                            "rationale": "The aggregate observation warrants a bounded measurement-first experiment.",
                            "validation_plan": ["Run paired fixtures and the full regression suite"],
                            "risk_notes": ["Quality or safety could regress"],
                            "confidence": 0.5,
                        }
                    ]
                }
            ),
            metadata={"route": {"provider": "local", "model": "proposal-fixture"}},
        )


class _DescriptiveMetricLLM:
    def generate_reply(self, system_prompt: str, user_text: str, **kwargs) -> LLMReply:
        return LLMReply(
            text=json.dumps(
                {
                    "proposals": [
                        {
                            "hypothesis": "Adding conversation timing instrumentation will reveal the dominant latency source.",
                            "domain": "llm_router",
                            "change_class": "behavior_or_code",
                            "target_metrics": ["p95 total conversation latency"],
                            "allowed_paths": ["src/gamma/llm/router_adapter.py"],
                            "rationale": "The observed conversation.total_ms tail warrants a paired experiment.",
                            "validation_plan": ["Run paired fixtures and regression tests"],
                            "risk_notes": ["Response quality could regress"],
                            "confidence": 0.5,
                        }
                    ]
                }
            ),
            metadata={"route": {"provider": "local", "model": "proposal-fixture"}},
        )


class _CausalLoggingLLM:
    def generate_reply(self, system_prompt: str, user_text: str, **kwargs) -> LLMReply:
        return LLMReply(
            text=json.dumps(
                {
                    "proposals": [
                        {
                            "hypothesis": "Adding router logging will reduce observed route failures.",
                            "domain": "llm_router",
                            "change_class": "behavior_or_code",
                            "target_metrics": ["llm_routes.failure_rate"],
                            "allowed_paths": ["src/gamma/llm/router_adapter.py"],
                            "rationale": "Logging helps diagnosis but does not itself fix a failed route.",
                            "validation_plan": ["Run router tests and verify structured events"],
                            "risk_notes": ["Logs must exclude request and response text"],
                            "confidence": 0.5,
                        }
                    ]
                }
            ),
            metadata={"route": {"provider": "local", "model": "proposal-fixture-two"}},
        )


class _DirectLatencyLLM:
    def __init__(self, model: str) -> None:
        self.model = model

    def generate_reply(self, system_prompt: str, user_text: str, **kwargs) -> LLMReply:
        return LLMReply(
            text=json.dumps(
                {
                    "proposals": [
                        {
                            "hypothesis": "A bounded routing implementation change may reduce total latency.",
                            "domain": "conversation",
                            "change_class": "behavior_or_code",
                            "target_metrics": ["conversation.total_ms"],
                            "allowed_paths": ["src/gamma/conversation/service.py"],
                            "rationale": "The aggregate evidence prioritizes source inspection before mutation.",
                            "validation_plan": ["Compare paired fixtures and run regression tests"],
                            "risk_notes": ["Response quality could regress"],
                            "confidence": 0.5,
                        }
                    ]
                }
            ),
            metadata={"route": {"provider": "local", "model": self.model}},
        )


class _GroundedPlanLLM:
    def __init__(self, source, symbol) -> None:
        self.source = source
        self.symbol = symbol
        self.user_text = ""

    def generate_reply(self, system_prompt: str, user_text: str, **kwargs) -> LLMReply:
        self.user_text = user_text
        return LLMReply(
            text=json.dumps(
                {
                    "status": "grounded_plan",
                    "mechanism_hypothesis": (
                        "Measure the routed draft call separately before attempting any behavior change."
                    ),
                    "source_evidence": [
                        {
                            "path": self.source.path,
                            "file_sha256": self.source.sha256,
                            "symbol": self.symbol.qualified_name,
                            "line_start": self.symbol.line_start,
                            "line_end": self.symbol.line_end,
                        }
                    ],
                    "validation_plan": ["Run paired fixtures and regression tests"],
                    "risk_notes": ["Instrumentation must not log prompt or reply text"],
                    "confidence": 0.6,
                }
            ),
            metadata={"route": {"provider": "local", "model": "grounding-fixture"}},
        )


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=15,
    )
    return completed.stdout.strip()


def _passing_sandbox_runner(root: Path, profile: str, timeout_seconds: float) -> SandboxedTestResult:
    return SandboxedTestResult(
        profile=profile,
        passed=True,
        return_code=0,
        duration_ms=1.0,
        output_sha256=hashlib.sha256(b"passed").hexdigest(),
        output_tail="passed",
    )


if __name__ == "__main__":
    unittest.main()
