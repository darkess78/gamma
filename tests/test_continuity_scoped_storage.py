from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest
from sqlmodel import Session, select

from gamma.config import settings
from gamma.memory.continuity import (
    ContinuityService, ConversationJournalEntry, JournalOwnershipError,
    RollingSessionSummary, WorkingStateCheckpoint,
)


def owner(path):
    return ContinuityService(database_url=f"sqlite:///{path}")


def begin(service, scope, text="Fixture turn", session_id="mixed"):
    return service.begin_exchange(session_id=session_id, text=text, speaker_name="Fixture",
                                  input_source="test", privacy_scope=scope)


def finish(service, exchange, scope, *, session_id="mixed", objective=None, signals=None):
    updates = {}
    if objective is not None:
        updates.update(current_objective=objective, deferred_intention=objective + " next")
    if signals is not None:
        updates["relationship_signals"] = signals
    service.complete_exchange(exchange_id=exchange, session_id=session_id, assistant_text="",
                              trace_id=None, output_target="dashboard_monitor", privacy_scope=scope,
                              state_updates=updates)


def turn(service, scope, text="Fixture turn", **kwargs):
    exchange = begin(service, scope, text, kwargs.get("session_id", "mixed"))
    finish(service, exchange, scope, **kwargs)
    return exchange


def test_live_mixed_scope_summary_reproduction(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "conversation_summary_interval_exchanges", 5)
    service = owner(tmp_path / "state.db")
    turn(service, "local_private", "PRIVATE_SUMMARY_REPRO")
    turn(service, "public", "Public greeting")
    assert "PRIVATE_SUMMARY_REPRO" not in service.prompt_context("mixed", privacy_scopes={"public"})


def test_live_mixed_scope_checkpoint_reproduction(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "conversation_summary_interval_exchanges", 5)
    service = owner(tmp_path / "state.db")
    turn(service, "local_private", objective="PRIVATE_OBJECTIVE_REPRO", signals=["PRIVATE_PERSON_REPRO"])
    turn(service, "public", "Public greeting")
    context = service.prompt_context("mixed", privacy_scopes={"public"})
    assert "PRIVATE_OBJECTIVE_REPRO" not in context
    assert "PRIVATE_PERSON_REPRO" not in context


@pytest.mark.parametrize("other_scope", ["public", "local_generic"])
def test_other_scope_keeps_private_continuation_and_own_fields(tmp_path, monkeypatch, other_scope):
    monkeypatch.setattr(settings, "conversation_summary_interval_exchanges", 4)
    service = owner(tmp_path / "state.db")
    turn(service, "local_private", "private text", objective="private goal", signals=["private person"])
    private = service.snapshot("mixed")
    turn(service, other_scope, "Other greeting")
    assert service.snapshot("mixed") == private
    other = service.snapshot("mixed", privacy_scope=other_scope)
    assert other["working_state"]["current_objective"] == ""
    assert other["working_state"]["relevant_people"] == []
    assert other["summary"]["completed_exchanges"] == 1
    turn(service, other_scope, "Other continuation", objective="other goal", signals=["other person"])
    other_before = service.snapshot("mixed", privacy_scope=other_scope)
    turn(service, "local_private", "Private followup")
    assert service.snapshot("mixed", privacy_scope=other_scope) == other_before
    private_after = service.snapshot("mixed")["working_state"]
    assert private_after["current_objective"] == "private goal"
    assert private_after["relevant_people"] == ["private person"]
    assert private_after["next_intended_action"] == "private goal next"


