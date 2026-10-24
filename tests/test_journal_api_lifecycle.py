import asyncio

import pytest

from gamma import main


@pytest.mark.parametrize("scheduler_fails", [False, True])
def test_application_owns_recovery_start_and_stop(monkeypatch, scheduler_fails):
    events = []
    class Recovery:
        async def start(self): events.append("recovery-start")
        async def stop(self): events.append("recovery-stop")
    class Scheduler:
        async def start(self):
            events.append("scheduler-start")
            if scheduler_fails: raise RuntimeError("scheduler failed")
        async def stop(self): events.append("scheduler-stop")
    monkeypatch.setattr(main, "JournalRecovery", Recovery)
    monkeypatch.setattr(main, "get_proactive_scheduler", Scheduler)
    monkeypatch.setattr(main, "start_minecraft_coordinator", lambda: "fixture")
    monkeypatch.setattr(main, "reset_minecraft_coordinator", lambda _: events.append("coordinator-stop"))
    async def exercise():
        async with main.lifespan(None):
            events.append("application-running")
    if scheduler_fails:
        with pytest.raises(RuntimeError, match="scheduler failed"):
            asyncio.run(exercise())
    else:
        asyncio.run(exercise())
    assert events[0] == "recovery-start"
    assert events[-1] == "recovery-stop"
    assert "scheduler-stop" in events
    assert "coordinator-stop" in events


def test_authenticated_asgi_lifespan_runs_actual_scoped_recovery(tmp_path, monkeypatch):
    import subprocess
    import sys
    from fastapi.testclient import TestClient
    from sqlmodel import Session, select
    from gamma.api import routes
    from gamma.conversation.service import ConversationService
    from gamma.memory.continuity import ContinuityService, ConversationJournalEntry
    from gamma.memory.journal_lifecycle import JournalRecovery
    path = tmp_path / "asgi.db"
    url = f"sqlite:///{path}"
    monkeypatch.setattr(main.settings, "database_url", url)
    monkeypatch.setattr(main.settings, "api_auth_enabled", True)
    monkeypatch.setattr(main.settings, "api_bearer_token", "synthetic-journal-test")
    live = ContinuityService(database_url=url)
    live.begin_exchange(session_id="live", text="Synthetic", speaker_name="Fixture",
                        input_source="test", privacy_scope="local_private")
    code = """
import os,sys
from gamma.memory.continuity import ContinuityService
s=ContinuityService(database_url='sqlite:///'+sys.argv[1])
s.begin_exchange(session_id='dead',text='Synthetic',speaker_name='Fixture',input_source='test',privacy_scope='local_private')
os._exit(23)
"""
    assert subprocess.run([sys.executable, "-B", "-c", code, str(path)], timeout=10).returncode == 23
    # Construct the real conversation facade without inference or production data.
    conversation = ConversationService()
    recovery = JournalRecovery(factory=lambda: live)
    class Scheduler:
        async def start(self): pass
        async def stop(self): pass
    monkeypatch.setattr(main, "JournalRecovery", lambda: recovery)
    monkeypatch.setattr(main, "get_proactive_scheduler", Scheduler)
    monkeypatch.setattr(main, "start_minecraft_coordinator", lambda: "fixture")
    monkeypatch.setattr(main, "reset_minecraft_coordinator", lambda _: None)
    monkeypatch.setattr(routes, "get_conversation_service", lambda: conversation)
    with TestClient(main.app) as client:
        assert client.get("/health").status_code == 401
        response = client.get("/v1/conversation/sessions/live/continuity",
                              headers={"Authorization": "Bearer synthetic-journal-test"})
        assert response.status_code == 200
        assert set(response.json()) == {"session_id", "summary", "working_state"}
        with Session(live._engine) as session:
            states = {entry.session_id: entry.completion_status for entry in session.exec(select(ConversationJournalEntry))}
        assert states == {"live": "pending", "dead": "interrupted"}
    assert recovery._task is None
