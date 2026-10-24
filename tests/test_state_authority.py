import os
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import insert, select, update
from sqlmodel import Session

from gamma.memory.continuity import ContinuityService, ConversationJournalEntry
from gamma.memory.sqlite_backup import verified_backup
from gamma.memory.state_authority import StateConflict, documents, exchanges, generations, outbox
from gamma.memory.state_authority import versions
from gamma.config import settings
from gamma.schemas.response import MemoryCandidate
from gamma.memory.models import ProfileFact


def service(path):
    return ContinuityService(database_url=f"sqlite:///{path}")


def begin(owner, name="one"):
    return owner.begin_exchange(session_id=name, text="Fictional question?", speaker_name="Fixture",
                                input_source="test", privacy_scope="local_private")


def complete(owner, exchange, name="one"):
    owner.complete_exchange(exchange_id=exchange, session_id=name, assistant_text="Fictional response",
                            trace_id="fixture", output_target="dashboard_monitor", privacy_scope="local_private")


def journal(owner, exchange):
    with Session(owner._engine) as session:
        return list(session.exec(select(ConversationJournalEntry).where(ConversationJournalEntry.exchange_id == exchange)).scalars())


def test_construction_and_expired_live_lease_do_not_interrupt_another_instance(tmp_path):
    first = service(tmp_path / "state.db")
    pending = begin(first)
    other = service(tmp_path / "state.db")
    assert [e.completion_status for e in journal(other, pending)] == ["pending"]
    with first._write_session() as session:
        session.execute(update(generations).where(generations.c.id == first.generation_id).values(lease_until=0))
        session.commit()
    assert other.mark_pending_interrupted(generation_id=first.generation_id) == 0
    complete(first, pending)
    assert first.snapshot("one")["state_version"] == 1


def test_retired_generation_recovery_cannot_touch_other_pending_work(tmp_path):
    first, second = service(tmp_path / "state.db"), service(tmp_path / "state.db")
    old, live = begin(first, "old"), begin(second, "live")
    first.retire_generation()
    assert second.mark_pending_interrupted(generation_id=first.generation_id) == 1
    assert [e.completion_status for e in journal(second, old)] == ["interrupted"]
    assert [e.completion_status for e in journal(second, live)] == ["pending"]
    with pytest.raises(StateConflict):
        complete(first, old, "old")
    complete(second, live, "live")


def test_stale_and_foreign_commits_cannot_change_checkpoint_or_duplicate_outbox(tmp_path):
    first, second = service(tmp_path / "state.db"), service(tmp_path / "state.db")
    one, two = begin(first), begin(second)
    with pytest.raises(StateConflict, match="not_owned"):
        complete(second, one)
    complete(first, one)
    with pytest.raises(StateConflict, match="stale_state_version"):
        complete(second, two)
    with pytest.raises(StateConflict, match="not_pending"):
        complete(first, one)
    with Session(first._engine) as session:
        assert len(list(session.execute(select(outbox)))) == 1
    assert first.snapshot("one")["state_version"] == 1
    assert len(journal(first, two)) == 1


def test_decision_checkpoint_summary_and_outbox_rollback_together(tmp_path):
    owner = service(tmp_path / "state.db")
    pending = begin(owner)
    with patch.object(owner, "_maybe_update_summary_locked", side_effect=RuntimeError("injected crash")):
        with pytest.raises(RuntimeError):
            complete(owner, pending)
    assert owner.snapshot("one")["state_version"] == 0
    assert owner.snapshot("one")["working_state"] is None
    assert [e.completion_status for e in journal(owner, pending)] == ["pending"]
    with Session(owner._engine) as session:
        assert list(session.execute(select(outbox))) == []
    complete(owner, pending)
    assert owner.snapshot("one")["state_version"] == 1


def test_real_process_crash_rolls_back_and_recovery_is_scoped(tmp_path):
    path = tmp_path / "state.db"
    live = service(path)
    pending = begin(live, "live")
    code = '''
import os, sys
from pathlib import Path
from gamma.config import settings
from gamma.memory.continuity import ContinuityService
settings.data_dir=Path(sys.argv[1]).parent
settings.assistant_state_enabled=True
s=ContinuityService(database_url='sqlite:///'+sys.argv[1])
x=s.begin_exchange(session_id='crash',text='Synthetic',speaker_name='Fixture',input_source='test',privacy_scope='local_private')
s._maybe_update_summary_locked=lambda *a,**kw: os._exit(23)
s.complete_exchange(exchange_id=x,session_id='crash',assistant_text='Never committed',trace_id=None,output_target='dashboard_monitor',privacy_scope='local_private',persona_emotion='happy')
'''
    result = subprocess.run([sys.executable, "-c", code, str(path)], timeout=15)
    assert result.returncode == 23
    with live._write_session() as session:
        dead = session.execute(select(exchanges.c.generation_id).where(exchanges.c.session_id == "crash")).scalar_one()
        session.execute(update(generations).where(generations.c.id == dead).values(lease_until=0))
        session.commit()
    assert live.snapshot("crash")["state_version"] == 0
    with Session(live._engine) as session:
        assert list(session.execute(select(documents))) == []
    assert live.mark_pending_interrupted(generation_id=dead) == 1
    assert [e.completion_status for e in journal(live, pending)] == ["pending"]


