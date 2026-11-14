from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from gamma.config import settings
from gamma.memory import sqlite_setup
from gamma.memory.continuity import ContinuityService
from gamma.memory.service import MemoryService


def test_memory_service_creates_missing_database_directory(tmp_path, monkeypatch):
    path = tmp_path / "fresh" / "memory" / "gamma.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{path}")
    service = MemoryService()
    try:
        assert path.exists()
    finally:
        service.close()


def test_continuity_service_creates_missing_database_directory(tmp_path):
    path = tmp_path / "fresh" / "continuity.db"
    ContinuityService(database_url=f"sqlite:///{path}")
    assert path.exists()


def test_in_memory_url_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sqlite_setup.ensure_sqlite_parent("sqlite:///:memory:")
    sqlite_setup.ensure_sqlite_parent("postgresql://host/db")
    assert list(tmp_path.iterdir()) == []


class _Cursor:
    def __init__(self, failures: int, message: str = "database is locked") -> None:
        self.failures = failures
        self.message = message
        self.calls = 0

    def execute(self, sql: str) -> None:
        self.calls += 1
        if self.calls <= self.failures:
            raise sqlite3.OperationalError(self.message)


def test_enable_wal_retries_a_concurrent_switch():
    cursor = _Cursor(failures=3)
    with patch.object(sqlite_setup.time, "sleep"):
        sqlite_setup.enable_wal(cursor)
    assert cursor.calls == 4


def test_enable_wal_does_not_retry_other_errors():
    cursor = _Cursor(failures=1, message="disk I/O error")
    with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
        sqlite_setup.enable_wal(cursor)
    assert cursor.calls == 1
