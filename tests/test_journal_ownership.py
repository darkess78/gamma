from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlmodel import Session, select

from gamma.memory.continuity import (
    ContinuityService, ConversationJournalEntry, ConversationJournalOwner,
    JournalOwnershipError,
)


def service(path):
    return ContinuityService(database_url=f"sqlite:///{path}")


def begin(owner, name="one"):
    return owner.begin_exchange(session_id=name, text="Synthetic question?", speaker_name="Fixture",
                                input_source="test", privacy_scope="local_private")


def complete(owner, exchange, name="one", text="Synthetic reply"):
    owner.complete_exchange(exchange_id=exchange, session_id=name, assistant_text=text,
                            trace_id=None, output_target="dashboard_monitor", privacy_scope="local_private")


def journal(owner, exchange):
    with Session(owner._engine) as session:
        return list(session.exec(select(ConversationJournalEntry).where(
            ConversationJournalEntry.exchange_id == exchange)))


def test_constructor_and_explicit_reconciliation_preserve_live_owners(tmp_path):
    first = service(tmp_path / "state.db")
    exchange = begin(first)
    other = service(tmp_path / "state.db")
    assert other.reconcile_interrupted(limit=1) == 0
    assert [x.completion_status for x in journal(first, exchange)] == ["pending"]
    complete(first, exchange)


@pytest.mark.parametrize("terminal_status", ["cancelled", "interrupted"])
def test_foreign_terminal_and_duplicate_completion_have_no_durable_effects(tmp_path, terminal_status):
    first, other = service(tmp_path / "state.db"), service(tmp_path / "state.db")
    exchange = begin(first)
    before = first.snapshot("one")
    first.record_output(target_policy="dashboard_monitor", turn_id="saved", text="Saved", status="completed", spoken=True)
    output = first.last_output("dashboard_monitor")
    with pytest.raises(JournalOwnershipError):
        complete(other, exchange)
    assert other.fail_exchange(exchange, status="cancelled") is False
    assert first.snapshot("one") == before
    assert first.fail_exchange(exchange, status=terminal_status) is True
    with pytest.raises(JournalOwnershipError):
        complete(first, exchange)
    assert first.snapshot("one") == before
    assert first.last_output("dashboard_monitor") == output
    assert [x.completion_status for x in journal(first, exchange)] == [terminal_status]
    fresh = begin(first, "fresh")
    complete(first, fresh, "fresh")
    saved = first.snapshot("fresh")
    with pytest.raises(JournalOwnershipError):
        complete(first, fresh, "fresh")
    assert len(journal(first, fresh)) == 2
    assert first.snapshot("fresh") == saved


def test_concurrent_duplicate_calls_commit_once(tmp_path):
    first = service(tmp_path / "state.db")
    exchange = begin(first)
    def attempt():
        try:
            complete(first, exchange)
            return "completed"
        except JournalOwnershipError:
            return "rejected"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: attempt(), range(2))) == ["completed", "rejected"]
    assert [x.role for x in journal(first, exchange)] == ["user", "assistant"]
    assert first.snapshot("one")["summary"]["completed_exchanges"] == 1


def test_completed_turn_replay_cannot_change_context_while_newer_turn_is_pending(tmp_path):
    owner = service(tmp_path / "replay.db")
    first = begin(owner)
    complete(owner, first, text="Original A reply")
    newer = owner.begin_exchange(
        session_id="one", text="Synthetic newer question?", speaker_name="Fixture",
        input_source="test", privacy_scope="local_private",
    )
    # Starting B registers this service's ownership again after A's cleanup.
    def communicated_turns():
        return [(turn.role, turn.text) for turn in owner.recent_turns(
            "one", privacy_scopes={"local_private"}, token_budget=4000,
        )]
    before_turns = communicated_turns()
    before_context = owner.prompt_context("one", privacy_scopes={"local_private"}, token_budget=4000)
    before_snapshot = owner.snapshot("one")
    assert before_turns == [("user", "Synthetic question?"), ("assistant", "Original A reply")]

    with pytest.raises(JournalOwnershipError):
        complete(owner, first, text="Unexpected replay of A")

    assert communicated_turns() == before_turns
    assert owner.prompt_context("one", privacy_scopes={"local_private"}, token_budget=4000) == before_context
    assert owner.snapshot("one") == before_snapshot
    assert [(turn.role, turn.text) for turn in journal(owner, first)] == before_turns
    assert [turn.completion_status for turn in journal(owner, newer)] == ["pending"]

    complete(owner, newer, text="Normal B reply")
    assert [turn.completion_status for turn in journal(owner, newer)] == ["completed", "completed"]
    assert communicated_turns() == before_turns + [
        ("user", "Synthetic newer question?"), ("assistant", "Normal B reply"),
    ]
    assert owner.snapshot("one")["summary"]["completed_exchanges"] == 2