def test_session_delete_invalidates_prior_work_and_removes_outbox(tmp_path):
    owner = service(tmp_path / "state.db")
    one = begin(owner)
    complete(owner, one)
    pending = begin(owner)
    owner.delete_session("one")
    with pytest.raises(StateConflict):
        complete(owner, pending)
    assert owner.snapshot("one")["state_version"] == 2
    with Session(owner._engine) as session:
        assert list(session.execute(select(outbox))) == []


def test_backup_includes_wal_and_fails_closed(tmp_path):
    path = tmp_path / "state#fixture.db"
    with sqlite3.connect(path) as db:
        db.execute("pragma journal_mode=wal")
        db.execute("create table fixture(value text)")
        db.execute("insert into fixture values ('synthetic')")
        db.commit()
        copy = verified_backup(path)
        with sqlite3.connect(copy) as saved:
            assert saved.execute("select value from fixture").fetchone()[0] == "synthetic"
        if os.name != "nt":
            assert copy.stat().st_mode & 0o777 == 0o600
    invalid = tmp_path / "invalid.db"
    invalid.write_bytes(b"invalid database")
    with pytest.raises(sqlite3.DatabaseError):
        verified_backup(invalid)
    assert verified_backup(tmp_path / "missing.db") is None


def test_retention_preserves_pending_work_and_removes_only_undispatched_orphans(tmp_path):
    owner = service(tmp_path / "state.db")
    done = begin(owner, "done")
    complete(owner, done, "done")
    uncertain = begin(owner, "uncertain")
    complete(owner, uncertain, "uncertain")
    pending = begin(owner, "pending")
    with owner._write_session() as session:
        session.execute(update(ConversationJournalEntry).values(occurred_at=datetime(2000, 1, 1, tzinfo=timezone.utc)))
        session.execute(update(outbox).where(outbox.c.exchange_id == uncertain).values(status="external_outcome_unknown"))
        session.commit()
    assert owner.prune() == 4
    assert [entry.completion_status for entry in journal(owner, pending)] == ["pending"]
    with Session(owner._engine) as session:
        assert list(session.execute(select(outbox.c.exchange_id)).scalars()) == [uncertain]
        assert set(session.execute(select(exchanges.c.exchange_id)).scalars()) == {pending, uncertain}
    assert owner.snapshot("done")["state_version"] == 1
    complete(owner, pending, "pending")


def test_transient_checkpoint_retention_preserves_pending_decisions(tmp_path):
    owner = service(tmp_path / "state.db")
    old = begin(owner, "transient:expired")
    complete(owner, old, "transient:expired")
    pending = begin(owner, "transient:pending")
    with owner._write_session() as session:
        session.execute(update(ConversationJournalEntry).values(occurred_at=datetime(2000, 1, 1, tzinfo=timezone.utc)))
        session.commit()
    owner.prune()
    assert owner.snapshot("transient:expired")["working_state"] is None
    with Session(owner._engine) as session:
        assert list(session.execute(select(versions.c.session_id)).scalars()) == ["transient:pending"]
    complete(owner, pending, "transient:pending")


def test_operator_mutations_invalidate_old_decisions_and_failed_edits_rollback(tmp_path):
    owner = service(tmp_path / "state.db")
    pending = begin(owner)
    writer = owner._memory_writer
    with pytest.raises(ValueError):
        writer.update_item("profile_fact", 99999, {"summary": "Invalid edit"})
    assert owner.snapshot("one")["state_version"] == 0
    writer.create_item({"kind": "profile_fact", "summary": "Operator correction"})
    assert owner.snapshot("one")["state_version"] == 1
    with pytest.raises(StateConflict):
        complete(owner, pending)
    newer = begin(owner)
    complete(owner, newer)
    with Session(owner._engine) as session:
        assert [fact.fact_text for fact in session.execute(select(ProfileFact)).scalars()] == ["Operator correction"]


