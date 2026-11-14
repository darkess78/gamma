"""Copied legacy tables receive an explicit disposition without replay authority."""

from copy import deepcopy
import hashlib
import json
import sqlite3

import pytest

from gamma.coordination.core import Core
from gamma.coordination.personal import digest
from gamma.coordination.personal_import import reconcile, reconcile_snapshot
from gamma.coordination.store import Store
from gamma.schemas.response import AssistantResponse


@pytest.fixture
def core(tmp_path):
    instance = Core(Store(tmp_path / "state"), runner=object(), clock=lambda: 100,
                    draft=lambda *_: AssistantResponse(display_text="Fixture", delivery_mode="text_only"))
    try:
        yield instance
    finally:
        instance.close()


def stored(core, kind):
    with core.store.transaction() as db:
        return core.store.list(db, kind, None)


@pytest.mark.parametrize("table", [
    "conversationjournalowner", "conversationinputcursor", "stream_viewer_trust",
    "conversationcontinuityowner", "conversationcursor", "stream_temp_memory", "durableoutputstate",
])
def test_operational_tables_are_only_content_free_references(core, table):
    marker = "PRIVATE_OPERATIONAL_PAYLOAD_MUST_NOT_BE_IMPORTED"
    tables = {table: [{"id": 1, "owner": marker, "text": marker, "cursor": 99, "status": "pending"}]}
    original = deepcopy(tables)
    brain = core.store.read("brain", "state")
    people = stored(core, "person")
    accounts = stored(core, "account")
    first = reconcile(core, "fixture:disposition", tables)
    references = stored(core, "personal_quarantine")
    assert len(references) == first["quarantined"] == 1
    assert references[0]["table"] == table
    assert references[0]["row_count"] == 1
    assert references[0]["payload_sha256"] == digest(tables[table])
    assert references[0]["status"] == "not_personal_state"
    assert first["table_census"][table] == {
        "disposition": "operational_quarantine", "row_count": 1, "payload_sha256": digest(tables[table]),
    }
    second = reconcile(core, "fixture:disposition", deepcopy(tables))
    assert second["migration_id"] == first["migration_id"]
    assert second["table_census"] == first["table_census"]
    assert second["quarantined"] == 1
    assert stored(core, "personal_quarantine") == references
    assert tables == original
    assert not stored(core, "personal_source") and not stored(core, "personal_record")
    assert stored(core, "person") == people and stored(core, "account") == accounts
    after = core.store.read("brain", "state")
    assert {key: value for key, value in after.items() if key != "sequence"} == \
        {key: value for key, value in brain.items() if key != "sequence"}
    # Each import invalidates derived personal context through the existing fence.
    assert after["sequence"] == brain["sequence"] + 2
    with core.store.transaction() as db:
        assert marker not in json.dumps(db.execute("SELECT kind,id,body FROM records").fetchall())
        assert core.personal.recall(db, marker) == []
        assert not set(row[0] for row in db.execute("SELECT DISTINCT kind FROM records")) & {
            "turn", "message", "task", "work_request", "interaction_output", "phone_message", "phone_request",
            "conversationjournalowner", "conversationinputcursor",
        }


def test_census_distinguishes_import_provenance_quarantine_and_sqlite_metadata(core):
    tables = {
        "profilefact": [{"id": 1, "fact_text": "Fictional drink", "subject_type": "primary_user", "confidence": .4}],
        "rollingsessionsummary": [{"session_id": "fixture", "summary": "Fictional summary"}],
        "future_private_table": [{"text": "UNKNOWN_PRIVATE_PAYLOAD"}, {"text": "SECOND_UNKNOWN_PRIVATE_PAYLOAD"}],
        "empty_future_table": [],
        "sqlite_sequence": [{"name": "future_private_table", "seq": 123}],
    }
    report = reconcile(core, "fixture:census", tables)
    expected = {
        "profilefact": "converted", "rollingsessionsummary": "checkpoint_provenance",
        "future_private_table": "unsupported_quarantine", "empty_future_table": "unsupported_quarantine",
        "sqlite_sequence": "sqlite_metadata",
    }
    assert set(report["table_census"]) == set(tables)
    for table, disposition in expected.items():
        assert report["table_census"][table] == {
            "disposition": disposition, "row_count": len(tables[table]), "payload_sha256": digest(tables[table]),
        }
    quarantine = stored(core, "personal_quarantine")
    assert report["quarantined"] == len(quarantine) == 1
    assert quarantine[0]["row_count"] == 2
    assert quarantine[0]["table"] == "future_private_table"
    assert quarantine[0]["status"] == "unsupported_legacy_table"
    migration = stored(core, "personal_migration")[0]
    assert migration["table_census"] == report["table_census"]
    assert migration["execution_authority_granted"] is False
    with core.store.transaction() as db:
        assert "UNKNOWN_PRIVATE_PAYLOAD" not in json.dumps(db.execute("SELECT body FROM records").fetchall())


def test_quarantine_total_counts_references_not_rows_or_sqlite_metadata(core):
    tables = {
        "profilefact": [{"id": None, "fact_text": "Invalid fixture"}],
        "workingstatecheckpoint": [{"summary": "Missing fixture primary key"}, {"summary": "Missing fixture primary key"}],
        "conversationjournalowner": [{"id": 1}, {"id": 2}],
        "future_private_table": [{"id": 1}, {"id": 2}, {"id": 3}],
        "sqlite_sequence": [{"name": "profilefact", "seq": 10}],
        "empty_table": [],
    }
    report = reconcile(core, "fixture:totals", tables)
    assert report["quarantined"] == len(stored(core, "personal_quarantine")) == 4
    repeated = reconcile(core, "fixture:totals", tables)
    assert repeated["quarantined"] == 4
    assert len(stored(core, "personal_quarantine")) == 4


