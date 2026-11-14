"""Private migration rehearsals use fictional rows and real coordinator controls."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from gamma.coordination.core import Core
from gamma.coordination.legacy_rows import convert_legacy
from gamma.coordination.personal_import import reconcile, reconcile_snapshot
from gamma.coordination.store import Store
from gamma.schemas.response import AssistantResponse


NAMESPACE = "fixture:personal-memory"


class Runner:
    pass


@pytest.fixture
def core(tmp_path):
    candidate = Core(Store(tmp_path / "state"), runner=Runner(), clock=lambda: 100,
                     draft=lambda *_: AssistantResponse(display_text="Acknowledged.", delivery_mode="text_only"))
    try:
        yield candidate
    finally:
        candidate.close()


def rows():
    return {
        "knownperson": [{"id": 1, "name": "Mira", "relationship_to_user": "Friend", "notes": "Fictional note", "trust": "owner"}],
        "personidentity": [{"id": 2, "person_id": 1, "platform": "discord", "platform_user_id": "fixture-123", "display_name": "Mira"}],
        "profilefact": [{"id": 3, "fact_text": "The workshop drink is jasmine tea.", "category": "preference",
                         "subject_type": "primary_user", "confidence": .4, "attribution": '{"kind":"self_report"}'}],
        "conversationjournalentry": [{"id": 4, "text": "An original statement.", "role": "user",
                                      "completion_status": "completed", "privacy_scope": "local_generic"}],
    }


def stored(core, kind):
    with core.store.transaction() as db:
        return core.store.list(db, kind, None)


def imported(core, kind):
    return [item for item in stored(core, kind) if item.get("legacy_table") or item.get("legacy_ref", {}).get("namespace") == NAMESPACE]


def test_real_reconciliation_preserves_provenance_and_is_idempotent(core):
    original = rows()
    first = reconcile(core, NAMESPACE, original)
    record = imported(core, "personal_record")[0]
    assert record["confidence"] == .4 and record["certainty"] == "legacy_unverified"
    source = core.store.read("personal_source", record["source_id"])
    assert source["source_kind"] == "legacy_reported"
    assert source["legacy_ref"] == {"namespace": NAMESPACE, "table": "profilefact", "id": 3}
    assert source["scope"] == "owner_private"
    second = reconcile(core, NAMESPACE, deepcopy(original))
    assert all(second[key] == 0 for key in ("sources_added", "records_added", "people_added", "accounts_added"))
    assert second["migration_id"] == first["migration_id"]
    assert stored(core,"personal_migration")[0]['sources_added']==first['sources_added']
    assert imported(core, "personal_record") == [record]
    account = imported(core, "account")[0]
    person = imported(core, "person")[0]
    assert account["grants_authority"] is False
    assert person["legacy_trust_label"] == "owner" and "permissions" not in person and "is_owner" not in person


def test_later_owner_correction_survives_exact_and_changed_legacy_reimport(core):
    tables = rows()
    reconcile(core, NAMESPACE, tables)
    mid = imported(core, "personal_record")[0]["id"]
    core.personal_control("correct", "memory", {"op": "correct", "target_id": mid,
        "kind": "preference", "text": "The workshop drink is mint tea."})
    corrected = core.store.read("personal_record", mid)
    reconcile(core, NAMESPACE, tables)
    tables["profilefact"][0]["fact_text"] = "An externally changed legacy claim."
    report = reconcile(core, NAMESPACE, tables)
    assert core.store.read("personal_record", mid) == corrected
    assert report["conflicts"] >= 1
    changed = convert_legacy(NAMESPACE, tables)["records"][0]["source_id"]
    assert core.store.read("personal_source", changed)["status"] == "quarantined"
    assert stored(core, "maintenance_proposal")


def test_forgotten_memory_cannot_be_resurrected_by_mutated_legacy_payload(core):
    tables = rows()
    reconcile(core, NAMESPACE, tables)
    record = imported(core, "personal_record")[0]
    core.personal_control("forget", "forget", {"target_id": record["id"]})
    tables["profilefact"][0]["fact_text"] = "FORGOTTEN_FIXTURE_SENTINEL with changed whitespace."
    reconcile(core, NAMESPACE, tables)
    assert core.store.read("personal_record", record["id"])["status"] == "forgotten"
    source_id = convert_legacy(NAMESPACE, tables)["records"][0]["source_id"]
    source = core.store.read("personal_source", source_id)
    assert source["status"] in {"forgotten", "deleted"} and source["text"] == ""
    assert "FORGOTTEN_FIXTURE_SENTINEL" not in json.dumps(stored(core, "personal_source"))


def test_invalid_converter_rows_and_public_legacy_scope_never_activate(core):
    tables = rows()
    tables["profilefact"][0]["confidence"] = True
    tables["profilefact"].append({"id": None, "fact_text": "Malformed statement"})
    report = reconcile(core, NAMESPACE, tables)
    assert report["quarantined"] >= 2
    assert not imported(core, "personal_record")
    journal = next(item for item in imported(core, "personal_source") if item["source_kind"] == "legacy_journal")
    assert journal["scope"] == "owner_private"
    assert journal["legacy_metadata"]["privacy_scope"] == "local_generic"
    with core.store.transaction() as db:
        assert core.personal.recall(db, "workshop jasmine drink") == []


def test_imported_account_can_be_unlinked_through_real_owner_control(core):
    reconcile(core, NAMESPACE, rows())
    account = imported(core, "account")[0]
    result = core.personal_control("unlink-imported", "unlink", {
        "person_id": account["person_id"], "platform": account["platform"],
        "platform_user_id": account["platform_user_id"],
    })
    assert result["status"] == "unlinked"
    assert all(item["status"] != "active" for item in imported(core, "account"))
    reconcile(core, NAMESPACE, rows())
    assert all(item["status"] != "active" for item in imported(core, "account"))


def test_owner_unlink_tombstone_blocks_new_legacy_primary_key_for_same_tuple(core):
    tables = rows()
    reconcile(core, NAMESPACE, tables)
    account = imported(core, "account")[0]
    # Use the existing explicit link/unlink controls, also exercising the tuple binding.
    values = {"person_id": account["person_id"], "platform": account["platform"],
              "platform_user_id": account["platform_user_id"], "confirmed": True}
    core.personal_control("confirm-tuple", "link", values)
    core.personal_control("unlink-tuple", "unlink", values)
    tables["personidentity"][0]["id"] = 99
    reconcile(core, NAMESPACE, tables)
    matching = [item for item in stored(core, "account") if item.get("platform") == "discord"
                and item.get("platform_user_id") == "fixture-123"]
    assert not any(item["status"] == "active" for item in matching)


def test_deleting_imported_person_invalidates_dependent_account(core):
    reconcile(core, NAMESPACE, rows())
    person = imported(core, "person")[0]
    core.personal_control("delete-person", "delete", {"target_id": person["source_id"]})
    assert core.store.read("person", person["id"])["status"] == "deleted"
    assert all(item["status"] != "active" for item in imported(core, "account"))
    reconcile(core, NAMESPACE, rows())
    assert core.store.read("person", person["id"])["status"] == "deleted"
    assert all(item["status"] != "active" for item in imported(core, "account"))


@pytest.mark.parametrize("table", ["workingstatecheckpoint", "scopedworkingstatecheckpoint", "stream_self_goals"])
def test_deleted_checkpoint_does_not_return_as_active_source_or_commitment(core, table):
    tables = {table: [{"id": 7, "summary": "CHECKPOINT_REMOVAL_SENTINEL unfinished plan", "created_at": "old"}]}
    reconcile(core, NAMESPACE, tables)
    record = imported(core, "personal_record")
    # Checkpoints may use legacy_ref rather than legacy_table in their canonical projection.
    if not record:
        record = [item for item in stored(core, "personal_record") if item.get("source_kind") == "legacy_checkpoint"]
    assert len(record) == 1
    core.personal_control("delete-checkpoint", "delete", {"target_id": record[0]["id"]})
    tables[table][0]["summary"] += " changed externally"
    reconcile(core, NAMESPACE, tables)
    matching = [item for item in stored(core, "personal_source") if item.get("legacy_ref", {}).get("table") == table]
    assert not any(item["status"] == "active" for item in matching)
    assert "CHECKPOINT_REMOVAL_SENTINEL" not in json.dumps(stored(core, "personal_source"))


def test_malformed_checkpoint_rows_are_quarantined_without_activating_them(core):
    report = reconcile(core, NAMESPACE, {"workingstatecheckpoint": [{"summary": "Missing primary key"}]})
    assert not any(item["source_kind"] == "legacy_checkpoint" and item["status"] == "active"
                   for item in stored(core, "personal_source"))
    assert not any(item.get("kind") == "commitment" for item in stored(core, "personal_record"))
    assert report["quarantined"] >= 1


def test_modified_legacy_authored_file_cannot_overwrite_later_owner_edit(core):
    reconcile(core, NAMESPACE, {}, auxiliary={"config/persona.yaml": {"speech_style": "Legacy style"}})
    core.personal_control("new-authored-style", "identity", {"fields": {"speech_style": "Current owner style"}})
    current = deepcopy(core.personal_state()["identity"])
    report = reconcile(core, NAMESPACE, {}, auxiliary={"config/persona.yaml": {"speech_style": "Externally changed legacy style"}})
    assert hashlib.sha256(json.dumps(core.personal_state()["identity"], sort_keys=True).encode()).digest() == \
        hashlib.sha256(json.dumps(current, sort_keys=True).encode()).digest()
    assert report["conflicts"] >= 1


def test_deleted_auxiliary_file_payload_cannot_be_reimported_with_new_hash(core):
    relative = "data/assistant_state.json"
    reconcile(core, NAMESPACE, {}, auxiliary={relative: {"working_note": "AUX_REMOVAL_SENTINEL"}})
    source = next(item for item in stored(core, "personal_source") if item.get("origin_kind") == relative)
    core.personal_control("delete-aux", "delete", {"target_id": source["id"]})
    reconcile(core, NAMESPACE, {}, auxiliary={relative: {"working_note": "AUX_REMOVAL_SENTINEL changed"}})
    assert "AUX_REMOVAL_SENTINEL" not in json.dumps(stored(core, "personal_source"))
    assert not any(item["status"] == "active" and item.get("origin_kind") == relative
                   for item in stored(core, "personal_source"))


def test_import_transaction_does_not_commit_partial_rows(core, monkeypatch):
    before = {kind: stored(core, kind) for kind in ("personal_source", "personal_record", "person", "account")}
    original = core.store.put

    def fail(db, kind, key, body):
        if kind == "person":
            raise RuntimeError("injected importer failure")
        return original(db, kind, key, body)

    monkeypatch.setattr(core.store, "put", fail)
    with pytest.raises(RuntimeError, match="injected importer failure"):
        reconcile(core, NAMESPACE, rows())
    after = {kind: stored(core, kind) for kind in before}
    assert hashlib.sha256(json.dumps(after, sort_keys=True).encode()).digest() == \
        hashlib.sha256(json.dumps(before, sort_keys=True).encode()).digest()


def snapshot(root):
    root.mkdir(mode=0o700)
    database = root / "gamma.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE profilefact (id INTEGER PRIMARY KEY, fact_text TEXT, subject_type TEXT)")
        db.execute("INSERT INTO profilefact VALUES (1,'A fictional snapshot claim','primary_user')")
    manifest = {"database": {"snapshot_path": str(database), "snapshot_sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
                             "source_path": "/unavailable-production-source/gamma.db"}, "copied_files": []}
    (root / "manifest.json").write_text(json.dumps(manifest))
    return manifest


def test_snapshot_reader_uses_only_the_copy_and_rejects_database_hash_mismatch(core, tmp_path):
    root = tmp_path / "copy"
    manifest = snapshot(root)
    assert reconcile_snapshot(core, root, namespace=NAMESPACE)["records_added"] == 1
    manifest["database"]["snapshot_sha256"] = "0" * 64
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="hash|digest|integrity"):
        reconcile_snapshot(core, root, namespace=NAMESPACE)


def test_snapshot_reader_rejects_auxiliary_hash_mismatch_before_writing(core, tmp_path):
    root = tmp_path / "copy"
    manifest = snapshot(root)
    auxiliary = root / "assistant_state.json"
    auxiliary.write_text('{"note":"changed fixture"}')
    manifest["copied_files"] = [{"relative_path": "data/assistant_state.json", "snapshot_path": str(auxiliary), "sha256": "0" * 64}]
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="hash|digest|integrity"):
        reconcile_snapshot(core, root, namespace=NAMESPACE)
    assert not imported(core, "personal_record")


def test_snapshot_reader_rejects_paths_outside_copy_before_writing(core, tmp_path):
    root = tmp_path / "copy"
    manifest = snapshot(root)
    manifest["database"]["snapshot_path"] = str(tmp_path / "outside.db")
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="outside"):
        reconcile_snapshot(core, root, namespace=NAMESPACE)
    assert not imported(core, "personal_record")


def test_current_confirmed_account_conflict_quarantines_legacy_identity_source(core):
    current_person = core.personal_control("current-person", "person", {"name": "Current person"})["person_id"]
    core.personal_control("current-account", "link", {"person_id": current_person, "platform": "discord",
        "platform_user_id": "fixture-123", "confirmed": True})
    report = reconcile(core, NAMESPACE, rows())
    source = next(item for item in imported(core, "personal_source") if item.get("legacy_table") == "personidentity")
    assert report["conflicts"] >= 1
    assert source["status"] == "quarantined"
    with core.store.transaction() as db:
        assert source["id"] not in {item["id"] for item in core.personal.recall(db, "Mira")}


def test_deleting_imported_sources_scrubs_all_person_and_account_payload_metadata(core):
    tables = rows()
    tables["knownperson"][0]["trust"] = "REMOVED_TRUST_FIXTURE_SENTINEL"
    tables["personidentity"][0]["display_name"] = "REMOVED_DISPLAY_FIXTURE_SENTINEL"
    reconcile(core, NAMESPACE, tables)
    person = imported(core, "person")[0]
    core.personal_control("delete-imported-person", "delete", {"target_id": person["source_id"]})
    state = json.dumps(core.personal_state())
    assert "REMOVED_TRUST_FIXTURE_SENTINEL" not in state
    assert "REMOVED_DISPLAY_FIXTURE_SENTINEL" not in state


@pytest.mark.parametrize("table,row,expected_pk", [
    ("rollingsessionsummary", {"session_id": "session-a", "summary": "Summary"}, "session-a"),
    ("workingstatecheckpoint", {"session_id": "session-b", "goal": "Unfinished"}, "session-b"),
    ("scopedsessionsummary", {"session_id": "session-c", "privacy_scope": "local_generic", "summary": "Summary"},
     ["session-c", "local_generic"]),
    ("scopedworkingstatecheckpoint", {"session_id": "session-d", "privacy_scope": "local_private", "goal": "Unfinished"},
     ["session-d", "local_private"]),
    ("stream_self_goals", {"id": 3, "goal": "Unfinished"}, 3),
])
def test_real_checkpoint_and_summary_primary_keys_are_preserved_and_idempotent(core, table, row, expected_pk):
    report = reconcile(core, NAMESPACE, {table: [row]})
    source = next(item for item in imported(core, "personal_source") if item.get("origin_kind") == table)
    assert source["legacy_ref"] == {"namespace": NAMESPACE, "table": table, "id": expected_pk}
    assert source["scope"] == "owner_private" and source["certainty"] == "legacy_unverified"
    repeated = reconcile(core, NAMESPACE, {table: [row]})
    assert repeated["sources_added"] == repeated["records_added"] == 0
    assert repeated["migration_id"] == report["migration_id"]
    records = [item for item in imported(core, "personal_record") if item.get("source_id") == source["id"]]
    if "checkpoint" in table or table == "stream_self_goals":
        assert len(records) == 1
        assert records[0]["legacy_ref"] == source["legacy_ref"]
        assert records[0]["status"] == "pending" and records[0]["execution_authority"] is False
    else:
        assert records == []


def test_scoped_checkpoint_tombstone_does_not_cross_scopes(core):
    table = "scopedworkingstatecheckpoint"
    tables = {table: [
        {"session_id": "same-session", "privacy_scope": "local_generic", "goal": "Generic unfinished work"},
        {"session_id": "same-session", "privacy_scope": "local_private", "goal": "Private unfinished work"},
    ]}
    reconcile(core, NAMESPACE, tables)
    records = imported(core, "personal_record")
    removed = next(item for item in records if item["legacy_ref"]["id"][1] == "local_private")
    other = next(item for item in records if item["legacy_ref"]["id"][1] == "local_generic")
    core.personal_control("delete-private-checkpoint", "delete", {"target_id": removed["id"]})
    for row in tables[table]:
        row["goal"] += " changed"
    reconcile(core, NAMESPACE, tables)
    assert core.store.read("personal_record", removed["id"])["status"] == "deleted"
    assert core.store.read("personal_record", other["id"])["status"] == "pending"
    sources = imported(core, "personal_source")
    assert all(item["status"] in {"forgotten", "deleted"} for item in sources
               if item["legacy_ref"]["id"] == ["same-session", "local_private"])
    assert any(item["status"] == "active" for item in sources
               if item["legacy_ref"]["id"] == ["same-session", "local_generic"])


@pytest.mark.parametrize("bad_id", [[], ["forged", "scope"], " \n", -1, True, 1.0])
def test_malformed_checkpoint_primary_keys_are_quarantined(core, bad_id):
    report = reconcile(core, NAMESPACE, {"stream_self_goals": [{"id": bad_id, "goal": "Untrusted unfinished goal"}]})
    assert report["quarantined"] >= 1
    assert not imported(core, "personal_record")
    assert not any(item["status"] == "active" for item in imported(core, "personal_source"))


def test_auxiliary_parser_and_hash_check_use_the_same_bytes(core, tmp_path, monkeypatch):
    root = tmp_path / "copy"
    manifest = snapshot(root)
    auxiliary = root / "assistant_state.json"
    before = b'{"note":"unverified old bytes"}'
    after = b'{"note":"verified new bytes"}'
    auxiliary.write_bytes(before)
    manifest["copied_files"] = [{"relative_path": "data/assistant_state.json", "snapshot_path": str(auxiliary),
                                 "sha256": hashlib.sha256(after).hexdigest()}]
    (root / "manifest.json").write_text(json.dumps(manifest))
    original = Path.read_text

    def concurrent_change(path, *args, **kwargs):
        text = original(path, *args, **kwargs)
        if path == auxiliary:
            auxiliary.write_bytes(after)
        return text

    monkeypatch.setattr(Path, "read_text", concurrent_change)
    with pytest.raises(ValueError, match="hash|digest|integrity"):
        reconcile_snapshot(core, root, namespace=NAMESPACE)
    assert not imported(core, "personal_record")


def test_distinct_auxiliary_and_checkpoint_imports_keep_distinct_migration_audits(core):
    first = reconcile(core, NAMESPACE, {"workingstatecheckpoint": [{"session_id": "fixture-session", "goal": "First goal"}]},
                      auxiliary={"data/assistant_state.json": {"note": "First note"}})
    second = reconcile(core, NAMESPACE, {"workingstatecheckpoint": [{"session_id": "fixture-session", "goal": "Changed goal"}]},
                       auxiliary={"data/assistant_state.json": {"note": "Changed note"}})
    assert first["migration_id"] != second["migration_id"]
    assert {item["migration_id"] for item in stored(core, "personal_migration")} == {first["migration_id"], second["migration_id"]}