def test_scope_counts_and_refresh_boundaries_survive_retention(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "conversation_summary_interval_exchanges", 3)
    monkeypatch.setattr(settings, "conversation_journal_max_exchanges_per_session", 2)
    service = owner(tmp_path / "state.db")
    turn(service, "local_private", "P1")
    turn(service, "public", "U1")
    first = service.snapshot("mixed", privacy_scope="public")["summary"]
    turn(service, "local_private", "P2")
    turn(service, "public", "U2")
    second = service.snapshot("mixed", privacy_scope="public")["summary"]
    assert second["completed_exchanges"] == 2
    assert second["summary_text"] == first["summary_text"]
    assert second["through_entry_id"] == first["through_entry_id"]
    turn(service, "local_private", "P3")
    assert service.snapshot("mixed")["summary"]["completed_exchanges"] == 3
    assert "P3" in service.snapshot("mixed")["summary"]["summary_text"]
    assert service.snapshot("mixed", privacy_scope="public")["summary"] == second
    turn(service, "public", "U3")
    refreshed = service.snapshot("mixed", privacy_scope="public")["summary"]
    assert refreshed["completed_exchanges"] == 3
    assert "U3" in refreshed["summary_text"] and "P" not in refreshed["summary_text"]
    for n in (4, 5, 6):
        turn(service, "public", f"U{n}")
    assert service.snapshot("mixed", privacy_scope="public")["summary"]["completed_exchanges"] == 6
    assert "U6" in service.snapshot("mixed", privacy_scope="public")["summary"]["summary_text"]
    assert len(service.recent_turns("mixed", privacy_scopes={"public"})) == 2
    assert len(service.recent_turns("mixed", privacy_scopes={"local_private"})) == 2


def test_concurrent_scope_writers_use_real_sqlite_transactions(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "conversation_summary_interval_exchanges", 2)
    path = tmp_path / "concurrent.db"
    a, b = owner(path), owner(path)
    barrier = Barrier(2)
    def writer(service, scope):
        for n in range(4):
            exchange = begin(service, scope, f"{scope} turn {n}")
            barrier.wait(timeout=10)
            finish(service, exchange, scope, objective=f"{scope} goal {n}", signals=[scope])
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(writer, a, "local_private"), pool.submit(writer, b, "public")]
        for future in futures:
            future.result(timeout=30)
    for scope in ("local_private", "public"):
        snapshot = a.snapshot("mixed", privacy_scope=scope)
        assert snapshot["summary"]["completed_exchanges"] == 4
        assert snapshot["working_state"]["current_objective"] == f"{scope} goal 3"
        assert snapshot["working_state"]["relevant_people"] == [scope]
    assert "local_private goal" not in a.prompt_context("mixed", privacy_scopes={"public"})


def test_legacy_mislabeled_rows_are_private_only_and_unchanged(tmp_path):
    service = owner(tmp_path / "legacy.db")
    with Session(service._engine) as db:
        db.add(RollingSessionSummary(session_id="mixed", summary_text="LEGACY_PRIVATE_SUMMARY",
                                    privacy_scope="public", completed_exchanges=7))
        db.add(WorkingStateCheckpoint(session_id="mixed", current_objective="LEGACY_PRIVATE_GOAL",
                                      privacy_scope="public", relevant_people_json='["legacy person"]'))
        db.commit()
    assert service.snapshot("mixed")["summary"]["privacy_scope"] == "local_private"
    assert "LEGACY_PRIVATE_SUMMARY" in service.prompt_context("mixed", privacy_scopes={"local_private"})
    for scope in ("public", "local_generic", "unknown"):
        assert service.snapshot("mixed", privacy_scope=scope) == {
            "session_id": "mixed", "summary": None, "working_state": None}
        assert "LEGACY_PRIVATE" not in service.prompt_context("mixed", privacy_scopes={scope})
    turn(service, "public", "Public hello")
    assert "LEGACY_PRIVATE" not in service.prompt_context("mixed", privacy_scopes={"public"})
    turn(service, "local_private", "Private continuation")
    assert service.snapshot("mixed")["working_state"]["current_objective"] == "LEGACY_PRIVATE_GOAL"
    with Session(service._engine) as db:
        assert db.get(RollingSessionSummary, "mixed").summary_text == "LEGACY_PRIVATE_SUMMARY"
        assert db.get(WorkingStateCheckpoint, "mixed").privacy_scope == "public"
    assert service.snapshot("mixed", privacy_scope="public")["working_state"]["current_objective"] == ""


