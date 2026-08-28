from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from gamma.config import settings
from gamma.dashboard.improvement_status import ImprovementStatusReader


class ImprovementStatusReaderTest(unittest.TestCase):
    def test_builds_sanitized_running_status_from_nested_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_root = Path(temporary_directory) / "improvement"
            series_directory = data_root / "validation-evidence" / "run-001" / "series" / "latency-series-001"
            series_directory.mkdir(parents=True)
            (series_directory / "manifest.json").write_text(
                json.dumps(_series_manifest(status="running", include_attempt=True)),
                encoding="utf-8",
            )
            (series_directory / "run.lock").write_text("active\n", encoding="utf-8")
            observation_directory = data_root / "validation-evidence" / "run-001" / "fixtures"
            observation_directory.mkdir(parents=True)
            (observation_directory / "post-restart.observation.json").write_text(
                json.dumps(_observation()),
                encoding="utf-8",
            )

            payload = ImprovementStatusReader(
                project_root=settings.project_root,
                data_root=data_root,
            ).build()

        self.assertEqual(payload["state"]["code"], "running")
        self.assertEqual(payload["current_series"]["id"], "latency-series-001")
        self.assertEqual(payload["current_series"]["attempt_count"], 1)
        self.assertEqual(payload["recent_attempts"][0]["outcome"], "validation_failed")
        self.assertEqual(payload["latest_observation"]["metrics"][0]["value"], 9233.8)
        self.assertEqual(payload["latest_observation"]["opportunities"][0]["kind"], "dominant_stage")
        self.assertTrue(payload["policy"]["isolated_experiments_enabled"])
        self.assertFalse(payload["policy"]["recurring_experiments_enabled"])
        self.assertFalse(payload["policy"]["automatic_promotion_enabled"])
        self.assertEqual(payload["scan"]["series_discovered"], 1)
        serialized = json.dumps(payload)
        self.assertNotIn("/private/runtime/with-owner-content", serialized)
        self.assertNotIn("attempts/latency-series-001-a01/candidate.json", serialized)
        self.assertNotIn("candidate_sha256", serialized)
        self.assertNotIn("raw model response", serialized)

    def test_stale_lock_is_reported_without_running_any_work(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_root = Path(temporary_directory) / "improvement"
            series_directory = data_root / "series" / "latency-series-001"
            series_directory.mkdir(parents=True)
            (series_directory / "manifest.json").write_text(
                json.dumps(_series_manifest(status="planned", include_attempt=False)),
                encoding="utf-8",
            )
            (series_directory / "run.lock").write_text("stale\n", encoding="utf-8")

            payload = ImprovementStatusReader(
                project_root=settings.project_root,
                data_root=data_root,
            ).build()

        self.assertEqual(payload["state"]["code"], "attention")
        self.assertIn("run lock", payload["state"]["detail"])
        self.assertEqual(payload["current_series"]["status"], "planned")

    def test_invalid_or_unrelated_manifests_do_not_break_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_root = Path(temporary_directory) / "improvement"
            invalid_directory = data_root / "series" / "bad-series"
            invalid_directory.mkdir(parents=True)
            (invalid_directory / "manifest.json").write_text(
                json.dumps(
                    {
                        "id": "bad",
                        "models": [],
                        "maximum_attempts": 3,
                        "attempts": [],
                        "status": "running",
                    }
                ),
                encoding="utf-8",
            )
            unrelated_directory = data_root / "experiments" / "candidate-001"
            unrelated_directory.mkdir(parents=True)
            (unrelated_directory / "manifest.json").write_text(
                json.dumps({"id": "candidate-001", "status": "planned"}),
                encoding="utf-8",
            )

            payload = ImprovementStatusReader(
                project_root=settings.project_root,
                data_root=data_root,
            ).build()

        self.assertEqual(payload["state"]["code"], "idle")
        self.assertEqual(payload["scan"]["series_discovered"], 0)
        self.assertEqual(payload["scan"]["malformed_artifacts"], 1)


def _series_manifest(*, status: str, include_attempt: bool) -> dict:
    attempts = []
    if include_attempt:
        attempts.append(
            {
                "attempt_number": 1,
                "experiment_id": "latency-series-001-a01",
                "requested_model": "qwen3.8:27b",
                "actual_provider": "ollama",
                "actual_model": "qwen3.8:27b",
                "outcome": "validation_failed",
                "rejection_codes": ["fixed_tests_failed"],
                "candidate_artifact": "attempts/latency-series-001-a01/candidate.json",
                "candidate_sha256": "e" * 64,
                "started_at": "2026-08-28T12:00:00Z",
                "completed_at": "2026-08-28T12:05:00Z",
            }
        )
    return {
        "version": 1,
        "id": "latency-series-001",
        "hypothesis": "Reduce grounded conversation latency without changing existing behavior.",
        "domain": "conversation_latency",
        "change_class": "behavior_or_code",
        "baseline_commit": "a" * 40,
        "contract_sha256": "b" * 64,
        "fixture_catalog_sha256": "c" * 64,
        "plan_sha256": "d" * 64,
        "grounding_sha256": "f" * 64,
        "allowed_paths": ["src/gamma/conversation/service.py"],
        "models": ["qwen3.8:27b", "gpt-oss:20b"],
        "maximum_changed_files": 2,
        "maximum_attempts": 3,
        "maximum_wall_clock_minutes": 60,
        "status": status,
        "attempts": attempts,
        "created_at": "2026-08-28T11:55:00Z",
        "started_at": "2026-08-28T12:00:00Z" if status == "running" else None,
        "authorization": "model_authored_isolated_candidates_only",
    }


def _observation() -> dict:
    return {
        "contract_version": 1,
        "generated_at": "2026-08-28T12:10:00Z",
        "runtime_dir": "/private/runtime/with-owner-content",
        "source_record_counts": {"conversation": 30, "fixtures": 30},
        "metrics": [
            {
                "metric_id": "conversation.total_ms",
                "source": "conversation",
                "role": "objective",
                "unit": "ms",
                "statistic": "p95",
                "summary": {"count": 30, "p95": 9233.8},
                "selected_value": 9233.8,
                "sufficient_data": True,
            }
        ],
        "opportunities": [
            {
                "domain": "conversation_latency",
                "priority": "high",
                "kind": "dominant_stage",
                "evidence": "draft_reply p95 accounts for most of total p95 latency.",
                "suggested_next_step": "Inspect the routed draft stage using a fresh isolated candidate.",
            }
        ],
        "warnings": [],
        "raw_model_response": "raw model response must not appear",
    }


if __name__ == "__main__":
    unittest.main()
