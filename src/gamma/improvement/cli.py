from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .contract import load_improvement_contract
from .evaluator import ImprovementEvaluator
from .models import ChangeClass, ValidationEvidence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Observe and evaluate Gamma improvement candidates.")
    parser.add_argument("--contract", type=Path, default=None, help="Improvement contract TOML path.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    observe = subparsers.add_parser("observe", help="Create an aggregate, read-only runtime observation.")
    observe.add_argument("--runtime-dir", type=Path, required=True)
    observe.add_argument("--output", type=Path)

    compare = subparsers.add_parser("compare", help="Compare isolated baseline and candidate runtime snapshots.")
    compare.add_argument("--baseline-runtime-dir", type=Path, required=True)
    compare.add_argument("--candidate-runtime-dir", type=Path, required=True)
    compare.add_argument("--change-class", choices=[item.value for item in ChangeClass], required=True)
    compare.add_argument("--evidence", type=Path)
    compare.add_argument("--output", type=Path)
    compare.add_argument("--fail-if-blocked", action="store_true")

    fixtures = subparsers.add_parser("run-fixtures", help="Run fictional cases through Shana evaluation mode.")
    fixtures.add_argument("--catalog", type=Path)
    fixtures.add_argument("--output-runtime-dir", type=Path, required=True)
    fixtures.add_argument("--base-url")
    fixtures.add_argument("--timeout-seconds", type=float, default=120.0)
    fixtures.add_argument("--repetitions", type=int, default=1)
    fixtures.add_argument("--thermal-state", choices=["cold", "warm", "unknown"], default="unknown")

    plan_experiment = subparsers.add_parser("plan-experiment", help="Create a proposal-only experiment manifest.")
    plan_experiment.add_argument("--id", required=True)
    plan_experiment.add_argument("--hypothesis", required=True)
    plan_experiment.add_argument("--domain", required=True)
    plan_experiment.add_argument("--change-class", choices=[item.value for item in ChangeClass], required=True)
    plan_experiment.add_argument("--baseline-commit", required=True)
    plan_experiment.add_argument("--allow-path", action="append", required=True)
    plan_experiment.add_argument("--state-root", type=Path, required=True)

    prepare_experiment = subparsers.add_parser("prepare-experiment", help="Create a disabled-by-default detached worktree.")
    prepare_experiment.add_argument("--id", required=True)
    prepare_experiment.add_argument("--state-root", type=Path, required=True)

    validate_scope = subparsers.add_parser("validate-scope", help="Validate candidate changed paths against a manifest.")
    validate_scope.add_argument("--manifest", type=Path, required=True)
    validate_scope.add_argument("--changed-path", action="append", required=True)

    propose = subparsers.add_parser("propose", help="Ask one or more models for proposal-only hypotheses.")
    propose.add_argument("--runtime-dir", type=Path, required=True)
    propose.add_argument("--output", type=Path, required=True)
    propose.add_argument("--model", action="append")
    propose.add_argument("--maximum-proposals", type=int, default=3)
    propose.add_argument("--allow-hosted", action="store_true")

    review = subparsers.add_parser(
        "review-proposals",
        help="Deterministically screen proposal batches and calculate independent-model consensus.",
    )
    review.add_argument("--runtime-dir", type=Path, required=True)
    review.add_argument("--proposals", type=Path, required=True)
    review.add_argument("--output", type=Path, required=True)

    grounding = subparsers.add_parser(
        "ground-source",
        help="Create a read-only source hash, symbol, call, and metric-reference artifact.",
    )
    grounding.add_argument("--path", action="append", required=True)
    grounding.add_argument("--target-metric", action="append", required=True)
    grounding.add_argument("--output", type=Path, required=True)

    grounded_plan = subparsers.add_parser(
        "ground-plan",
        help="Ask local models for source-cited proposal-only plans against pinned grounding.",
    )
    grounded_plan.add_argument("--runtime-dir", type=Path, required=True)
    grounded_plan.add_argument("--proposals", type=Path, required=True)
    grounded_plan.add_argument("--proposal-hash", required=True)
    grounded_plan.add_argument("--grounding", type=Path, required=True)
    grounded_plan.add_argument("--output", type=Path, required=True)
    grounded_plan.add_argument("--model", action="append")
    grounded_plan.add_argument("--allow-hosted", action="store_true")

    draft_candidate = subparsers.add_parser(
        "draft-candidate",
        help="Ask local models for bounded candidate edits in an enabled detached experiment.",
    )
    draft_candidate.add_argument("--id", required=True)
    draft_candidate.add_argument("--state-root", type=Path, required=True)
    draft_candidate.add_argument("--grounding", type=Path, required=True)
    draft_candidate.add_argument("--grounded-plans", type=Path, required=True)
    draft_candidate.add_argument("--plan-index", type=int, default=0)
    draft_candidate.add_argument("--output", type=Path, required=True)
    draft_candidate.add_argument("--model", action="append")
    draft_candidate.add_argument("--allow-hosted", action="store_true")

    apply_candidate = subparsers.add_parser(
        "apply-candidate",
        help="Apply one validated candidate draft only inside its enabled detached experiment.",
    )
    apply_candidate.add_argument("--id", required=True)
    apply_candidate.add_argument("--state-root", type=Path, required=True)
    apply_candidate.add_argument("--grounding", type=Path, required=True)
    apply_candidate.add_argument("--grounded-plans", type=Path, required=True)
    apply_candidate.add_argument("--plan-index", type=int, default=0)
    apply_candidate.add_argument("--candidates", type=Path, required=True)
    apply_candidate.add_argument("--candidate-index", type=int, default=0)
    apply_candidate.add_argument("--receipt", type=Path, required=True)

    validate_candidate = subparsers.add_parser(
        "validate-candidate",
        help="Run fixed static and sandboxed regression checks for an isolated candidate.",
    )
    validate_candidate.add_argument("--id", required=True)
    validate_candidate.add_argument("--state-root", type=Path, required=True)
    validate_candidate.add_argument("--receipt", type=Path, required=True)
    validate_candidate.add_argument("--output", type=Path, required=True)

    plan_series = subparsers.add_parser(
        "plan-series",
        help="Create a pinned bounded multi-attempt experiment series.",
    )
    plan_series.add_argument("--id", required=True)
    plan_series.add_argument("--hypothesis", required=True)
    plan_series.add_argument("--domain", required=True)
    plan_series.add_argument("--change-class", choices=[item.value for item in ChangeClass], required=True)
    plan_series.add_argument("--baseline-commit", required=True)
    plan_series.add_argument("--grounding", type=Path, required=True)
    plan_series.add_argument("--grounded-plans", type=Path, required=True)
    plan_series.add_argument("--plan-index", type=int, default=0)
    plan_series.add_argument("--model", action="append", required=True)
    plan_series.add_argument("--maximum-changed-files", type=int, default=12)
    plan_series.add_argument("--maximum-attempts", type=int, default=3)
    plan_series.add_argument("--maximum-wall-clock-minutes", type=int, default=60)
    plan_series.add_argument("--state-root", type=Path, required=True)

    run_series = subparsers.add_parser(
        "run-series",
        help="Run bounded local-model attempts in fresh detached worktrees.",
    )
    run_series.add_argument("--id", required=True)
    run_series.add_argument("--state-root", type=Path, required=True)
    run_series.add_argument("--grounding", type=Path, required=True)
    run_series.add_argument("--grounded-plans", type=Path, required=True)
    run_series.add_argument("--plan-index", type=int, default=0)
    return parser


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run-fixtures":
        from .fixtures import FixtureRunner, HttpConversationTransport, load_fixture_catalog

        transport = HttpConversationTransport(base_url=args.base_url, timeout_seconds=args.timeout_seconds)
        try:
            report = FixtureRunner(transport).run(
                catalog=load_fixture_catalog(args.catalog),
                output_runtime_dir=args.output_runtime_dir,
                repetitions=args.repetitions,
                thermal_state=args.thermal_state,
            )
        finally:
            transport.close()
        sys.stdout.write(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n")
        return 0

    if args.command in {"plan-series", "run-series"}:
        from ..config import PROJECT_ROOT
        from .coordinator import (
            BoundedExperimentCoordinator,
            ExperimentSeriesStore,
            build_series_manifest,
        )
        from .experiments import _file_sha256
        from .grounded_plans import GroundedPlan
        from .grounding import SourceGroundingReport

        grounding_report = SourceGroundingReport.model_validate_json(
            args.grounding.read_text(encoding="utf-8")
        )
        plan = _load_indexed_model(
            args.grounded_plans,
            collection="plans",
            index=args.plan_index,
            model_type=GroundedPlan,
        )
        series_store = ExperimentSeriesStore(args.state_root)
        if args.command == "plan-series":
            manifest = build_series_manifest(
                series_id=args.id,
                hypothesis=args.hypothesis,
                domain=args.domain,
                change_class=ChangeClass(args.change_class),
                baseline_commit=args.baseline_commit,
                plan=plan,
                grounding=grounding_report,
                models=tuple(args.model),
                maximum_changed_files=args.maximum_changed_files,
                maximum_attempts=args.maximum_attempts,
                maximum_wall_clock_minutes=args.maximum_wall_clock_minutes,
                contract_path=args.contract,
            )
            path = series_store.create(manifest)
            sys.stdout.write(
                json.dumps(
                    {
                        "manifest": str(path),
                        "status": manifest.status,
                        "maximum_attempts": manifest.maximum_attempts,
                        "maximum_wall_clock_minutes": manifest.maximum_wall_clock_minutes,
                    },
                    indent=2,
                )
                + "\n"
            )
            return 0

        from ..llm.factory import build_llm_adapter
        from .candidates import CandidateDraftGenerator
        from .proposals import require_local_proposal_destination
        from .semantic_review import CandidateSemanticReviewer
        from .validation import CandidateValidator

        require_local_proposal_destination()
        contract_path = args.contract or PROJECT_ROOT / "config" / "improvement.toml"
        fixture_path = PROJECT_ROOT / "evaluations" / "improvement" / "conversation.toml"
        contract = load_improvement_contract(contract_path)
        configured_root = Path(contract.policy.experiment_worktree_root)
        if not configured_root.is_absolute():
            configured_root = PROJECT_ROOT / configured_root
        improvement_llm = build_llm_adapter()
        result = BoundedExperimentCoordinator(
            candidate_generator=CandidateDraftGenerator(improvement_llm),
            candidate_validator=CandidateValidator(),
            semantic_reviewer=CandidateSemanticReviewer(improvement_llm),
        ).run(
            store=series_store,
            series_id=args.id,
            plan=plan,
            grounding=grounding_report,
            contract=contract,
            current_contract_sha256=_file_sha256(contract_path),
            current_fixture_catalog_sha256=_file_sha256(fixture_path),
            project_root=PROJECT_ROOT,
            worktree_root=configured_root,
        )
        sys.stdout.write(
            json.dumps(
                {
                    "status": result.status,
                    "attempts": len(result.attempts),
                    "successful_experiment_id": result.successful_experiment_id,
                    "terminal_reason": result.terminal_reason,
                    "promotion_authority": False,
                },
                indent=2,
            )
            + "\n"
        )
        return 0 if result.status in {"fixed_tests_passed", "ready_for_holdout"} else 2

    if args.command in {
        "plan-experiment",
        "prepare-experiment",
        "validate-scope",
        "draft-candidate",
        "apply-candidate",
        "validate-candidate",
    }:
        from .experiments import (
            ExperimentManifest,
            ExperimentStore,
            ExperimentWorkspaceManager,
            build_experiment_manifest,
            validate_candidate_scope,
        )

        if args.command == "plan-experiment":
            manifest = build_experiment_manifest(
                experiment_id=args.id,
                hypothesis=args.hypothesis,
                domain=args.domain,
                change_class=ChangeClass(args.change_class),
                baseline_commit=args.baseline_commit,
                allowed_paths=tuple(args.allow_path),
                contract_path=args.contract,
            )
            path = ExperimentStore(args.state_root).create(manifest)
            sys.stdout.write(json.dumps({"manifest": str(path), "status": manifest.status}, indent=2) + "\n")
            return 0
        if args.command == "prepare-experiment":
            contract = load_improvement_contract(args.contract)
            store = ExperimentStore(args.state_root)
            manifest = store.read(args.id)
            configured_root = Path(contract.policy.experiment_worktree_root)
            if not configured_root.is_absolute():
                from ..config import PROJECT_ROOT

                configured_root = PROJECT_ROOT / configured_root
            path = ExperimentWorkspaceManager(worktree_root=configured_root).create(
                manifest,
                enabled=contract.policy.isolated_experiments_enabled,
            )
            store.transition(args.id, "workspace_ready")
            sys.stdout.write(json.dumps({"worktree": str(path), "status": "workspace_ready"}, indent=2) + "\n")
            return 0
        if args.command == "validate-candidate":
            from .candidates import CandidateApplicationReceipt
            from .validation import CandidateValidator

            if args.output.exists():
                raise FileExistsError(f"candidate validation output already exists: {args.output}")
            contract = load_improvement_contract(args.contract)
            if not contract.policy.isolated_experiments_enabled:
                raise PermissionError("isolated candidate work is disabled by the improvement contract")
            store = ExperimentStore(args.state_root)
            manifest = store.read(args.id)
            configured_root = Path(contract.policy.experiment_worktree_root)
            if not configured_root.is_absolute():
                from ..config import PROJECT_ROOT

                configured_root = PROJECT_ROOT / configured_root
            workspace = (configured_root.resolve() / manifest.id).resolve()
            if workspace.parent != configured_root.resolve():
                raise ValueError("experiment workspace escaped its configured root")
            receipt = CandidateApplicationReceipt.model_validate_json(
                args.receipt.read_text(encoding="utf-8")
            )
            report = CandidateValidator().validate(
                manifest=manifest,
                receipt=receipt,
                workspace=workspace,
                required_gates=contract.policy.gates_for(manifest.change_class),
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            sys.stdout.write(
                json.dumps(
                    {
                        "output": str(args.output),
                        "passed": report.passed,
                        "remaining_required_gates": list(report.remaining_required_gates),
                    },
                    indent=2,
                )
                + "\n"
            )
            return 0 if report.passed else 2
        if args.command in {"draft-candidate", "apply-candidate"}:
            from .candidates import (
                CandidateDraft,
                CandidateDraftGenerator,
                apply_candidate_draft,
            )
            from .grounded_plans import GroundedPlan
            from .grounding import SourceGroundingReport

            contract = load_improvement_contract(args.contract)
            if not contract.policy.isolated_experiments_enabled:
                raise PermissionError("isolated candidate work is disabled by the improvement contract")
            store = ExperimentStore(args.state_root)
            manifest = store.read(args.id)
            configured_root = Path(contract.policy.experiment_worktree_root)
            if not configured_root.is_absolute():
                from ..config import PROJECT_ROOT

                configured_root = PROJECT_ROOT / configured_root
            workspace = (configured_root.resolve() / manifest.id).resolve()
            if workspace.parent != configured_root.resolve():
                raise ValueError("experiment workspace escaped its configured root")
            grounding_report = SourceGroundingReport.model_validate_json(
                args.grounding.read_text(encoding="utf-8")
            )
            plan = _load_indexed_model(
                args.grounded_plans,
                collection="plans",
                index=args.plan_index,
                model_type=GroundedPlan,
            )
            if args.command == "draft-candidate":
                from ..llm.factory import build_llm_adapter
                from .proposals import require_local_proposal_destination

                if args.output.exists():
                    raise FileExistsError(f"candidate output already exists: {args.output}")
                if not args.allow_hosted:
                    require_local_proposal_destination()
                models = args.model or [None]
                if len(models) > 3:
                    raise ValueError("at most three candidate models may be requested")
                generator = CandidateDraftGenerator(build_llm_adapter())
                batches = [
                    generator.generate(
                        manifest=manifest,
                        plan=plan,
                        grounding=grounding_report,
                        workspace=workspace,
                        model_override=model,
                    )
                    for model in models
                ]
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(
                    json.dumps(
                        {
                            "authority": "candidate_draft_only",
                            "batches": [batch.model_dump(mode="json") for batch in batches],
                        },
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                accepted = sum(
                    batch.draft is not None and batch.draft.status == "candidate"
                    for batch in batches
                )
                sys.stdout.write(
                    json.dumps({"output": str(args.output), "candidate_drafts": accepted}, indent=2)
                    + "\n"
                )
                return 0 if accepted else 2

            if args.receipt.exists():
                raise FileExistsError(f"candidate receipt already exists: {args.receipt}")
            draft = _load_indexed_model(
                args.candidates,
                collection="draft",
                index=args.candidate_index,
                model_type=CandidateDraft,
            )
            receipt = apply_candidate_draft(
                draft,
                manifest=manifest,
                plan=plan,
                grounding=grounding_report,
                workspace=workspace,
            )
            store.transition(args.id, "candidate_ready")
            args.receipt.parent.mkdir(parents=True, exist_ok=True)
            args.receipt.write_text(
                json.dumps(receipt.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            sys.stdout.write(
                json.dumps(
                    {"receipt": str(args.receipt), "changed_paths": list(receipt.changed_paths)},
                    indent=2,
                )
                + "\n"
            )
            return 0
        manifest = ExperimentManifest.model_validate_json(args.manifest.read_text(encoding="utf-8"))
        result = validate_candidate_scope(manifest, args.changed_path)
        sys.stdout.write(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True) + "\n")
        return 0 if result.passed else 2

    evaluator = ImprovementEvaluator(load_improvement_contract(args.contract))
    if args.command == "ground-plan":
        import hashlib

        from ..llm.factory import build_llm_adapter
        from .grounded_plans import GroundedPlanGenerator
        from .grounding import SourceGroundingReport
        from .proposals import ImprovementProposal, require_local_proposal_destination

        if args.output.exists():
            raise FileExistsError(f"grounded plan output already exists: {args.output}")
        if not args.allow_hosted:
            require_local_proposal_destination()
        payload = json.loads(args.proposals.read_text(encoding="utf-8"))
        raw_batches = payload.get("batches") if isinstance(payload, dict) else None
        if not isinstance(raw_batches, list):
            raise ValueError("proposal artifact requires a batches array")
        proposals = [
            ImprovementProposal.model_validate(item)
            for batch in raw_batches
            if isinstance(batch, dict) and isinstance(batch.get("proposals"), list)
            for item in batch["proposals"]
        ]
        proposal = next(
            (
                item
                for item in proposals
                if hashlib.sha256(item.hypothesis.encode("utf-8")).hexdigest()
                == args.proposal_hash
            ),
            None,
        )
        if proposal is None:
            raise ValueError("proposal hash was not found in the proposal artifact")
        grounding = SourceGroundingReport.model_validate_json(
            args.grounding.read_text(encoding="utf-8")
        )
        observation = evaluator.observe(args.runtime_dir)
        generator = GroundedPlanGenerator(build_llm_adapter())
        models = args.model or [None]
        if len(models) > 3:
            raise ValueError("at most three grounding models may be requested")
        batches = [
            generator.generate(
                proposal=proposal,
                grounding=grounding,
                observation=observation,
                model_override=model,
            )
            for model in models
        ]
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {"authority": "grounding_only", "batches": [item.model_dump(mode="json") for item in batches]},
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        accepted = sum(len(batch.plans) for batch in batches)
        sys.stdout.write(
            json.dumps({"output": str(args.output), "accepted_grounded_plans": accepted}, indent=2)
            + "\n"
        )
        return 0 if accepted else 2
    if args.command == "ground-source":
        from .grounding import build_source_grounding

        if args.output.exists():
            raise FileExistsError(f"grounding output already exists: {args.output}")
        known_metrics = {metric.id for metric in evaluator.contract.metrics}
        unknown_metrics = sorted(set(args.target_metric) - known_metrics)
        if unknown_metrics:
            raise ValueError("unknown target metrics: " + ", ".join(unknown_metrics))
        report = build_source_grounding(
            paths=tuple(args.path),
            target_metrics=tuple(args.target_metric),
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        sys.stdout.write(
            json.dumps(
                {
                    "output": str(args.output),
                    "grounded_files": len(report.files),
                    "unavailable_paths": len(report.unavailable_paths),
                },
                indent=2,
            )
            + "\n"
        )
        return 0
    if args.command == "review-proposals":
        from .proposals import ProposalBatch
        from .review import review_proposal_batches

        if args.output.exists():
            raise FileExistsError(f"review output already exists: {args.output}")
        payload = json.loads(args.proposals.read_text(encoding="utf-8"))
        raw_batches = payload.get("batches") if isinstance(payload, dict) else None
        if not isinstance(raw_batches, list):
            raise ValueError("proposal artifact requires a batches array")
        batches = [ProposalBatch.model_validate(item) for item in raw_batches]
        report = review_proposal_batches(batches, evaluator.observe(args.runtime_dir))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        sys.stdout.write(
            json.dumps(
                {
                    "output": str(args.output),
                    "manifest_candidates": report.manifest_candidate_count,
                    "consensus_candidates": sum(
                        item.state == "consensus" for item in report.consensus
                    ),
                },
                indent=2,
            )
            + "\n"
        )
        return 0
    if args.command == "propose":
        from ..llm.factory import build_llm_adapter
        from .proposals import ImprovementProposalGenerator, require_local_proposal_destination

        if args.output.exists():
            raise FileExistsError(f"proposal output already exists: {args.output}")
        if not args.allow_hosted:
            require_local_proposal_destination()
        report = evaluator.observe(args.runtime_dir)
        generator = ImprovementProposalGenerator(build_llm_adapter())
        models = args.model or [None]
        if len(models) > 3:
            raise ValueError("at most three proposal models may be requested")
        batches = [
            generator.generate(
                report=report,
                contract=evaluator.contract,
                maximum_proposals=args.maximum_proposals,
                model_override=model,
            )
            for model in models
        ]
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {"authority": "proposal_only", "batches": [batch.model_dump(mode="json") for batch in batches]},
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        accepted_count = sum(len(batch.proposals) for batch in batches)
        sys.stdout.write(json.dumps({"output": str(args.output), "accepted_proposals": accepted_count}, indent=2) + "\n")
        return 0 if accepted_count else 2
    if args.command == "observe":
        result = evaluator.observe(args.runtime_dir)
    else:
        evidence = _load_evidence(args.evidence)
        result = evaluator.compare(
            baseline_runtime_dir=args.baseline_runtime_dir,
            candidate_runtime_dir=args.candidate_runtime_dir,
            change_class=ChangeClass(args.change_class),
            evidence=evidence,
        )
    rendered = json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    if args.command == "compare" and args.fail_if_blocked and not result.promotion_eligible:
        return 2
    return 0


def _load_evidence(path: Path | None) -> ValidationEvidence:
    if path is None:
        return ValidationEvidence()
    return ValidationEvidence.model_validate_json(path.read_text(encoding="utf-8"))


def _load_indexed_model(path: Path, *, collection: str, index: int, model_type):
    if index < 0:
        raise ValueError("artifact index cannot be negative")
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_batches = payload.get("batches") if isinstance(payload, dict) else None
    if not isinstance(raw_batches, list):
        raise ValueError("artifact requires a batches array")
    values = []
    for batch in raw_batches:
        if not isinstance(batch, dict):
            continue
        raw = batch.get(collection)
        if collection == "draft":
            raw = [raw] if isinstance(raw, dict) else []
        if isinstance(raw, list):
            values.extend(item for item in raw if isinstance(item, dict))
    if index >= len(values):
        raise ValueError(f"artifact {collection} index is out of range")
    return model_type.model_validate(values[index])


if __name__ == "__main__":
    raise SystemExit(run())