def test_shared_memory_update_invalidates_other_session_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "memory_enabled", True)
    monkeypatch.setattr(settings, "memory_write_mode", "on")
    owner = service(tmp_path / "state.db")
    one, two = begin(owner, "one"), begin(owner, "two")
    owner.complete_exchange(exchange_id=one, session_id="one", assistant_text="Accepted", trace_id=None,
        output_target="dashboard_monitor", privacy_scope="local_private",
        memory_candidates=[MemoryCandidate(type="profile", text="New shared fact")])
    with pytest.raises(StateConflict):
        complete(owner, two, "two")


def test_memory_candidates_rollback_with_decision_and_commit_existing_fact_updates(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "memory_enabled", True)
    monkeypatch.setattr(settings, "memory_write_mode", "on")
    owner = service(tmp_path / "state.db")
    pending = begin(owner)
    candidate = MemoryCandidate(type="profile", text="My name is Fixture", importance=0.5, tags=["identity"])
    def commit():
        owner.complete_exchange(exchange_id=pending, session_id="one", assistant_text="Recorded",
                                trace_id=None, output_target="dashboard_monitor", privacy_scope="local_private",
                                memory_candidates=[candidate])
    with patch.object(owner, "_maybe_update_summary_locked", side_effect=RuntimeError("rollback")):
        with pytest.raises(RuntimeError):
            commit()
    with Session(owner._engine) as session:
        assert list(session.execute(select(ProfileFact)).scalars()) == []
    commit()
    with Session(owner._engine) as session:
        assert len(list(session.execute(select(ProfileFact)).scalars())) == 1
    candidate.importance = 0.9
    assert owner._memory_writer.persist_candidates([candidate]) == 0
    with Session(owner._engine) as session:
        assert session.execute(select(ProfileFact)).scalar_one().confidence == 0.9


@pytest.mark.parametrize("fast_mode", [False, True])
@pytest.mark.parametrize("stale", [False, True])
def test_real_conversation_commits_memory_only_with_current_decision(tmp_path, monkeypatch, fast_mode, stale):
    import json
    from types import SimpleNamespace
    from gamma.conversation.service import ConversationService
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'conversation.db'}")
    monkeypatch.setattr(settings, "memory_enabled", True)
    monkeypatch.setattr(settings, "memory_write_mode", "on")
    monkeypatch.setattr(settings, "speech_filter_llm_enabled", False)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "assistant_state_enabled", True)
    conversation = ConversationService()
    other = service(tmp_path / "conversation.db")
    def reply(*args, **kwargs):
        if stale:
            complete(other, begin(other), "one")
        return SimpleNamespace(text=json.dumps({"action": "reply", "requested_delivery": "text_only", "final_text": "A fictional response.",
            "emotion": "neutral", "memory_candidates": [{"type": "profile", "text": "A stale fictional fact", "importance": 0.8}]}))
    conversation._llm = SimpleNamespace(generate_reply=reply)
    monkeypatch.setattr(conversation, "_append_timing_log", lambda **kw: None)
    original_complete = conversation._continuity.complete_exchange
    proposed = []
    def checked_complete(**kwargs):
        proposed.extend(kwargs["memory_candidates"])
        return original_complete(**kwargs)
    monkeypatch.setattr(conversation._continuity, "complete_exchange", checked_complete)
    with patch("gamma.conversation.service.build_system_prompt", return_value="Fixture"):
        if stale:
            with pytest.raises(StateConflict):
                conversation.respond(user_text="Hello there", session_id="one", fast_mode=fast_mode)
        else:
            conversation.respond(user_text="Hello there", session_id="one", fast_mode=fast_mode)
    assert len(proposed) == 1 and proposed[0].text == "A stale fictional fact"
    with Session(other._engine) as session:
        assert len(list(session.execute(select(ProfileFact)).scalars())) == (0 if stale else 1)
        assert len(list(session.execute(select(documents).where(documents.c.key.in_(("assistant_feeling", "assistant_emotion")))))) == (0 if stale else 2)
    assert other.snapshot("one")["state_version"] == 1


