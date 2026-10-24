from __future__ import annotations

import asyncio
from contextlib import contextmanager

import pytest
from sqlmodel import Session, select

from test_conversation_recipient import fixture_state, PRIVATE, Capture
from gamma.errors import ConversationError
from gamma.memory.continuity import ContinuityService, ConversationJournalEntry


class Raising:
    supports_vision = False

    def __init__(self, error, on_enter=None):
        self.error = error
        self.on_enter = on_enter
        self.calls = 0

    def generate_reply(self, *args, **kwargs):
        self.calls += 1
        if self.on_enter:
            self.on_enter()
        raise self.error


def rows(service):
    with Session(service._continuity._engine) as db:
        return [r.model_dump() for r in db.exec(
            select(ConversationJournalEntry).order_by(ConversationJournalEntry.id)
        )]


def begin(owner, session):
    return owner.begin_exchange(session_id=session, text="Synthetic input.",
                                speaker_name="Fixture", input_source="test",
                                privacy_scope="local_private")


def current(service):
    return [r for r in rows(service) if r["session_id"] == "interruption"]


def protect_other_work(service):
    owner = service._continuity
    saved = begin(owner, "saved")
    owner.complete_exchange(exchange_id=saved, session_id="saved",
                            assistant_text="Saved reply.", trace_id=None,
                            output_target="dashboard_monitor", privacy_scope="local_private",
                            state_updates={"current_objective": "Saved objective."})
    begin(owner, "other-pending")
    foreign = ContinuityService(database_url=str(owner._engine.url))
    begin(foreign, "foreign-pending")
    return foreign, rows(service), owner.snapshot("saved", privacy_scope="local_private")


@pytest.mark.parametrize("fast", [False, True])
@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, SystemExit, asyncio.CancelledError])
def test_interruption_closes_only_owned_pending_and_preserves_original(fixture_state, fast, exception_type):
    service = fixture_state.service
    foreign, protected, checkpoint = protect_other_work(service)
    original = exception_type("Synthetic interruption.")
    service._llm = Raising(original)
    before = service._continuity.snapshot("interruption", privacy_scope="local_private")

    with pytest.raises(exception_type) as caught:
        service.respond("Synthetic turn.", session_id="interruption", recipient=PRIVATE, fast_mode=fast)

    assert caught.value is original
    assert service._llm.calls == 1
    assert len(current(service)) == 1
    assert current(service)[0]["completion_status"] == "interrupted"
    assert current(service)[0]["owner_id"] == service._continuity._owner_id
    assert [r for r in rows(service) if r["session_id"] != "interruption"] == protected
    assert service._continuity.snapshot("interruption", privacy_scope="local_private") == before
    assert service._continuity.snapshot("saved", privacy_scope="local_private") == checkpoint
    assert foreign.reconcile_interrupted() == 0


@pytest.mark.parametrize("fast", [False, True])
def test_ordinary_failure_remains_failed_with_pipeline_error(fixture_state, fast):
    service = fixture_state.service
    original = RuntimeError("Synthetic ordinary failure.")
    service._llm = Raising(original)
    with pytest.raises(ConversationError) as caught:
        service.respond("Synthetic turn.", session_id="interruption", recipient=PRIVATE, fast_mode=fast)
    assert caught.value.__cause__ is original
    assert service._llm.calls == 1
    assert len(current(service)) == 1
    assert current(service)[0]["completion_status"] == "failed"


@pytest.mark.parametrize("fast", [False, True])
@pytest.mark.parametrize("journal_kwargs", [{"session_id": "interruption", "evaluation_mode": True}, {}])
def test_interruption_without_journal_keeps_no_journal(fixture_state, fast, journal_kwargs):
    service = fixture_state.service
    before = rows(service)
    original = KeyboardInterrupt("Synthetic interruption.")
    service._llm = Raising(original)
    with pytest.raises(KeyboardInterrupt) as caught:
        service.respond("Synthetic turn.", recipient=PRIVATE, fast_mode=fast, **journal_kwargs)
    assert caught.value is original
    assert service._llm.calls == 1
    assert rows(service) == before


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled", "interrupted"])
def test_interruption_cannot_rewrite_existing_terminal_or_its_context(fixture_state, status):
    service = fixture_state.service
    preserved = {}

    def make_terminal():
        exchange = current(service)[0]["exchange_id"]
        if status == "completed":
            service._continuity.complete_exchange(
                exchange_id=exchange, session_id="interruption", assistant_text="Committed reply.",
                trace_id=None, output_target="dashboard_monitor", privacy_scope="local_private",
                state_updates={"current_objective": "Committed objective."})
        else:
            assert service._continuity.fail_exchange(exchange, status=status)
        preserved["rows"] = rows(service)
        preserved["snapshot"] = service._continuity.snapshot("interruption", privacy_scope="local_private")

    original = KeyboardInterrupt("Synthetic interruption.")
    service._llm = Raising(original, make_terminal)
    with pytest.raises(KeyboardInterrupt) as caught:
        service.respond("Synthetic turn.", session_id="interruption", recipient=PRIVATE)
    assert caught.value is original
    assert rows(service) == preserved["rows"]
    assert service._continuity.snapshot("interruption", privacy_scope="local_private") == preserved["snapshot"]