@pytest.mark.parametrize("failure_point", ["_update_working_state_locked", "_maybe_update_summary_locked"])
def test_terminal_effects_rollback_together(tmp_path, monkeypatch, failure_point):
    owner = service(tmp_path / "state.db")
    exchange = begin(owner)
    before = owner.snapshot("one")
    def fail(*args, **kwargs):
        raise RuntimeError("injected persistence failure")
    with monkeypatch.context() as patch:
        patch.setattr(owner, failure_point, fail)
        with pytest.raises(RuntimeError, match="injected persistence"):
            complete(owner, exchange)
    assert [x.completion_status for x in journal(owner, exchange)] == ["pending"]
    assert owner.snapshot("one") == before
    complete(owner, exchange)
    assert len(journal(owner, exchange)) == 2


def test_scope_mismatch_rejected(tmp_path):
    owner = service(tmp_path / "state.db")
    exchange = begin(owner)
    with pytest.raises(JournalOwnershipError):
        complete(owner, exchange, name="wrong-session")
    assert [x.completion_status for x in journal(owner, exchange)] == ["pending"]


def test_wrong_privacy_scope_cannot_publish_or_advance_owned_pending_turn(tmp_path):
    owner = service(tmp_path / "privacy-scope.db")
    exchange = begin(owner)
    before = owner.snapshot("one")
    before_context = owner.prompt_context("one", privacy_scopes={"local_private"}, token_budget=4000)

    with pytest.raises(JournalOwnershipError):
        owner.complete_exchange(
            exchange_id=exchange, session_id="one", assistant_text="Wrong-scope reply",
            trace_id=None, output_target="dashboard_monitor", privacy_scope="public",
        )

    assert [turn.completion_status for turn in journal(owner, exchange)] == ["pending"]
    assert owner.snapshot("one") == before
    assert owner.prompt_context("one", privacy_scopes={"local_private"}, token_budget=4000) == before_context
    assert owner.recent_turns("one", privacy_scopes={"local_private", "public"}, token_budget=4000) == []
    complete(owner, exchange, text="Authorized private reply")
    assert [(turn.role, turn.text) for turn in owner.recent_turns(
        "one", privacy_scopes={"local_private"}, token_budget=4000,
    )] == [("user", "Synthetic question?"), ("assistant", "Authorized private reply")]
    assert owner.recent_turns("one", privacy_scopes={"public"}, token_budget=4000) == []


CHILD = """
import os,sys
from gamma.memory.continuity import ContinuityService
s=ContinuityService(database_url='sqlite:///'+sys.argv[1])
x=s.begin_exchange(session_id='child',text='Synthetic',speaker_name='Fixture',input_source='test',privacy_scope='local_private')
print(x,flush=True)
sys.stdin.readline()
os._exit(23)
"""


