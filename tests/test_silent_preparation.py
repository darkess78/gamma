import sqlite3
import multiprocessing
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest

from gamma.conversation.service import ConversationService
from gamma.errors import ConversationError
from gamma.memory.continuity import ContinuityService
from gamma.schemas.response import AssistantResponse, ToolCall


def prepare_service(tmp_path, monkeypatch):
    state = {"mode": "wake", "control_revision": 1, "audience": {}, "outputs": {}}
    monkeypatch.setattr("gamma.presence.load_presence_state", lambda **kw: dict(state))
    monkeypatch.setattr("gamma.presence.presence_state_path", lambda: tmp_path / "presence.json")
    service = object.__new__(ConversationService)
    service._continuity = ContinuityService(database_url=f"sqlite:///{tmp_path / 'state.db'}")
    service.respond = Mock(return_value=AssistantResponse(
        spoken_text="Unapproved speech", display_text="Unapproved display", speech_text="Unapproved speech",
        internal_summary="No additional action is needed.",
        tool_calls=[ToolCall(tool="save_core_memory", args={"text": "not authorized"})]))
    return service, state


def test_preparation_only_commits_summary_with_no_delivery_or_tool_metadata(tmp_path, monkeypatch):
    service, state = prepare_service(tmp_path, monkeypatch)
    prepared = service.prepare_silent_cycle()
    request = service.respond.call_args.kwargs
    assert request["evaluation_mode"] is True and request["synthesize_speech"] is False
    assert request["speaker_ctx"].source == "silent_scheduler"
    prepared()
    prepared.discard()
    with sqlite3.connect(tmp_path / "state.db") as db:
        assert db.execute("select status from gamma_decision_outbox").fetchall() == [("internal_only",)]
        assert db.execute("select count(*) from gamma_delivery_grant").fetchone()[0] == 0
        assert db.execute("select count(*) from conversationjournalentry where role='assistant'").fetchone()[0] == 0
        assert db.execute("select status from gamma_exchange_owner").fetchone()[0] == "completed"


def test_changed_presence_discards_owned_pending_cycle(tmp_path, monkeypatch):
    service, state = prepare_service(tmp_path, monkeypatch)
    prepared = service.prepare_silent_cycle()
    state.update(mode="sleep", control_revision=2)
    with pytest.raises(ConversationError, match="presence_changed"):
        prepared()
    prepared.discard()
    with sqlite3.connect(tmp_path / "state.db") as db:
        assert db.execute("select status from gamma_exchange_owner").fetchone()[0] == "failed"
        assert db.execute("select count(*) from gamma_decision_outbox").fetchone()[0] == 0


def test_sleep_never_starts_inference(tmp_path, monkeypatch):
    service, state = prepare_service(tmp_path, monkeypatch)
    state["mode"] = "sleep"
    with pytest.raises(ConversationError, match="presence_suppressed"):
        service.prepare_silent_cycle()
    service.respond.assert_not_called()


def _operator_process(path, database, probe, proceed, results):
    from gamma.presence import load_presence_state, save_presence_state
    from gamma.supervisor.lifecycle_lock import lifecycle_lock
    assert probe.wait(10)
    try:
        with lifecycle_lock(Path(path).with_suffix(".lock"), timeout=0.2):
            results.put("entered_during_commit")
    except TimeoutError:
        results.put("blocked_during_commit")
    assert proceed.wait(10)
    state = load_presence_state(path=Path(path))
    state.update(mode="sleep", control_revision=state["control_revision"] + 1)
    save_presence_state(state, path=Path(path))
    with sqlite3.connect(database) as db:
        results.put(db.execute("select status from gamma_exchange_owner").fetchone()[0])


def test_operator_process_cannot_change_presence_inside_silent_commit(tmp_path, monkeypatch):
    from gamma.presence import load_presence_state, save_presence_state, presence_state_for_mode
    path = tmp_path / "presence.json"
    monkeypatch.setattr("gamma.presence.presence_state_path", lambda: path)
    save_presence_state(presence_state_for_mode("wake"))
    service = object.__new__(ConversationService)
    service._continuity = ContinuityService(database_url=f"sqlite:///{tmp_path / 'state.db'}")
    service.respond = Mock(return_value=AssistantResponse(spoken_text="", internal_summary="No action needed."))
    prepared = service.prepare_silent_cycle()
    entered, release = threading.Event(), threading.Event()
    original = service._continuity.complete_exchange
    def held_commit(**kwargs):
        entered.set()
        assert release.wait(10)
        original(**kwargs)
    service._continuity.complete_exchange = held_commit
    ctx = multiprocessing.get_context("spawn")
    probe, proceed, results = ctx.Event(), ctx.Event(), ctx.Queue()
    process = ctx.Process(target=_operator_process, args=(str(path), str(tmp_path / "state.db"), probe, proceed, results))
    process.start()
    try:
        with ThreadPoolExecutor(1) as pool:
            commit = pool.submit(prepared)
            try:
                assert entered.wait(5)
                probe.set()
                assert results.get(timeout=10) == "blocked_during_commit"
            finally:
                release.set()
            commit.result(5)
        proceed.set()
        assert results.get(timeout=10) == "completed"
        process.join(5)
        assert process.exitcode == 0
        assert load_presence_state()["mode"] == "sleep"
    finally:
        release.set()
        probe.set()
        proceed.set()
        if process.is_alive():
            process.terminate()
            process.join(5)
        prepared.discard()
