from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..improvement.contract import load_improvement_contract
from ..improvement.models import ObservationReport

if TYPE_CHECKING:
    from ..improvement.coordinator import ExperimentSeriesManifest


_MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
_MAX_SCANNED_FILES = 5_000
_MAX_SCANNED_DIRECTORIES = 500
_MAX_RECENT_SERIES = 12
_MAX_RECENT_ATTEMPTS = 20
_SKIPPED_DIRECTORIES = {"worktrees", ".git", ".venv", "__pycache__"}


class ImprovementStatusReader:
    """Build a bounded, sanitized, read-only view of improvement activity."""

    def __init__(
        self,
        *,
        project_root: Path,
        data_root: Path | None = None,
        contract_path: Path | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.data_root = data_root or self.project_root / "data" / "improvement"
        self.contract_path = (contract_path or self.project_root / "config" / "improvement.toml").resolve()

    def build(self) -> dict[str, Any]:
        policy, policy_error = self._policy_status()
        series, observations, scan = self._discover_artifacts()
        ordered_series = sorted(series, key=_series_sort_key, reverse=True)
        recent_series = ordered_series[:_MAX_RECENT_SERIES]
        latest_observation = (
            max(observations, key=lambda item: _timestamp_sort_key(item.get("generated_at")))
            if observations
            else None
        )
        current_series = next(
            (item for item in ordered_series if item["status"] in {"running", "planned"}),
            None,
        )
        latest_series = ordered_series[0] if ordered_series else None
        state = _engine_state(current_series=current_series, latest_series=latest_series, series=ordered_series)
        recent_attempts = sorted(
            (
                {"series_id": item["id"], **attempt}
                for item in recent_series
                for attempt in item["attempts"]
            ),
            key=lambda item: _timestamp_sort_key(item.get("completed_at") or item.get("started_at")),
            reverse=True,
        )[:_MAX_RECENT_ATTEMPTS]
        safeguards = _safeguards(policy)
        return {
            "version": 1,
            "generated_at": _utc_now(),
            "state": state,
            "policy": policy,
            "policy_error": policy_error,
            "current_series": current_series,
            "latest_series": latest_series,
            "recent_series": recent_series,
            "recent_attempts": recent_attempts,
            "latest_observation": latest_observation,
            "safeguards": safeguards,
            "scan": {
                **scan,
                "series_discovered": len(series),
                "observations_discovered": len(observations),
            },
        }

    def _policy_status(self) -> tuple[dict[str, Any], str | None]:
        try:
            contract = load_improvement_contract(self.contract_path)
        except (OSError, ValueError):
            return {
                "contract_loaded": False,
                "contract_version": None,
                "isolated_experiments_enabled": False,
                "recurring_experiments_enabled": False,
                "automatic_promotion_enabled": False,
                "required_gates": {},
            }, "Improvement contract could not be loaded."
        return {
            "contract_loaded": True,
            "contract_version": contract.version,
            "isolated_experiments_enabled": contract.policy.isolated_experiments_enabled,
            "recurring_experiments_enabled": contract.policy.recurring_experiments_enabled,
            "automatic_promotion_enabled": False,
            "maximum_records_per_source": contract.policy.maximum_records_per_source,
            "required_gates": {
                change_class: list(gates)
                for change_class, gates in sorted(contract.policy.required_gates.items())
            },
        }, None

    def _discover_artifacts(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        series: list[dict[str, Any]] = []
        observations: list[dict[str, Any]] = []
        malformed = 0
        files_scanned = 0
        directories_scanned = 0
        truncated = False
        if self.data_root.is_symlink() or not self.data_root.is_dir():
            return series, observations, {
                "data_available": False,
                "files_scanned": 0,
                "directories_scanned": 0,
                "malformed_artifacts": 0,
                "truncated": False,
            }

        for directory, directory_names, file_names in os.walk(self.data_root, followlinks=False):
            directories_scanned += 1
            directory_names[:] = [
                name
                for name in sorted(directory_names)
                if name not in _SKIPPED_DIRECTORIES and not (Path(directory) / name).is_symlink()
            ]
            if directories_scanned >= _MAX_SCANNED_DIRECTORIES:
                directory_names[:] = []
                truncated = True
            for file_name in sorted(file_names):
                files_scanned += 1
                if files_scanned > _MAX_SCANNED_FILES:
                    truncated = True
                    break
                path = Path(directory) / file_name
                if file_name == "manifest.json":
                    payload = _read_json_object(path)
                    if payload is None or not _looks_like_series(payload):
                        continue
                    try:
                        from ..improvement.coordinator import ExperimentSeriesManifest

                        manifest = ExperimentSeriesManifest.model_validate(payload)
                    except ValueError:
                        malformed += 1
                        continue
                    series.append(_series_summary(manifest, lock_present=(path.parent / "run.lock").is_file()))
                elif "observation" in file_name.lower() and file_name.lower().endswith(".json"):
                    payload = _read_json_object(path)
                    if payload is None or not _looks_like_observation(payload):
                        continue
                    try:
                        observation = ObservationReport.model_validate(payload)
                    except ValueError:
                        malformed += 1
                        continue
                    observations.append(_observation_summary(observation))
            if files_scanned > _MAX_SCANNED_FILES:
                break

        return series, observations, {
            "data_available": True,
            "files_scanned": min(files_scanned, _MAX_SCANNED_FILES),
            "directories_scanned": min(directories_scanned, _MAX_SCANNED_DIRECTORIES),
            "malformed_artifacts": malformed,
            "truncated": truncated,
        }


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        if path.is_symlink() or path.stat().st_size > _MAX_ARTIFACT_BYTES:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _looks_like_series(payload: dict[str, Any]) -> bool:
    return all(key in payload for key in ("id", "models", "maximum_attempts", "attempts", "status"))


def _looks_like_observation(payload: dict[str, Any]) -> bool:
    return all(key in payload for key in ("metrics", "opportunities", "source_record_counts"))


def _series_summary(manifest: ExperimentSeriesManifest, *, lock_present: bool) -> dict[str, Any]:
    return {
        "id": _bounded_text(manifest.id, 80),
        "hypothesis": _bounded_text(manifest.hypothesis, 700),
        "domain": _bounded_text(manifest.domain, 80),
        "change_class": manifest.change_class.value,
        "baseline_commit": manifest.baseline_commit[:12],
        "models": [_bounded_text(model, 200) for model in manifest.models],
        "maximum_attempts": manifest.maximum_attempts,
        "maximum_wall_clock_minutes": manifest.maximum_wall_clock_minutes,
        "attempt_count": len(manifest.attempts),
        "remaining_attempts": max(0, manifest.maximum_attempts - len(manifest.attempts)),
        "status": manifest.status,
        "terminal_reason": _bounded_text(manifest.terminal_reason, 120) if manifest.terminal_reason else None,
        "successful_experiment_id": manifest.successful_experiment_id,
        "source_scope": [_bounded_text(path, 240) for path in manifest.allowed_paths[:12]],
        "source_scope_truncated": len(manifest.allowed_paths) > 12,
        "created_at": manifest.created_at,
        "started_at": manifest.started_at,
        "completed_at": manifest.completed_at,
        "lock_present": lock_present,
        "attempts": [
            {
                "attempt_number": attempt.attempt_number,
                "experiment_id": attempt.experiment_id,
                "requested_model": _bounded_text(attempt.requested_model, 200),
                "actual_model": _bounded_text(attempt.actual_model, 200) if attempt.actual_model else None,
                "outcome": attempt.outcome,
                "rejection_codes": [_bounded_text(code, 80) for code in attempt.rejection_codes],
                "started_at": attempt.started_at,
                "completed_at": attempt.completed_at,
            }
            for attempt in manifest.attempts
        ],
    }


def _observation_summary(report: ObservationReport) -> dict[str, Any]:
    return {
        "generated_at": report.generated_at,
        "source_record_counts": {
            _bounded_text(source, 80): count
            for source, count in sorted(report.source_record_counts.items())
        },
        "metrics": [
            {
                "id": _bounded_text(metric.metric_id, 160),
                "role": metric.role,
                "statistic": metric.statistic,
                "value": metric.selected_value,
                "unit": _bounded_text(metric.unit, 40),
                "sample_count": metric.summary.count,
                "sufficient_data": metric.sufficient_data,
            }
            for metric in report.metrics
        ],
        "opportunities": [
            {
                "domain": _bounded_text(item.domain, 80),
                "priority": item.priority,
                "kind": _bounded_text(item.kind, 120),
                "evidence": _bounded_text(item.evidence, 700),
                "suggested_next_step": _bounded_text(item.suggested_next_step, 700),
            }
            for item in report.opportunities[:12]
        ],
        "warnings": [_bounded_text(item, 500) for item in report.warnings[:12]],
    }


def _engine_state(
    *,
    current_series: dict[str, Any] | None,
    latest_series: dict[str, Any] | None,
    series: list[dict[str, Any]],
) -> dict[str, str]:
    stale_lock = next((item for item in series if item["lock_present"] and item["status"] != "running"), None)
    if stale_lock:
        return {
            "code": "attention",
            "label": "Attention required",
            "detail": f"{stale_lock['id']} has a run lock outside a running state; inspect it before retrying.",
        }
    if current_series and current_series["status"] == "running":
        if not current_series["lock_present"]:
            return {
                "code": "attention",
                "label": "Interrupted run suspected",
                "detail": f"{current_series['id']} says it is running, but its active run lock is missing.",
            }
        return {
            "code": "running",
            "label": "Experiment running",
            "detail": f"Gamma is testing {current_series['domain']} in isolated attempt {current_series['attempt_count'] + 1} of {current_series['maximum_attempts']}.",
        }
    if current_series and current_series["status"] == "planned":
        return {
            "code": "planned",
            "label": "Experiment planned",
            "detail": f"{current_series['id']} is prepared but has not started.",
        }
    if latest_series and latest_series["status"] == "ready_for_holdout":
        return {
            "code": "review",
            "label": "Ready for holdout",
            "detail": "Fixed tests and independent semantic review passed; performance and owner review are still required.",
        }
    if latest_series and latest_series["status"] == "fixed_tests_passed":
        return {
            "code": "review",
            "label": "Fixed tests passed",
            "detail": "The candidate still needs holdout evidence and owner review; it has not been promoted.",
        }
    if latest_series:
        return {
            "code": "idle",
            "label": "Idle",
            "detail": f"The latest series ended as {latest_series['status']}; no isolated series is currently running.",
        }
    return {
        "code": "idle",
        "label": "Idle",
        "detail": "No isolated improvement series has been recorded yet.",
    }


def _safeguards(policy: dict[str, Any]) -> list[dict[str, Any]]:
    behavior_gates = policy.get("required_gates", {}).get("behavior_or_code", [])
    return [
        {
            "id": "isolated_candidates",
            "label": "Isolated candidates",
            "enforced": bool(policy.get("isolated_experiments_enabled")),
            "detail": "Candidate edits run in fresh detached worktrees, never in the live checkout.",
        },
        {
            "id": "recurring_disabled",
            "label": "Recurring runs disabled",
            "enforced": not bool(policy.get("recurring_experiments_enabled")),
            "detail": "Gamma cannot schedule an unattended recurring improvement loop under the current contract.",
        },
        {
            "id": "automatic_promotion_absent",
            "label": "No automatic promotion",
            "enforced": not bool(policy.get("automatic_promotion_enabled")),
            "detail": "Passing candidates remain review artifacts; the framework has no live promotion operation.",
        },
        {
            "id": "owner_approval",
            "label": "Owner approval required",
            "enforced": "human_approval" in behavior_gates,
            "detail": "Behavior or code changes require recorded human approval after the other gates pass.",
        },
        {
            "id": "read_only_dashboard",
            "label": "Read-only dashboard",
            "enforced": True,
            "detail": "This page can inspect sanitized status only; it cannot start, approve, deploy, or promote work.",
        },
    ]


def _series_sort_key(item: dict[str, Any]) -> float:
    return _timestamp_sort_key(item.get("completed_at") or item.get("started_at") or item.get("created_at"))


def _timestamp_sort_key(value: object) -> float:
    if not value:
        return 0.0
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError:
        return 0.0


def _bounded_text(value: object, maximum: int) -> str:
    text = str(value).strip()
    return text if len(text) <= maximum else text[: maximum - 1] + "…"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