def test_actual_live_then_crashed_process_recovery_is_bounded_and_scoped(tmp_path):
    path = tmp_path / "state.db"
    core = service(path)
    saved = begin(core, "saved")
    complete(core, saved, "saved")
    snapshot = core.snapshot("saved")
    core.record_output(target_policy="dashboard_monitor", turn_id="saved", text="Saved", status="completed", spoken=True)
    output = core.last_output("dashboard_monitor")
    child = subprocess.Popen([sys.executable, "-B", "-c", CHILD, str(path)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        exchange = child.stdout.readline().strip()
        assert exchange
        live = begin(core, "live")
        assert core.reconcile_interrupted(limit=1) == 0
        assert [x.completion_status for x in journal(core, exchange)] == ["pending"]
        child.stdin.write("exit\n")
        child.stdin.flush()
        assert child.wait(timeout=10) == 23
        # Fair bounded reconciliation must reach the dead owner despite other live work.
        recovered = sum(core.reconcile_interrupted(limit=1) for _ in range(6))
        assert recovered == 1
        assert [x.completion_status for x in journal(core, exchange)] == ["interrupted"]
        assert [x.completion_status for x in journal(core, live)] == ["pending"]
        assert core.snapshot("saved") == snapshot
        assert core.last_output("dashboard_monitor") == output
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
        child.stdin.close()
        child.stdout.close()


@pytest.mark.parametrize("mutation", ["other-host", "pid-reused", "unverifiable"])
def test_unverifiable_ownership_never_recovers_from_age(tmp_path, monkeypatch, mutation):
    import psutil
    owner = service(tmp_path / "state.db")
    exchange = begin(owner)
    with Session(owner._engine) as session:
        record = session.get(ConversationJournalOwner, owner._owner_id)
        if mutation == "other-host":
            record.host = "synthetic-other-host"
        elif mutation == "pid-reused":
            record.process_started -= 100
        session.add(record)
        session.commit()
    if mutation == "unverifiable":
        def denied(*args, **kwargs):
            raise psutil.AccessDenied(os.getpid())
        monkeypatch.setattr(psutil, "Process", denied)
    assert owner.reconcile_interrupted(limit=100) == 0
    assert [x.completion_status for x in journal(owner, exchange)] == ["pending"]


def test_retention_preserves_pending_and_owner_cleanup_is_bounded(tmp_path):
    from datetime import datetime, timedelta, timezone
    owner = service(tmp_path / "state.db")
    exchange = begin(owner)
    with Session(owner._engine) as session:
        row = journal(owner, exchange)[0]
        row.occurred_at = datetime.now(timezone.utc) - timedelta(days=1000)
        session.add(row)
        session.commit()
    owner.prune()
    owner.reconcile_interrupted(limit=1)
    assert [x.completion_status for x in journal(owner, exchange)] == ["pending"]
    with Session(owner._engine) as session:
        assert session.get(ConversationJournalOwner, owner._owner_id) is not None
    assert owner.delete_session("one") == 1
    for _ in range(3):
        owner.reconcile_interrupted(limit=1)
    with Session(owner._engine) as session:
        assert list(session.exec(select(ConversationJournalOwner))) == []


def test_legacy_schema_migrates_without_claiming_pending_rows(tmp_path):
    path = tmp_path / "legacy.db"
    owner = service(path)
    saved = begin(owner, "saved")
    complete(owner, saved, "saved")
    pending = begin(owner, "legacy")
    snapshot = owner.snapshot("saved")
    with sqlite3.connect(path) as conn:
        # Rebuild the legacy table lacking the additive ownership column.
        fields = [row[1] for row in conn.execute("PRAGMA table_info(conversationjournalentry)") if row[1] != "owner_id"]
        definitions = []
        for row in conn.execute("PRAGMA table_info(conversationjournalentry)"):
            if row[1] != "owner_id":
                definitions.append(row[1] + " " + row[2] + (" PRIMARY KEY" if row[5] else ""))
        conn.execute("CREATE TABLE legacy_journal (" + ",".join(definitions) + ")")
        conn.execute("INSERT INTO legacy_journal SELECT " + ",".join(fields) + " FROM conversationjournalentry")
        conn.execute("DROP TABLE conversationjournalentry")
        conn.execute("ALTER TABLE legacy_journal RENAME TO conversationjournalentry")
        conn.execute("DROP TABLE conversationjournalowner")
    restored = service(path)
    assert restored.reconcile_interrupted(limit=100) == 0
    restored.prune()
    assert [x.completion_status for x in journal(restored, pending)] == ["pending"]
    assert restored.snapshot("saved") == snapshot
    assert [x.completion_status for x in journal(restored, saved)] == ["completed", "completed"]


def test_actual_crash_before_commit_rolls_back_all_terminal_effects(tmp_path):
    path = tmp_path / "crash-terminal.db"
    core = service(path)
    saved = begin(core, "saved")
    complete(core, saved, "saved")
    snapshot = core.snapshot("saved")
    code = """
import os,sys
from gamma.memory.continuity import ContinuityService
s=ContinuityService(database_url='sqlite:///'+sys.argv[1])
x=s.begin_exchange(session_id='crash-terminal',text='Synthetic',speaker_name='Fixture',input_source='test',privacy_scope='local_private')
print(x,flush=True)
original=s._maybe_update_summary_locked
def crash(session,**kwargs):
    original(session,**kwargs)
    session.flush()
    os._exit(23)
s._maybe_update_summary_locked=crash
s.complete_exchange(exchange_id=x,session_id='crash-terminal',assistant_text='Must roll back',trace_id=None,output_target='dashboard_monitor',privacy_scope='local_private')
"""
    child = subprocess.run([sys.executable, "-B", "-c", code, str(path)], capture_output=True, text=True, timeout=10)
    assert child.returncode == 23, child.stderr
    exchange = child.stdout.strip()
    assert [x.completion_status for x in journal(core, exchange)] == ["pending"]
    assert core.snapshot("crash-terminal")["summary"] is None
    assert core.snapshot("crash-terminal")["working_state"] is None
    live = begin(core, "live")
    assert core.reconcile_interrupted(limit=100) == 1
    assert [x.completion_status for x in journal(core, exchange)] == ["interrupted"]
    assert [x.completion_status for x in journal(core, live)] == ["pending"]
    assert core.snapshot("saved") == snapshot


def test_actual_concurrent_processes_keep_ownership_and_serialize_sqlite_writes(tmp_path):
    path = tmp_path / "concurrent.db"
    observer = service(path)
    code = """
import sys
from gamma.memory.continuity import ContinuityService,JournalOwnershipError
s=ContinuityService(database_url='sqlite:///'+sys.argv[1])
x=s.begin_exchange(session_id='shared',text='Synthetic',speaker_name='Fixture',input_source='test',privacy_scope='local_private')
print(x,flush=True)
foreign=sys.stdin.readline().strip()
try:
    s.complete_exchange(exchange_id=foreign,session_id='shared',assistant_text='Foreign',trace_id=None,output_target='dashboard_monitor',privacy_scope='local_private')
except JournalOwnershipError:
    pass
else:
    raise RuntimeError('Foreign commit succeeded')
s.complete_exchange(exchange_id=x,session_id='shared',assistant_text='Owned',trace_id=None,output_target='dashboard_monitor',privacy_scope='local_private')
"""
    children = [subprocess.Popen([sys.executable, "-B", "-c", code, str(path)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    try:
        exchanges = [child.stdout.readline().strip() for child in children]
        assert all(exchanges)
        assert observer.reconcile_interrupted(limit=100) == 0
        for index, child in enumerate(children):
            child.stdin.write(exchanges[1 - index] + "\n")
            child.stdin.flush()
        for child in children:
            assert child.wait(timeout=10) == 0, child.stderr.read()
        for exchange in exchanges:
            assert [x.role for x in journal(observer, exchange)] == ["user", "assistant"]
        assert observer.snapshot("shared")["summary"]["completed_exchanges"] == 2
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
            child.stdin.close()
            child.stdout.close()
            child.stderr.close()


def test_registry_cleanup_is_bounded_and_preserves_active_pending_ownership(tmp_path):
    owner = service(tmp_path / "bounded.db")
    active = begin(owner)
    with Session(owner._engine) as session:
        for index in range(5):
            session.add(ConversationJournalOwner(owner_id=f"orphan-{index}", host="synthetic", pid=1, process_started=1.0))
        session.commit()
    owner.reconcile_interrupted(limit=2)
    with Session(owner._engine) as session:
        assert len(list(session.exec(select(ConversationJournalOwner)))) == 4
        assert session.get(ConversationJournalOwner, owner._owner_id) is not None
    assert [x.completion_status for x in journal(owner, active)] == ["pending"]


def test_terminal_commit_failure_rolls_back_and_pending_status_is_retryable(tmp_path, monkeypatch):
    from sqlalchemy.orm import Session as OrmSession
    owner = service(tmp_path / "commit-failure.db")
    exchange = begin(owner)
    def fail(session):
        session.flush()
        raise RuntimeError("injected commit failure")
    with monkeypatch.context() as patch:
        patch.setattr(OrmSession, "commit", fail)
        with pytest.raises(RuntimeError, match="injected commit failure"):
            complete(owner, exchange)
    assert [x.completion_status for x in journal(owner, exchange)] == ["pending"]
    assert owner.snapshot("one")["summary"] is None
    assert owner.snapshot("one")["working_state"] is None
    complete(owner, exchange)
    assert len(journal(owner, exchange)) == 2
