"""Actual adapter startup exposes bounded pending and processing work."""
import asyncio
import json
from types import SimpleNamespace as NS

import pytest

from test_coordination_discord_private import setup


async def until(predicate):
    deadline = asyncio.get_running_loop().time() + 1
    while not predicate() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(.001)
    assert predicate()


def controller():
    value = NS(_state='listening', _voice=NS(stop=lambda: None))
    async def handle(_owner, _action):
        value._state = 'disconnected'
        return {'ok': True, 'state': 'disconnected'}
    value.handle = handle
    return value


def enqueue(adapter):
    adapter._enqueue_audio(NS(closed=False, epoch=adapter._voice_epoch),
        {'user_id': '1', 'wav': b'SECRET' * 8})


def test_actual_start_two_audio_workers_and_pending_queue_are_visible_until_close():
    async def run():
        adapter, service, _dm, _room = setup()
        adapter._controller = controller()
        adapter._started = False
        entered = []
        gate = asyncio.Event()
        async def submit(observation, audio):
            entered.append(observation.request_id)
            await gate.wait()
            return {'status': 'generated'}
        service.submit_audio = submit
        try:
            await adapter.start()
            enqueue(adapter)
            await until(lambda: len(entered) == 1)
            assert adapter.voice_timing_snapshot()['processing_utterances'] == 1
            enqueue(adapter)
            await until(lambda: len(entered) == 2)
            assert adapter.voice_timing_snapshot()['processing_utterances'] == 2
            enqueue(adapter)
            snapshot = adapter.voice_timing_snapshot()
            assert snapshot['queued_utterances'] == 1
            assert snapshot['processing_utterances'] == 2
            assert len(entered) == 2
            assert 'SECRET' not in json.dumps(snapshot)
        finally:
            await adapter.close()
        snapshot = adapter.voice_timing_snapshot()
        assert snapshot['queued_utterances'] == snapshot['processing_utterances'] == 0
        assert len(entered) == 2
        assert adapter._tasks == []
    asyncio.run(run())


@pytest.mark.parametrize('outcome', ['error', 'epoch'])
def test_processing_gauge_releases_after_error_or_stale_epoch(outcome):
    async def run():
        adapter, service, _dm, _room = setup()
        adapter._controller = controller()
        adapter._started = False
        calls = []
        async def submit(observation, audio):
            calls.append(observation.request_id)
            raise RuntimeError('SECRET')
        service.submit_audio = submit
        enqueue(adapter)
        if outcome == 'epoch':
            adapter._voice_epoch += 1
        try:
            await adapter.start()
            await asyncio.wait_for(adapter._audio_queue.join(), 1)
            snapshot = adapter.voice_timing_snapshot()
            assert snapshot['queued_utterances'] == snapshot['processing_utterances'] == 0
            assert len(calls) == (1 if outcome == 'error' else 0)
            assert snapshot['recent_requests'][-1]['status'] == ('failed' if outcome == 'error' else 'cancelled')
            assert 'SECRET' not in json.dumps(snapshot)
        finally:
            await adapter.close()
        assert adapter.voice_timing_snapshot()['processing_utterances'] == 0
    asyncio.run(run())