@pytest.mark.parametrize("scope", ["public", "local_private"])
def test_scoped_terminal_rollback_foreign_duplicate_and_stale_guards(tmp_path, monkeypatch, scope):
    service = owner(tmp_path / "atomic.db")
    other = owner(tmp_path / "atomic.db")
    turn(service, "local_private", "private initial", objective="PRIVATE_KEEP")
    turn(service, "public", "public initial", objective="PUBLIC_KEEP")
    before = {s: service.snapshot("mixed", privacy_scope=s) for s in ("public", "local_private")}
    exchange = begin(service, scope)
    with pytest.raises(JournalOwnershipError):
        finish(other, exchange, scope, objective="FOREIGN")
    original = service._maybe_update_summary_locked
    def write_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("after scoped state writes")
    with monkeypatch.context() as patch:
        patch.setattr(service, "_maybe_update_summary_locked", write_then_fail)
        with pytest.raises(RuntimeError, match="after scoped"):
            finish(service, exchange, scope, objective="ROLLBACK")
    with Session(service._engine) as db:
        rows = list(db.exec(select(ConversationJournalEntry).where(
            ConversationJournalEntry.exchange_id == exchange)))
        assert len(rows) == 1 and rows[0].completion_status == "pending"
    assert {s: service.snapshot("mixed", privacy_scope=s) for s in before} == before
    finish(service, exchange, scope, objective="COMMITTED")
    after = {s: service.snapshot("mixed", privacy_scope=s) for s in before}
    newer = begin(service, scope)
    with pytest.raises(JournalOwnershipError):
        finish(service, exchange, scope, objective="DUPLICATE")
    assert {s: service.snapshot("mixed", privacy_scope=s) for s in before} == after
    assert service.fail_exchange(newer, status="cancelled")
    with pytest.raises(JournalOwnershipError):
        finish(service, newer, scope, objective="STALE")
    assert {s: service.snapshot("mixed", privacy_scope=s) for s in before} == after


def test_retention_preserves_pending_scope_then_removes_orphan_state_and_deletion(tmp_path, monkeypatch):
    from gamma.memory.continuity import ScopedSessionSummary, ScopedWorkingStateCheckpoint

    monkeypatch.setattr(settings, "conversation_journal_retention_days", 1)
    service = owner(tmp_path / "retention.db")
    turn(service, "local_private", objective="PRIVATE_PENDING_OBJECTIVE")
    turn(service, "public", objective="PUBLIC_EXPIRED_OBJECTIVE")
    pending = begin(service, "local_private", "Quiet pending")
    old = datetime.now(timezone.utc) - timedelta(days=3)
    with Session(service._engine) as db:
        for row in db.exec(select(ConversationJournalEntry)):
            row.occurred_at = old
            db.add(row)
        for model in (ScopedSessionSummary, ScopedWorkingStateCheckpoint):
            for row in db.exec(select(model)):
                row.updated_at = old
                db.add(row)
        db.add(RollingSessionSummary(session_id="mixed", summary_text="OLD_LEGACY", updated_at=old))
        db.commit()
    service.prune()
    assert service.snapshot("mixed", privacy_scope="public")["summary"] is None
    assert service.snapshot("mixed", privacy_scope="public")["working_state"] is None
    assert service.snapshot("mixed")["working_state"]["current_objective"] == "PRIVATE_PENDING_OBJECTIVE"
    with Session(service._engine) as db:
        assert db.get(RollingSessionSummary, "mixed") is not None
        assert list(db.exec(select(ConversationJournalEntry)))[0].completion_status == "pending"
    assert service.fail_exchange(pending)
    service.prune()
    assert service.snapshot("mixed")["summary"] is None
    assert service.snapshot("mixed")["working_state"] is None
    turn(service, "local_private")
    turn(service, "public")
    begin(service, "public")
    assert service.delete_session("mixed") == 3
    for scope in ("local_private", "public", "local_generic"):
        assert service.snapshot("mixed", privacy_scope=scope)["working_state"] is None
        assert service.prompt_context("mixed", privacy_scopes={scope}) == ""


