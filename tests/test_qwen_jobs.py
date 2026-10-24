import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("qwen_jobs", Path(__file__).resolve().parents[1] / "scripts/qwen_jobs.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_bounded_ledger_never_evicts_active_jobs_and_rejects_retained_replay():
    ledger = module.JobLedger(capacity=2)
    first, second, third = "a" * 32, "b" * 32, "c" * 32
    ledger.begin(first)
    ledger.begin(second)
    with pytest.raises(RuntimeError):
        ledger.begin(third)
    ledger.finish(second, False)
    with pytest.raises(RuntimeError):
        ledger.begin(second)
    ledger.begin(third)
    assert ledger.status(first)["state"] == "active"
    assert ledger.status(second)["state"] == "unknown"
    ledger.finish(first, True)
    assert ledger.status(first)["state"] == "completed"
    assert module.JobLedger().generation != ledger.generation


@pytest.mark.parametrize("value", ["a" * 32 + "\n", "../private", "", None, "A" * 32])
def test_invalid_job_identity(value):
    with pytest.raises(ValueError):
        module.JobLedger().begin(value)