def test_snapshot_reader_accounts_for_quoted_unknown_table_without_importing_payload(core, tmp_path):
    copied = tmp_path / "copy"
    copied.mkdir(mode=0o700)
    database = copied / "gamma.db"
    table = 'future-"table'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE "future-""table" (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT)')
        db.execute('INSERT INTO "future-""table" (text) VALUES (?)', ("QUOTED_PRIVATE_PAYLOAD",))
    original = database.read_bytes()
    manifest = {"database": {"snapshot_path": str(database), "snapshot_sha256": hashlib.sha256(original).hexdigest()},
                "copied_files": []}
    (copied / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    report = reconcile_snapshot(core, copied, namespace="fixture:quoted-table")
    assert set(report["table_census"]) == {table, "sqlite_sequence"}
    assert report["table_census"][table]["disposition"] == "unsupported_quarantine"
    assert report["quarantined"] == 1
    assert database.read_bytes() == original
    assert not stored(core, "personal_source") and not stored(core, "personal_record")
    with core.store.transaction() as db:
        assert "QUOTED_PRIVATE_PAYLOAD" not in json.dumps(db.execute("SELECT body FROM records").fetchall())


@pytest.mark.parametrize("portable", [False, True])
def test_portable_and_historical_manifest_paths_reconcile_the_same_copy(core, tmp_path, portable):
    copied = tmp_path / "copy"
    database = copied / "database" / "memory.sqlite3"
    auxiliary = copied / "raw" / "data" / "assistant_state.json"
    database.parent.mkdir(parents=True, mode=0o700)
    auxiliary.parent.mkdir(parents=True, mode=0o700)
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE profilefact (id INTEGER PRIMARY KEY, fact_text TEXT, subject_type TEXT, confidence REAL)")
        db.execute("INSERT INTO profilefact VALUES (1,'Portable fictional claim','primary_user',0.4)")
    auxiliary.write_text('{"note":"Unverified fixture state"}', encoding="utf-8")
    original_db, original_aux = database.read_bytes(), auxiliary.read_bytes()
    manifest = {"database": {
        "snapshot_path": "database/memory.sqlite3" if portable else str(database),
        "snapshot_sha256": hashlib.sha256(original_db).hexdigest(),
    }, "copied_files": [{"relative_path": "data/assistant_state.json",
        "snapshot_path": "raw/data/assistant_state.json" if portable else str(auxiliary),
        "sha256": hashlib.sha256(original_aux).hexdigest()}]}
    (copied / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    first = reconcile_snapshot(core, copied, namespace="fixture:portable")
    assert first["records_added"] == 1 and first["sources_added"] == 2
    second = reconcile_snapshot(core, copied, namespace="fixture:portable")
    assert second["migration_id"] == first["migration_id"]
    assert second["records_added"] == second["sources_added"] == 0
    assert database.read_bytes() == original_db and auxiliary.read_bytes() == original_aux


@pytest.mark.parametrize("member", ["database", "auxiliary"])
@pytest.mark.parametrize("bad_path", ["../outside", "raw/../../outside", "..\\outside"])
def test_portable_manifest_traversal_fails_before_canonical_import(core, tmp_path, member, bad_path):
    copied = tmp_path / "copy"
    copied.mkdir(mode=0o700)
    database = copied / "memory.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE profilefact (id INTEGER PRIMARY KEY, fact_text TEXT)")
        db.execute("INSERT INTO profilefact VALUES (1,'Must not import')")
    manifest = {"database": {"snapshot_path": bad_path if member == "database" else "memory.sqlite3",
        "snapshot_sha256": hashlib.sha256(database.read_bytes()).hexdigest()}, "copied_files": []}
    if member == "auxiliary":
        manifest["copied_files"] = [{"relative_path": "data/assistant_state.json", "snapshot_path": bad_path, "sha256": "0" * 64}]
    (copied / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="traversal|portable|outside"):
        reconcile_snapshot(core, copied, namespace="fixture:bad-path")
    assert not stored(core, "personal_source") and not stored(core, "personal_record")
    assert not stored(core, "personal_migration")


@pytest.mark.parametrize("member", ["database", "auxiliary"])
def test_portable_manifest_symlink_members_fail_before_import(core, tmp_path, member):
    copied = tmp_path / "copy"
    copied.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.mkdir()
    database = outside / "memory.sqlite3" if member == "database" else copied / "memory.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE profilefact (id INTEGER PRIMARY KEY, fact_text TEXT)")
    manifest = {"database": {"snapshot_path": "memory.sqlite3",
        "snapshot_sha256": hashlib.sha256(database.read_bytes()).hexdigest()}, "copied_files": []}
    if member == "database":
        (copied / "memory.sqlite3").symlink_to(database)
    else:
        auxiliary = outside / "state.json"
        auxiliary.write_text('{"note":"outside fixture"}', encoding="utf-8")
        (copied / "raw").symlink_to(outside, target_is_directory=True)
        manifest["copied_files"] = [{"relative_path": "data/assistant_state.json", "snapshot_path": "raw/state.json",
            "sha256": hashlib.sha256(auxiliary.read_bytes()).hexdigest()}]
    (copied / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="symlink"):
        reconcile_snapshot(core, copied, namespace="fixture:symlink")
    assert not stored(core, "personal_migration") and not stored(core, "personal_source")