def test_old_pending_silent_completion_retains_fresh_scoped_state(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "conversation_journal_retention_days", 1)
    service = owner(tmp_path / "long-work.db")
    exchange = begin(service, "local_private", "Long quiet work")
    with Session(service._engine) as db:
        row = db.exec(select(ConversationJournalEntry)).one()
        row.occurred_at = datetime.now(timezone.utc) - timedelta(days=3)
        db.add(row)
        db.commit()
    finish(service, exchange, "local_private", objective="Quiet completed objective")
    assert service.recent_turns("mixed", privacy_scopes={"local_private"}) == []
    assert service.snapshot("mixed")["working_state"]["current_objective"] == "Quiet completed objective"
    assert service.snapshot("mixed")["summary"]["completed_exchanges"] == 1
    assert "Long quiet work" in service.snapshot("mixed")["summary"]["summary_text"]


def summary_completed_ids(service, session_id, scope):
    with Session(service._engine) as db:
        return list(db.exec(select(ConversationJournalEntry.id).where(
            ConversationJournalEntry.session_id == session_id,
            ConversationJournalEntry.privacy_scope == scope,
            ConversationJournalEntry.completion_status == "completed",
        )))


def finish_answered(service, exchange, scope, text, session_id="mixed"):
    service.complete_exchange(exchange_id=exchange, session_id=session_id, assistant_text=text,
                              trace_id=None, output_target="dashboard_monitor", privacy_scope=scope)


@pytest.mark.parametrize("scope", ["local_private", "public", "local_generic"])
def test_summary_coverage_tracks_selected_rows_after_older_quiet_completion(tmp_path, monkeypatch, scope):
    monkeypatch.setattr(settings, "conversation_summary_interval_exchanges", 1)
    service = owner(tmp_path / "coverage.db")
    older = begin(service, scope, "OLDER_QUIET_USER")
    newer = begin(service, scope, "NEWER_ANSWERED_USER")
    finish_answered(service, newer, scope, "NEWER_ASSISTANT_REPLY")
    first = service.snapshot("mixed", privacy_scope=scope)["summary"]
    assert "OLDER_QUIET_USER" not in first["summary_text"]
    assert "NEWER_ASSISTANT_REPLY" in first["summary_text"]
    finish(service, older, scope)
    summary = service.snapshot("mixed", privacy_scope=scope)["summary"]
    ids = summary_completed_ids(service, "mixed", scope)
    assert "OLDER_QUIET_USER" in summary["summary_text"]
    assert "NEWER_ASSISTANT_REPLY" in summary["summary_text"]
    assert summary["through_entry_id"] == max(ids) == first["through_entry_id"]
    assert summary["completed_exchanges"] == 2
    with Session(service._engine) as db:
        rows = list(db.exec(select(ConversationJournalEntry).where(
            ConversationJournalEntry.exchange_id == older)))
        assert len(rows) == 1 and rows[0].role == "user"


def test_summary_coverage_for_ordinary_answered_and_quiet_completions(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "conversation_summary_interval_exchanges", 1)
    service = owner(tmp_path / "ordinary.db")
    exchange = begin(service, "public", "ORDINARY_USER")
    finish_answered(service, exchange, "public", "ORDINARY_REPLY")
    summary = service.snapshot("mixed", privacy_scope="public")["summary"]
    assert summary["through_entry_id"] == max(summary_completed_ids(service, "mixed", "public"))
    assert summary["summary_text"] == "user: ORDINARY_USER\nassistant: ORDINARY_REPLY"
    turn(service, "public", "ORDINARY_QUIET")
    next_summary = service.snapshot("mixed", privacy_scope="public")["summary"]
    assert next_summary["through_entry_id"] == max(summary_completed_ids(service, "mixed", "public"))
    assert next_summary["through_entry_id"] > summary["through_entry_id"]
    assert next_summary["completed_exchanges"] == 2


