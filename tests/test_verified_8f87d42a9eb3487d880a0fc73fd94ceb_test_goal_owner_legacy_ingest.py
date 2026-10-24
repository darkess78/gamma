import json
import os
import tempfile
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from gamma.coding.lessons import LessonStore

# Helper to create a minimal valid autonomous work record

def make_valid_record(work_id, skill, verified=True):
    tasks = [
        {
            "skill": skill,
            "status": "verified" if verified else "already_satisfied",
        }
    ]
    return {
        "id": work_id,
        "status": "completed",
        "tasks": tasks,
        "application": {
            "passed": True,
            "evidence": {
                "installed_checks": {
                    "passed": True,
                    "count": 3,
                },
                "activation": {
                    "passed": True,
                },
            },
        },
    }

# Regex for work id
WORK_ID_RE = re.compile(r"^work-[0-9a-f]{32}$")

@pytest.fixture
def store(tmp_path):
    return LessonStore(tmp_path)

# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field, value", [
    ("status", "not_completed"),
    ("tasks", []),
    ("tasks", [{"skill": "python", "status": "unknown"}]),
    ("application", {"passed": False}),
    ("application", {"passed": True, "evidence": {"installed_checks": {"passed": True, "count": 0}}}),
    ("application", {"passed": True, "evidence": {"installed_checks": {"passed": True, "count": True}}}),
    ("application", {"passed": True, "evidence": {"installed_checks": {"passed": True, "count": 1}, "activation": {"passed": False}}}),
])
def test_ingest_autonomous_invalid(store, field, value):
    record = make_valid_record("work-" + "0"*32, "python")
    record[field] = value
    # Should not raise, but should not create a lesson
    store.ingest_autonomous(record)
    assert store.list() == []

# ---------------------------------------------------------------------------
# Successful ingestion
# ---------------------------------------------------------------------------

def test_ingest_autonomous_creates_lesson(store):
    record = make_valid_record("work-" + "a"*32, "python")
    store.ingest_autonomous(record)
    lessons = store.list()
    assert len(lessons) == 1
    lesson = lessons[0]
    assert lesson["id"] == "lesson-python"
    assert lesson["skill"] == "python"
    assert lesson["active"] is True
    assert lesson["authority"] == "observed_repository"
    assert lesson["evidence_runs"] == [record["id"]]
    # Check history provenance
    hist = store.history(lesson["id"])
    assert hist
    assert hist[0]["provenance"] == "autonomous_ingest"

# ---------------------------------------------------------------------------
# Deduplication: same work id should not create duplicate evidence
# ---------------------------------------------------------------------------

def test_ingest_autonomous_deduplication(store):
    record = make_valid_record("work-" + "b"*32, "python")
    store.ingest_autonomous(record)
    # ingest again
    store.ingest_autonomous(record)
    lessons = store.list()
    assert len(lessons) == 1
    assert lessons[0]["evidence_runs"] == [record["id"]]

# ---------------------------------------------------------------------------
# Owner edited lesson should not be overwritten
# ---------------------------------------------------------------------------

def test_ingest_autonomous_preserves_owner_edit(store):
    # First ingest to create lesson
    record = make_valid_record("work-" + "c"*32, "python")
    store.ingest_autonomous(record)
    lesson_id = "lesson-python"
    # Edit by owner
    store.edit(lesson_id, text="Owner text", active=False)
    # Ingest another autonomous record for same skill
    new_record = make_valid_record("work-" + "d"*32, "python")
    store.ingest_autonomous(new_record)
    lesson = store.list()[0]
    # Owner text and active state should be preserved
    assert lesson["text"] == "Owner text"
    assert lesson["active"] is False
    # Evidence runs should include both work ids
    assert set(lesson["evidence_runs"]) == {record["id"], new_record["id"]}

# ---------------------------------------------------------------------------
# History size limit (last 20 entries)
# ---------------------------------------------------------------------------

def test_history_limit(store):
    # Create 25 autonomous records for same lesson
    for i in range(25):
        wid = f"work-{i:032x}"
        record = make_valid_record(wid, "python")
        store.ingest_autonomous(record)
    hist = store.history("lesson-python")
    # Should keep at most 20 snapshots
    assert len(hist) <= 20
    # The most recent provenance should be autonomous_ingest
    assert hist[0]["provenance"] == "autonomous_ingest"

# ---------------------------------------------------------------------------
# Ensure that non‑matching work id pattern is rejected
# ---------------------------------------------------------------------------

def test_ingest_autonomous_invalid_work_id(store):
    record = make_valid_record("invalid-id", "python")
    store.ingest_autonomous(record)
    assert store.list() == []

# ---------------------------------------------------------------------------
# Ensure that missing required fields cause no lesson creation
# ---------------------------------------------------------------------------

def test_ingest_autonomous_missing_fields(store):
    record = {
        "id": "work-" + "e"*32,
        # status missing
        "tasks": [{"skill": "python", "status": "verified"}],
        "application": {"passed": True, "evidence": {"installed_checks": {"passed": True, "count": 1}, "activation": {"passed": True}}},
    }
    store.ingest_autonomous(record)
    assert store.list() == []