def test_persona_migration_is_preserved_and_rolls_back_with_decision(tmp_path, monkeypatch):
    import json
    from gamma.persona.assistant_state import AssistantStateStore
    from gamma.persona.emotion_service import EmotionMemoryService
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'persona.db'}")
    monkeypatch.setattr(settings, "assistant_state_enabled", True)
    feeling = tmp_path / "assistant_state.json"
    emotion = tmp_path / "assistant_emotion_memory.json"
    feeling.write_text(json.dumps({"current_emotion": "neutral", "recent_emotions": ["neutral"], "notes": ["Prior fictional note"]}))
    emotion.write_text(json.dumps({"state": {"current_emotion": "neutral"}, "episodes": [], "patterns": []}))
    originals = {path: path.read_bytes() for path in (feeling, emotion)}
    owner = service(tmp_path / "persona.db")
    pending = begin(owner)
    def commit():
        owner.complete_exchange(exchange_id=pending, session_id="one", assistant_text="A fictional happy reply",
            trace_id=None, output_target="dashboard_monitor", privacy_scope="local_private", persona_emotion="happy")
    with patch.object(owner, "_maybe_update_summary_locked", side_effect=RuntimeError("rollback persona")):
        with pytest.raises(RuntimeError):
            commit()
    with Session(owner._engine) as session:
        assert list(session.execute(select(documents))) == []
    assert AssistantStateStore().load().current_emotion == "neutral"
    commit()
    assert AssistantStateStore().load().recent_emotions == ["neutral", "happy"]
    assert EmotionMemoryService().load_bundle()["state"].current_emotion == "happy"
    with Session(owner._engine) as session:
        rows = {r["key"]: r for r in session.execute(select(documents)).mappings()}
        assert rows["assistant_feeling"]["version"] == 1
        assert json.loads(rows["assistant_feeling"]["legacy_payload"])["notes"] == ["Prior fictional note"]
    assert all(path.read_bytes() == original for path, original in originals.items())


def test_invalid_persona_migration_aborts_decision_without_overwriting_source(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "assistant_state_enabled", True)
    legacy = tmp_path / "assistant_emotion_memory.json"
    legacy.write_bytes(b"invalid JSON")
    owner = service(tmp_path / "state.db")
    pending = begin(owner)
    with pytest.raises(ValueError):
        owner.complete_exchange(exchange_id=pending, session_id="one", assistant_text="Uncommitted",
            trace_id=None, output_target="dashboard_monitor", privacy_scope="local_private", persona_emotion="happy")
    assert owner.snapshot("one")["state_version"] == 0
    with Session(owner._engine) as session:
        assert list(session.execute(select(documents))) == []
        assert list(session.execute(select(outbox))) == []
    assert legacy.read_bytes() == b"invalid JSON"


def test_schema_migration_keeps_verified_backup_and_stops_on_backup_error(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.execute("create table legacy(value text)")
        db.execute("insert into legacy values ('synthetic')")
    with patch("gamma.memory.state_authority.verified_backup", side_effect=OSError("backup denied")):
        with pytest.raises(OSError):
            service(path)
    with sqlite3.connect(path) as db:
        assert db.execute("select name from sqlite_master where name='gamma_state_generation'").fetchone() is None
    owner = service(path)
    assert owner._authority.backup_path.is_file()
    with sqlite3.connect(owner._authority.backup_path) as db:
        assert db.execute("select value from legacy").fetchone()[0] == "synthetic"
        assert db.execute("select name from sqlite_master where name='gamma_state_generation'").fetchone() is None


def test_two_real_processes_cannot_commit_the_same_state_version(tmp_path):
    path = tmp_path / "concurrent.db"
    service(path)
    code = '''
import sys
from gamma.memory.continuity import ContinuityService
from gamma.memory.state_authority import StateConflict
s=ContinuityService(database_url='sqlite:///'+sys.argv[1])
x=s.begin_exchange(session_id='shared',text='Fictional concurrent input',speaker_name='Fixture',input_source='test',privacy_scope='local_private')
print(x,flush=True)
sys.stdin.readline()
try:
 s.complete_exchange(exchange_id=x,session_id='shared',assistant_text='Single committed response',trace_id=None,output_target='dashboard_monitor',privacy_scope='local_private')
 print('completed',flush=True)
except StateConflict:
 print('stale',flush=True)
'''
    children = [subprocess.Popen([sys.executable, "-c", code, str(path)], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        futures = [pool.submit(child.stdout.readline) for child in children]
        assert all(f.result(timeout=10).strip() for f in futures)
        for child in children:
            child.stdin.write("commit\n")
            child.stdin.flush()
        results = [child.communicate(timeout=10) for child in children]
        assert {stdout.strip() for stdout, stderr in results} == {"completed", "stale"}
        assert all(child.returncode == 0 for child in children)
        with sqlite3.connect(path) as db:
            assert db.execute("select version from gamma_state_version where session_id='shared'").fetchone()[0] == 1
            assert db.execute("select count(*) from gamma_decision_outbox").fetchone()[0] == 1
            assert db.execute("select count(*) from conversationjournalentry where role='assistant'").fetchone()[0] == 1
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)
        pool.shutdown(wait=True, cancel_futures=True)
