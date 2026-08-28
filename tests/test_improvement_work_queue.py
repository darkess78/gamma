from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gamma.improvement.work_queue import ImprovementWorkStore
from gamma.improvement.worker import AutonomousImprovementRunner


class ImprovementWorkStoreTest(unittest.TestCase):
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
