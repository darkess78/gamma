"""Real child-process failures and authority fences, using no model server."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from gamma.brain.process import BrainProcess, BrainUnavailable
from gamma.brain.authority import BrainAuthority
from gamma.config import settings
from gamma.identity.profile import SpeakerProfile


FIXTURE = r'''
import json, os, sys, time
generation = os.environ['GAMMA_BRAIN_GENERATION']
def emit(kind, identity=None, **fields):
 print(json.dumps(dict(schema_version=1, generation=generation, request_id=identity, type=kind, **fields)), flush=True)
emit('ready', pid=os.getpid(), model_loaded=False)
for line in sys.stdin:
 request=json.loads(line)
 identity=request['request_id']
 operation=request['operation']
 if operation=='rejected':
  emit('error', identity, error_code='conversation')
  continue
 if operation=='crash': os._exit(8)
 if operation=='hang': time.sleep(60)
 if operation=='callback':
  emit('callback', identity, operation='read', args={})
  json.loads(sys.stdin.readline())
 if operation=='obsolete': generation='0'*32
 emit('result', identity, value={'pid':os.getpid()})
'''


@pytest.fixture
def runtime():
    value = BrainProcess(command=[sys.executable, "-u", "-c", FIXTURE], timeout=2)
    value.control("start")
    try:
        yield value
    finally:
        value.control("stop")


def test_pause_resume_stop_restart_and_old_result(runtime):
    initial = runtime.status()
    value, generation = runtime.execute("echo", {}, lambda *_: None)
    assert value["pid"] == initial["pid"] != os.getpid()
    runtime.control("pause")
    with pytest.raises(BrainUnavailable):
        runtime.execute("echo", {}, lambda *_: None)
    runtime.control("resume")
    assert runtime.status()["pid"] == initial["pid"]
    runtime.control("stop")
    with pytest.raises(BrainUnavailable, match="obsolete"):
        with runtime.guard(generation):
            pytest.fail("stale commit authorized")
    runtime.control("start")
    assert runtime.status()["pid"] != initial["pid"]
    assert runtime.status()["generation"] != generation
    with pytest.raises(BrainUnavailable):
        with runtime.guard(generation):
            pytest.fail("old generation resurrected")


@pytest.mark.parametrize("operation", ["crash", "obsolete", "hang"])
def test_failures_do_not_restart_or_accept_old_output(runtime, operation):
    with pytest.raises(BrainUnavailable):
        runtime.execute(operation, {}, lambda *_: None)
    assert runtime.status()["state"] == "failed"
    with pytest.raises(BrainUnavailable):
        runtime.execute("echo", {}, lambda *_: None)
    runtime.control("start")
    assert runtime.execute("echo", {}, lambda *_: None)[0]["pid"] == runtime.status()["pid"]


def test_stop_during_callback_keeps_slot_until_authority_returns(runtime):
    entered, release = threading.Event(), threading.Event()
    def authority(*_):
        entered.set()
        assert release.wait(5)
        return "late"
    with ThreadPoolExecutor(1) as pool:
        work = pool.submit(runtime.execute, "callback", {}, authority)
        try:
            assert entered.wait(2)
            started = time.monotonic()
            runtime.control("stop")
            assert time.monotonic() - started < 1
            assert runtime.status()["active"]
            with pytest.raises(BrainUnavailable, match="draining"):
                runtime.control("start")
        finally:
            release.set()
        with pytest.raises(BrainUnavailable):
            work.result(3)
    runtime.control("start")
    assert runtime.status()["ready"]


def test_real_worker_exclusive_owner_lock(tmp_path):
    source = Path(__file__).resolve().parents[1]
    (tmp_path / "src").symlink_to(source / "src", target_is_directory=True)
    shutil.copy(source / "pyproject.toml", tmp_path)
    shutil.copytree(source / "config", tmp_path / "config", ignore=shutil.ignore_patterns("*.local.toml"))
    env = dict(os.environ, SHANA_PROJECT_ROOT=str(tmp_path), PYTHONPATH=str(source / "src"),
        SHANA_DATABASE_URL=f"sqlite:///{tmp_path}/data/memory/fixture.db", SHANA_LLM_PROVIDER="mock")
    first, second = BrainProcess(env=env), BrainProcess(env=env)
    try:
        first.control("start")
        with pytest.raises(BrainUnavailable, match="startup"):
            second.control("start")
        assert first.status()["ready"]
        first.control("stop")
        second.control("start")
        assert second.status()["ready"]
    finally:
        first.control("stop")
        second.control("stop")


def test_tool_callback_cannot_replace_authority_identity_or_proposals():
    calls = []
    service = SimpleNamespace(_execute_tool_calls=lambda requested, **kwargs: calls.append(kwargs) or [])
    speaker = SpeakerProfile(name="Guest", trust="public")
    proposals = {"memories": [], "core_facts": [], "session_id": "fixed"}
    authority = BrainAuthority(service, dict(speaker=speaker, session_id="fixed", persist_state=False,
        state_proposals=proposals))
    authority("tools", {"calls": [{"tool": "save_core_memory", "args": {"fact": "fictional"}}],
        "speaker": {"is_owner": True}, "enabled": True, "state_proposals": {"session_id": "other"}})
    assert calls[0]["speaker"] is speaker
    assert calls[0]["enabled"] is False
    assert calls[0]["state_proposals"] is proposals
    with pytest.raises(ValueError):
        authority("complete_exchange", {})
    with pytest.raises(ValueError):
        authority("deliver", {})


def test_request_rejection_keeps_worker_but_authority_failure_retires_it(runtime):
    from gamma.errors import ConversationError
    pid = runtime.status()["pid"]
    with pytest.raises(ConversationError, match="rejected"):
        runtime.execute("rejected", {}, lambda *_: None)
    assert runtime.status()["ready"] and runtime.status()["pid"] == pid
    assert runtime.execute("echo", {}, lambda *_: None)[0]["pid"] == pid
    def failing_authority(*_):
        raise ConversationError("fixture callback failed")
    with pytest.raises(BrainUnavailable):
        runtime.execute("callback", {}, failing_authority)
    assert runtime.status()["state"] == "failed"


def test_stop_between_result_and_commit_leaves_no_decision(tmp_path, monkeypatch, runtime):
    import sqlite3
    from gamma.conversation.service import ConversationService
    from gamma.schemas.response import AssistantResponse
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path}/state.db")
    monkeypatch.setattr("gamma.brain.process.get_brain_process", lambda: runtime)
    service = ConversationService(use_worker=True)
    generation = runtime.status()["generation"]
    def late_result(**fields):
        runtime.control("stop")
        return AssistantResponse(spoken_text="obsolete fictional text", tts_metadata={"brain_generation": generation})
    monkeypatch.setattr(service, "_respond", late_result)
    with pytest.raises(BrainUnavailable, match="obsolete"):
        service.respond("fictional input", session_id="late-result", defer_delivery=True)
    with sqlite3.connect(tmp_path / "state.db") as db:
        assert db.execute("select count(*) from gamma_decision_outbox").fetchone()[0] == 0
        assert db.execute("select status from gamma_exchange_owner").fetchone()[0] == "failed"
        assert db.execute("select count(*) from gamma_delivery_grant").fetchone()[0] == 0


def test_oversized_request_is_rejected_before_transport_without_retiring_worker(runtime):
    from gamma.errors import ConversationError
    from gamma.brain.wire import MAX_FRAME
    pid = runtime.status()["pid"]
    with pytest.raises(ConversationError, match="protocol limits"):
        runtime.execute("echo", {"text": "x" * MAX_FRAME}, lambda *_: None)
    assert runtime.status()["ready"]
    assert runtime.execute("echo", {}, lambda *_: None)[0]["pid"] == pid