def test_interruption_cleanup_cannot_change_a_foreign_owned_exchange(fixture_state):
    service = fixture_state.service
    foreign = ContinuityService(database_url=str(service._continuity._engine.url))
    begin(foreign, "foreign")
    protected = {}

    def transfer_owner():
        with Session(service._continuity._engine) as db:
            entry = db.exec(select(ConversationJournalEntry).where(
                ConversationJournalEntry.session_id == "interruption")).one()
            entry.owner_id = foreign._owner_id
            db.add(entry)
            db.commit()
        protected["rows"] = rows(service)

    original = KeyboardInterrupt("Synthetic interruption.")
    service._llm = Raising(original, transfer_owner)
    with pytest.raises(KeyboardInterrupt) as caught:
        service.respond("Synthetic turn.", session_id="interruption", recipient=PRIVATE)
    assert caught.value is original
    assert rows(service) == protected["rows"]
    assert current(service)[0]["completion_status"] == "pending"


def test_cleanup_persistence_error_preserves_primary_and_safe_diagnostic(fixture_state, monkeypatch, caplog):
    service = fixture_state.service
    original = KeyboardInterrupt("Synthetic primary interruption.")
    cleanup_error = OSError("SYNTHETIC_PRIVATE_ERROR_DO_NOT_LOG")

    @contextmanager
    def failed_write():
        raise cleanup_error
        yield

    def fail_cleanup_storage():
        monkeypatch.setattr(service._continuity, "_write_session", failed_write)

    service._llm = Raising(original, fail_cleanup_storage)
    with pytest.raises(KeyboardInterrupt) as caught:
        service.respond("Synthetic turn.", session_id="interruption", recipient=PRIVATE)
    assert caught.value is original
    assert current(service)[0]["completion_status"] == "pending"
    assert len(current(service)) == 1
    diagnostics = [r.getMessage() for r in caplog.records if r.name == "gamma.conversation.service"]
    assert diagnostics == ["Journal interruption cleanup failed (OSError)"]
    assert "SYNTHETIC_PRIVATE_ERROR_DO_NOT_LOG" not in caplog.text


def test_interruption_during_terminal_transaction_rolls_back_then_closes_pending(fixture_state, monkeypatch):
    service = fixture_state.service
    service._llm = Capture()
    original = asyncio.CancelledError("Synthetic terminal interruption.")
    before = service._continuity.snapshot("interruption", privacy_scope="local_private")

    def interrupt_commit(*args, **kwargs):
        raise original

    monkeypatch.setattr(service._continuity, "_update_working_state_locked", interrupt_commit)
    with pytest.raises(asyncio.CancelledError) as caught:
        service.respond("Synthetic turn.", session_id="interruption", recipient=PRIVATE)
    assert caught.value is original
    assert service._llm.calls[0]["purpose"] == "conversation_draft"
    assert len(current(service)) == 1
    assert current(service)[0]["completion_status"] == "interrupted"
    assert service._continuity.snapshot("interruption", privacy_scope="local_private") == before


def test_ordinary_terminal_persistence_error_keeps_existing_pending_behavior(fixture_state, monkeypatch):
    service = fixture_state.service
    service._llm = Capture()
    original = RuntimeError("Synthetic terminal persistence failure.")

    def fail_commit(*args, **kwargs):
        raise original

    monkeypatch.setattr(service._continuity, "_update_working_state_locked", fail_commit)
    with pytest.raises(RuntimeError) as caught:
        service.respond("Synthetic turn.", session_id="interruption", recipient=PRIVATE)
    assert caught.value is original
    assert len(current(service)) == 1
    assert current(service)[0]["completion_status"] == "pending"
