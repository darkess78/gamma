"""Tests must never allocate or reconcile the user's shared runtime ledger."""
from pathlib import Path
import pytest

@pytest.fixture(autouse=True)
def isolated_shared_admission(tmp_path, monkeypatch):
    monkeypatch.setenv("GAMMA_ADMISSION_DB", str(tmp_path / "admission.sqlite3"))
    monkeypatch.setenv("GAMMA_ADMISSION_POLICY", str(Path(__file__).resolve().parents[1] / "config/admission.toml"))
