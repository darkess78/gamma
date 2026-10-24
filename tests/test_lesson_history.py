import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import pytest

from gamma.coding.lessons import LessonStore

# Helper to create a minimal observation result

def make_result(id, tasks):
    return {
        "id": id,
        "version": 1,
        "production_verified": False,
        "tasks": tasks,
    }

# Helper to create a task dict

def make_task(skill, status, attempt_count=0):
    return {
        "skill": skill,
        "status": status,
        "attempt_count": attempt_count,
    }

@pytest.fixture
def store(tmp_path):
    return LessonStore(tmp_path)

def test_history_after_ingest_and_edit(store):
    # Ingest two observations for the same skill
    skill = "python"
    id1 = "run-1"
    id2 = "run-2"
    store.ingest(make_result(id1, [make_task(skill, "verified_practice")]))
    store.ingest(make_result(id2, [make_task(skill, "verified_practice")]))

    identifier = f"lesson-{skill}"
    # Edit the lesson
    new_text = "Updated lesson text"
    store.edit(identifier, text=new_text, active=True)

    # Retrieve history
    history = store.history(identifier)
    assert isinstance(history, list)
    # Should contain at least the edit and the two ingests
    assert len(history) >= 3
    # Newest first
    assert history[0]["updated_at"] >= history[1]["updated_at"]
    # Each snapshot has required keys
    required_keys = {"text", "active", "authority", "evidence_runs", "updated_at"}
    for snap in history:
        assert required_keys.issubset(snap.keys())
    # The most recent snapshot should reflect the edit
    assert history[0]["text"] == new_text
    assert history[0]["authority"] == "owner_edited"

def test_history_bounded_to_20(store):
    skill = "java"
    identifier = f"lesson-{skill}"
    # Create 25 ingests
    for i in range(25):
        store.ingest(make_result(f"run-{i}", [make_task(skill, "verified_practice")]))
    # Edit once
    store.edit(identifier, text="final text", active=True)
    history = store.history(identifier)
    # Should be bounded to 20 entries
    assert len(history) == 20
    # The most recent should be the edit
    assert history[0]["text"] == "final text"
    # The oldest in history should correspond to the 5th most recent ingest
    # (since 20 entries total, 1 edit + 19 ingests)
    assert history[-1]["updated_at"] <= history[-2]["updated_at"]

# Ensure that history preserves owner edits and observation updates

def test_history_preserves_provenance(store):
    skill = "go"
    identifier = f"lesson-{skill}"
    # Ingest one observation
    store.ingest(make_result("run-obs", [make_task(skill, "verified_practice")]))
    # Edit by owner
    store.edit(identifier, text="Owner edit", active=True)
    # Ingest another observation
    store.ingest(make_result("run-obs2", [make_task(skill, "verified_practice")]))
    history = store.history(identifier)
    # The history should contain three snapshots: two observations and one edit
    assert len(history) == 3
    # The order should be newest first
    assert history[0]["updated_at"] >= history[1]["updated_at"] >= history[2]["updated_at"]
    # The middle snapshot should be the owner edit
    assert history[1]["authority"] == "owner_edited"
    assert history[1]["text"] == "Owner edit"
    # The oldest snapshot should be the first observation
    assert history[2]["authority"] == "observed_practice"
