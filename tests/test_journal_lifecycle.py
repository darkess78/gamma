import asyncio
import threading

import pytest

from gamma.memory.journal_lifecycle import JournalRecovery


def test_recovery_retries_factory_and_batch_failures_and_stops_cleanly(caplog):
    class Fixture:
        calls = 0
        def reconcile_interrupted(self, *, limit):
            assert limit == 2
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("Synthetic SQL text must not be logged")
    fixture = Fixture()
    attempts = []
    def factory():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("Synthetic factory failure")
        return fixture
    async def exercise():
        recovery = JournalRecovery(factory=factory, interval_seconds=0.01, batch_size=2)
        await recovery.start()
        with pytest.raises(RuntimeError, match="already started"):
            await recovery.start()
        for _ in range(100):
            if fixture.calls >= 2:
                break
            await asyncio.sleep(0.005)
        assert fixture.calls >= 2
        await recovery.stop()
        assert recovery._task is None
        count = fixture.calls
        await asyncio.sleep(0.03)
        assert fixture.calls == count
        await recovery.stop()
    asyncio.run(exercise())
    assert len(attempts) == 2
    assert "Synthetic SQL text" not in caplog.text


def test_stop_waits_for_inflight_bounded_batch_without_detaching_thread():
    entered, release = threading.Event(), threading.Event()
    class Fixture:
        calls = 0
        def reconcile_interrupted(self, *, limit):
            self.calls += 1
            if self.calls == 2:
                entered.set()
                assert release.wait(timeout=2)
    fixture = Fixture()
    async def exercise():
        recovery = JournalRecovery(factory=lambda: fixture, interval_seconds=0.01, batch_size=1)
        await recovery.start()
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.005)
        assert entered.is_set()
        stopping = asyncio.create_task(recovery.stop())
        await asyncio.sleep(0.01)
        assert not stopping.done()
        release.set()
        await asyncio.wait_for(stopping, timeout=2)
        assert recovery._task is None
    try:
        asyncio.run(exercise())
    finally:
        release.set()


def test_actual_database_and_dead_process_are_reconciled_by_lifecycle(tmp_path):
    import subprocess
    import sys
    from sqlmodel import Session, select
    from gamma.memory.continuity import ContinuityService, ConversationJournalEntry
    path = tmp_path / "lifecycle.db"
    code = """
import os,sys
from gamma.memory.continuity import ContinuityService
s=ContinuityService(database_url='sqlite:///'+sys.argv[1])
s.begin_exchange(session_id='crashed',text='Synthetic',speaker_name='Fixture',input_source='test',privacy_scope='local_private')
os._exit(23)
"""
    assert subprocess.run([sys.executable, "-B", "-c", code, str(path)], timeout=10).returncode == 23
    service = ContinuityService(database_url=f"sqlite:///{path}")
    def status():
        with Session(service._engine) as session:
            return session.exec(select(ConversationJournalEntry)).one().completion_status
    assert status() == "pending"
    async def exercise():
        recovery = JournalRecovery(factory=lambda: service, interval_seconds=0.01)
        await recovery.start()
        assert status() == "interrupted"
        await recovery.stop()
    asyncio.run(exercise())


def test_cancelled_start_can_be_stopped_without_leaving_an_unowned_thread():
    entered, release = threading.Event(), threading.Event()
    class Fixture:
        def reconcile_interrupted(self, *, limit):
            entered.set()
            assert release.wait(timeout=2)
    async def exercise():
        recovery = JournalRecovery(factory=Fixture, interval_seconds=30)
        starting = asyncio.create_task(recovery.start())
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.005)
        assert entered.is_set()
        starting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await starting
        stopping = asyncio.create_task(recovery.stop())
        await asyncio.sleep(0.01)
        assert not stopping.done()
        release.set()
        await asyncio.wait_for(stopping, timeout=2)
        assert recovery._task is None
    try:
        asyncio.run(exercise())
    finally:
        release.set()