def test_summary_interval_preserves_text_and_marker_until_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "conversation_summary_interval_exchanges", 3)
    service = owner(tmp_path / "interval.db")
    older = begin(service, "public", "INTERVAL_OLDER_USER")
    newer = begin(service, "public", "INTERVAL_NEWER_USER")
    finish_answered(service, newer, "public", "INTERVAL_NEWER_REPLY")
    first = service.snapshot("mixed", privacy_scope="public")["summary"]
    finish(service, older, "public")
    skipped = service.snapshot("mixed", privacy_scope="public")["summary"]
    assert skipped["summary_text"] == first["summary_text"]
    assert skipped["through_entry_id"] == first["through_entry_id"]
    assert skipped["completed_exchanges"] == 2
    assert "INTERVAL_OLDER_USER" not in skipped["summary_text"]
    turn(service, "public", "INTERVAL_REFRESH")
    refreshed = service.snapshot("mixed", privacy_scope="public")["summary"]
    assert refreshed["completed_exchanges"] == 3
    assert "INTERVAL_OLDER_USER" in refreshed["summary_text"]
    assert "INTERVAL_REFRESH" in refreshed["summary_text"]
    assert refreshed["through_entry_id"] == max(summary_completed_ids(service, "mixed", "public"))


def test_summary_marker_excludes_larger_other_scope_session_and_pending_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "conversation_summary_interval_exchanges", 1)
    service = owner(tmp_path / "isolation.db")
    own = begin(service, "public", "OWN_SELECTED_USER")
    private = begin(service, "local_private", "PRIVATE_OTHER_SCOPE")
    finish_answered(service, private, "local_private", "PRIVATE_OTHER_REPLY")
    other = begin(service, "public", "OTHER_SESSION_USER", session_id="other")
    finish_answered(service, other, "public", "OTHER_SESSION_REPLY", session_id="other")
    begin(service, "public", "PENDING_NOT_SELECTED")
    finish(service, own, "public")
    summary = service.snapshot("mixed", privacy_scope="public")["summary"]
    assert summary["summary_text"] == "user: OWN_SELECTED_USER"
    assert summary["through_entry_id"] == max(summary_completed_ids(service, "mixed", "public"))
    assert summary["through_entry_id"] < max(summary_completed_ids(service, "mixed", "local_private"))
    assert summary["through_entry_id"] < max(summary_completed_ids(service, "other", "public"))
    assert service.snapshot("mixed", privacy_scope="local_private")["summary"]["completed_exchanges"] == 1
    assert service.snapshot("other", privacy_scope="public")["summary"]["completed_exchanges"] == 1


def test_summary_marker_can_decrease_after_retention_and_sqlite_id_reuse(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "conversation_summary_interval_exchanges", 1)
    monkeypatch.setattr(settings, "conversation_journal_retention_days", 1)
    service = owner(tmp_path / "reuse.db")
    exchange = begin(service, "public", "EXPIRED_USER")
    finish_answered(service, exchange, "public", "EXPIRED_REPLY")
    previous = service.snapshot("mixed", privacy_scope="public")["summary"]
    with Session(service._engine) as db:
        for row in db.exec(select(ConversationJournalEntry)):
            row.occurred_at = datetime.now(timezone.utc) - timedelta(days=3)
            db.add(row)
        db.commit()
    service.prune()
    assert summary_completed_ids(service, "mixed", "public") == []
    assert service.snapshot("mixed", privacy_scope="public")["summary"]["through_entry_id"] == previous["through_entry_id"]
    turn(service, "public", "REUSED_ID_USER")
    summary = service.snapshot("mixed", privacy_scope="public")["summary"]
    assert summary["summary_text"] == "user: REUSED_ID_USER"
    assert summary["through_entry_id"] == max(summary_completed_ids(service, "mixed", "public"))
    assert summary["through_entry_id"] < previous["through_entry_id"]
    assert summary["completed_exchanges"] == 2


def test_summary_empty_selected_rows_have_no_coverage_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "conversation_summary_interval_exchanges", 1)
    service = owner(tmp_path / "empty.db")
    turn(service, "local_private", "UNRELATED_COMPLETED")
    begin(service, "public", "ONLY_PENDING", session_id="empty")
    with service._write_session() as db:
        service._maybe_update_summary_locked(db, session_id="empty", privacy_scope="public", through_entry_id=999)
    summary = service.snapshot("empty", privacy_scope="public")["summary"]
    assert summary["summary_text"] == ""
    assert summary["through_entry_id"] is None
    assert summary["completed_exchanges"] == 0
