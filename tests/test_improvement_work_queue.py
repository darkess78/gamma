from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gamma.improvement.work_queue import ImprovementWorkStore
from gamma.improvement.worker import AutonomousImprovementRunner, _previous_proposal_feedback


class ImprovementWorkStoreTest(unittest.TestCase):
    def test_previous_cycle_feedback_is_bounded_deduplicated_and_restart_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            work_root = Path(temporary_directory)
            cycle_root = work_root / "cycle-01"
            cycle_root.mkdir()
            proposal = {
                "hypothesis": "A prior bounded routing change may reduce total latency.",
                "domain": "conversation",
            }
            (cycle_root / "proposals.json").write_text(
                json.dumps({"batches": [{"proposals": [proposal, proposal]}]}),
                encoding="utf-8",
            )

            feedback = _previous_proposal_feedback(work_root, cycle=2)

        self.assertEqual(len(feedback), 1)
        self.assertEqual(feedback[0]["hypothesis"], proposal["hypothesis"])
        self.assertEqual(feedback[0]["outcome"], "not_actionable_in_prior_cycle")

    def test_durable_request_supports_pause_resume_and_stop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = ImprovementWorkStore(Path(temporary_directory) / "control")
            request = store.create(
                goal="Improve the highest-priority measured Gamma bottleneck safely.",
                selection_mode="automatic",
                focus_domains=(),
                models=("model-a", "model-b", "model-c"),
                budget_minutes=480,
                maximum_cycles=3,
                maximum_attempts_per_series=6,
            )

            paused = store.control(request.id, "pause")
            resumed = store.control(request.id, "resume")
            stopped = store.control(request.id, "stop")

            self.assertEqual(paused.status, "paused")
            self.assertEqual(resumed.status, "queued")
            self.assertEqual(stopped.status, "stopped")
            self.assertIsNotNone(stopped.completed_at)
            self.assertFalse(stopped.public_summary()["promotion_authority"])
            self.assertEqual(store.list()[0].id, request.id)

    def test_request_rejects_single_model_and_excessive_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = ImprovementWorkStore(Path(temporary_directory) / "control")
            with self.assertRaisesRegex(ValueError, "two or three"):
                store.create(
                    goal="Improve measured latency with bounded isolated validation.",
                    selection_mode="directed",
                    focus_domains=("conversation",),
                    models=("model-a",),
                    budget_minutes=60,
                    maximum_cycles=1,
                    maximum_attempts_per_series=2,
                )
            with self.assertRaises(ValueError):
                store.create(
                    goal="Improve measured latency with bounded isolated validation.",
                    selection_mode="directed",
                    focus_domains=("conversation",),
                    models=("model-a", "model-b"),
                    budget_minutes=721,
                    maximum_cycles=1,
                    maximum_attempts_per_series=2,
                )

    def test_review_ready_candidate_can_be_rejected_without_losing_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = ImprovementWorkStore(Path(temporary_directory) / "control")
            request = store.create(
                goal="Improve the highest-priority measured Gamma bottleneck safely.",
                selection_mode="automatic",
                focus_domains=(),
                models=("model-a", "model-b"),
                budget_minutes=480,
                maximum_cycles=3,
                maximum_attempts_per_series=6,
            )

            def ready_for_review(item):
                item.status = "review_ready"
                item.stage = "review_ready"
                item.current_series_id = "review-series-001"
                item.completed_at = "2026-08-29T20:00:00Z"
                return item

            store.mutate(request.id, ready_for_review)
            rejected = store.control(request.id, "reject")

            self.assertEqual(rejected.status, "rejected")
            self.assertEqual(rejected.current_series_id, "review-series-001")
            self.assertIn("owner_rejected_candidate", rejected.reason_codes)
            self.assertIn("evidence was retained", rejected.result_summary)
            self.assertFalse(rejected.public_summary()["promotion_authority"])
            with self.assertRaisesRegex(ValueError, "only review-ready"):
                store.control(rejected.id, "reject")

    def test_runner_honors_safe_stop_before_model_work(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_root = Path(temporary_directory) / "improvement"
            runner = AutonomousImprovementRunner(project_root=Path.cwd(), data_root=data_root)
            request = runner.store.create(
                goal="Improve measured latency with bounded isolated validation.",
                selection_mode="directed",
                focus_domains=("conversation",),
                models=("model-a", "model-b"),
                budget_minutes=60,
                maximum_cycles=1,
                maximum_attempts_per_series=2,
            )
            runner.store.control(request.id, "stop")

            with (
                patch.object(runner, "_verify_live_checkout") as verify,
                patch("gamma.improvement.worker.require_local_proposal_destination") as destination,
            ):
                result = runner.run(request.id)

            self.assertEqual(result.status, "stopped")
            verify.assert_not_called()
            destination.assert_not_called()


if __name__ == "__main__":
    unittest.main()
