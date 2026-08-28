from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..config import PROJECT_ROOT
from .models import ChangeClass


class MetricContract(BaseModel):
    id: str
    source: Literal["conversation", "llm_routes", "fixtures", "live_voice"]
    path: str
    aggregation: Literal["numeric", "rate"] = "numeric"
    match_values: tuple[str, ...] = ()
    role: Literal["objective", "guardrail", "diagnostic"]
    direction: Literal["lower", "higher"]
    statistic: Literal["mean", "p50", "p95", "p99"] = "p95"
    unit: str = "ms"
    minimum_samples: int = Field(default=20, ge=1, le=100_000)
    minimum_practical_change_percent: float = Field(default=5.0, ge=0.0, le=100.0)
    maximum_regression_percent: float = Field(default=3.0, ge=0.0, le=100.0)

    @model_validator(mode="after")
    def validate_rate(self) -> "MetricContract":
        if self.aggregation == "rate" and not self.match_values:
            raise ValueError(f"rate metric {self.id!r} requires match_values")
        return self


class ImprovementPolicy(BaseModel):
    maximum_records_per_source: int = Field(default=5000, ge=1, le=1_000_000)
    dominant_stage_share_percent: float = Field(default=25.0, ge=0.0, le=100.0)
    first_audio_warning_ms: float = Field(default=5000.0, ge=0.0, le=300_000.0)
    isolated_experiments_enabled: bool = False
    experiment_worktree_root: str = "./data/improvement/worktrees"
    required_gates: dict[str, tuple[str, ...]]

    def gates_for(self, change_class: ChangeClass) -> tuple[str, ...]:
        return self.required_gates.get(change_class.value, ())


class ImprovementContract(BaseModel):
    version: int = Field(ge=1)
    policy: ImprovementPolicy
    metrics: tuple[MetricContract, ...]

    @model_validator(mode="after")
    def validate_unique_metrics(self) -> "ImprovementContract":
        metric_ids = [metric.id for metric in self.metrics]
        if len(metric_ids) != len(set(metric_ids)):
            raise ValueError("improvement metric ids must be unique")
        missing_classes = sorted(item.value for item in ChangeClass if item.value not in self.policy.required_gates)
        if missing_classes:
            raise ValueError(f"required_gates is missing change classes: {', '.join(missing_classes)}")
        return self


def load_improvement_contract(path: Path | None = None) -> ImprovementContract:
    contract_path = path or PROJECT_ROOT / "config" / "improvement.toml"
    payload = tomllib.loads(contract_path.read_text(encoding="utf-8"))
    return ImprovementContract.model_validate(payload)
