from concurrent.futures import Future, ThreadPoolExecutor
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from uuid import uuid4
from unittest.mock import Mock, patch

import httpx
import pytest

from gamma.brain.queue import BrainQueue, BrainQueueFull
from gamma.stream.ingress import StreamIngress
from gamma.stream.ingress_store import IngressStore, IngressConflict, IngressFull
from gamma.stream.models import StreamInputEvent


def test_store_concurrent_dedup_conflict_and_terminal_owner(tmp_path):
    path = tmp_path / 'receipts.db'
    store = IngressStore(path, capacity=2)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: IngressStore(path).claim('same', 'digest', 'owner'), range(16)))
    assert sum(fresh for _, fresh in results) == 1
    with pytest.raises(IngressConflict):
        store.claim('same', 'changed', 'owner')
    # A digest is not itself the deduplication key.
    assert store.claim('different', 'digest', 'owner')[1]
    with pytest.raises(IngressFull):
        store.claim('overflow', 'third', 'owner')
    assert not store.transition('same', 'outsider', 'running')
    assert store.transition('same', 'owner', 'running')
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda state: store.transition('same', 'owner', state), ['completed', 'uncertain']))
    assert sum(outcomes) == 1
    assert not store.transition('same', 'owner', 'failed')
    assert store.stats()['active_count'] == 1


def test_store_retention_preserves_uncertain_and_recent_terminal(tmp_path):
    store = IngressStore(tmp_path / 'receipts.db', capacity=2, history_limit=2, retention_seconds=0.02)
    store.claim('uncertain', 'a', 'owner')
    store.orphan('owner')
    store.claim('done', 'b', 'new')
    store.transition('done', 'new', 'failed')
    with pytest.raises(IngressFull):
        store.claim('full', 'c', 'new')
    time.sleep(0.04)
    store.claim('later', 'd', 'new')
    assert store.get('uncertain')['state'] == 'uncertain'
    assert store.get('done') is None


def test_ingress_receipt_is_fast_and_tracks_delivery_and_cancellation(tmp_path):
    runtime = StreamIngress(tmp_path / 'receipts.db')
    queue = BrainQueue(capacity=2)
    entered, release = threading.Event(), threading.Event()
    delivery = Future()
    def decide():
        entered.set()
        assert release.wait(3)
        return delivery
    def submit(event, function):
        return runtime.submit(event, speech=False, fast=True, submit=lambda wrap: queue.submit(lambda: wrap(function)))
    first = StreamInputEvent(event_id='one', kind='chat_message', text='fictional')
    try:
        receipt = submit(first, decide)
        assert entered.wait(1) and receipt['state'] in {'accepted', 'running'}
        assert submit(first, lambda: pytest.fail('duplicate executed'))['duplicate']
        second = StreamInputEvent(event_id='two', kind='chat_message')
        submit(second, lambda: pytest.fail('cancelled job executed'))
        assert runtime.cancel('two')['cancelled']
        assert runtime.receipt('two')['state'] == 'cancelled'
        assert not runtime.cancel('one')['cancelled']
        release.set()
        queue.submit(lambda: None).result(2)
        assert runtime.receipt('one')['state'] == 'running'
        delivery.set_result(None)
        assert runtime.receipt('one')['state'] == 'completed'
        assert runtime._pending == {}
    finally:
        release.set()
        queue.close()


def test_failed_queue_submission_and_bad_result_are_visible(tmp_path):
    runtime = StreamIngress(tmp_path / 'receipts.db')
    event = StreamInputEvent(event_id='full', kind='system')
    def fail(_):
        raise BrainQueueFull()
    with pytest.raises(BrainQueueFull):
        runtime.submit(event, speech=False, fast=True, submit=fail)
    assert runtime.receipt('full')['reason_code'] == 'queue_unavailable'
    event.event_id = 'broken'
    future = Future()
    runtime.submit(event, speech=False, fast=True, submit=lambda _: future)
    future.set_result(None)
    assert runtime.receipt('broken')['state'] == 'failed'
    assert not runtime._pending


def test_real_http_receipts_shared_brain_cancel_and_crash(tmp_path):
    source = Path(__file__).resolve().parents[1]
    (tmp_path / 'src').symlink_to(source / 'src', target_is_directory=True)
    (tmp_path / 'data/memory').mkdir(parents=True)
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/app.example.toml').write_text('')
    (tmp_path / 'pyproject.toml').write_text('[project]\nname="fixture"\nversion="0"\n')
    script = tmp_path / 'server.py'
    script.write_text('''
import os, time
from pathlib import Path
from gamma.main import app
from gamma.api import routes
import uvicorn
brain = routes.get_stream_brain()
original = brain.handle_event
def handle(event, **kwargs):
    if event.text == 'controlled-block':
        Path('entered').touch()
        while not Path('release').exists():
            time.sleep(.02)
    return original(event, **kwargs)
brain.handle_event = handle
uvicorn.run(app, host='127.0.0.1', port=int(os.environ['FIXTURE_PORT']), access_log=False)
''')
    with socket.socket() as reserve:
        reserve.bind(('127.0.0.1', 0))
        port = reserve.getsockname()[1]
    token = uuid4().hex
    env = dict(os.environ, SHANA_PROJECT_ROOT=str(tmp_path), PYTHONPATH=str(tmp_path / 'src'),
               SHANA_DATABASE_URL=f"sqlite:///{tmp_path / 'data/memory/fixture.db'}",
               SHANA_LLM_PROVIDER='mock', SHANA_LLM_ROUTER_ENABLED='false', SHANA_TTS_PROVIDER='stub',
               SHANA_STT_PROVIDER='stub', SHANA_PROACTIVE_IDLE_ENABLED='false',
               SHANA_AUDIO_UNDERSTANDING_ENABLED='false', SHANA_API_AUTH_ENABLED='true',
               SHANA_API_BEARER_TOKEN=token, FIXTURE_PORT=str(port), GAMMA_LIFECYCLE_MANAGER='')
    log = (tmp_path / 'server.log').open('wb')
    def launch():
        return subprocess.Popen([sys.executable, str(script)], cwd=tmp_path, env=env, stdout=log, stderr=log)
    server = launch()
    client = httpx.Client(base_url=f'http://127.0.0.1:{port}', headers={'Authorization': f'Bearer {token}'}, timeout=3)
    def post_job(event):
        return client.post('/v1/stream/jobs', json={'schema_version': 1, **event})
    def wait_for(predicate):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                if predicate():
                    return
            except httpx.ConnectError:
                pass
            if server.poll() is not None:
                raise AssertionError((tmp_path / 'server.log').read_text()[-3000:])
            time.sleep(.03)
        raise AssertionError('fixture timed out')
    try:
        wait_for(lambda: client.get('/health').status_code == 200)
        assert httpx.post(f'http://127.0.0.1:{port}/v1/stream/jobs', json={}).status_code == 401
        event = {'event_id': 'blocked', 'kind': 'system', 'text': 'controlled-block'}
        for invalid in (None, True, '1', 0, 2):
            malformed = {**event, 'schema_version': invalid}
            assert client.post('/v1/stream/jobs', json=malformed).status_code == 422
        assert client.post('/v1/stream/jobs', json=event).status_code == 422
        assert client.get('/v1/stream/jobs/blocked').status_code == 404
        response = post_job(event)
        assert response.status_code == 202, response.text
        assert response.json()['schema_version'] == 1
        wait_for(lambda: (tmp_path / 'entered').exists())
        assert client.get('/health').status_code == 200
        from gamma.integrations.twitch.client import GammaStreamClient
        from gamma.integrations.twitch.worker import TwitchIrcWorker, TwitchWorkerConfig
        from gamma.config import settings
        trust = Mock()
        trust.trust_level_for.return_value = 'new_viewer'
        with patch.object(settings, 'api_auth_enabled', True):
            irc = TwitchIrcWorker(config=TwitchWorkerConfig(channel='room', bot_username='bot', oauth_token='fictional'),
                                  client=GammaStreamClient(base_url=str(client.base_url), bearer_token=token,
                                                           timeout_seconds=2, queued=True),
                                  trust_store=trust, state_path=tmp_path / 'irc-state.json')
            line = '@id=message-one;user-id=42;display-name=Fixture :fixture!fixture@localhost PRIVMSG #room :hello'
            posted = irc.handle_line(line)
            repeated = irc.handle_line(line)
            assert posted['state'] == 'accepted' and not posted['duplicate']
            assert repeated['duplicate'] and repeated['job_id'] == posted['job_id']
            import asyncio
            import hashlib
            from gamma.integrations.twitch.eventsub import TwitchEventSubWorker, TwitchEventSubConfig
            import websockets
            notification = {
                'metadata': {'message_type': 'notification', 'message_id': 'eventsub-fixture'},
                'payload': {'subscription': {'id': 'fixture-sub', 'type': 'channel.follow'},
                            'event': {'user_id': '42', 'user_name': 'Fixture'}}}
            async def eventsub_wire():
                worker = TwitchEventSubWorker(
                    config=TwitchEventSubConfig(client_id='fixture', oauth_token='fictional', broadcaster_user_id='42'),
                    client=GammaStreamClient(base_url=str(client.base_url), bearer_token=token,
                                             timeout_seconds=2, queued=True), state_path=tmp_path / 'eventsub-state.json')
                async def serve(connection):
                    await connection.send(json.dumps(notification))
                    await connection.send(json.dumps(notification))
                    await connection.send(json.dumps({'metadata': {'message_type': 'session_keepalive'}}))
                    await connection.close()
                async with websockets.serve(serve, '127.0.0.1', 0) as ws_server:
                    ws_port = ws_server.sockets[0].getsockname()[1]
                    async with websockets.connect(f'ws://127.0.0.1:{ws_port}') as connection:
                        await asyncio.wait_for(worker._run_socket(connection, reconnects=0), timeout=5)
                assert json.loads((tmp_path / 'eventsub-state.json').read_text())['last_message_kind'] == 'keepalive'
            asyncio.run(eventsub_wire())
            job_id = 'eventsub-' + hashlib.sha256(b'42:eventsub-fixture').hexdigest()
            assert client.get(f'/v1/stream/jobs/{job_id}').json()['state'] == 'accepted'
            assert client.get('/v1/stream/jobs/status').json()['active_count'] == 3
        assert client.get('/v1/brain/status').json()['active']
        assert post_job(event).json()['duplicate']
        assert post_job({**event, 'text': 'different'}).status_code == 409
        queued = {**event, 'event_id': 'queued', 'text': 'fictional'}
        assert post_job(queued).status_code == 202
        assert client.post('/v1/stream/jobs/queued/cancel').json()['cancelled']
        assert not client.post('/v1/stream/jobs/blocked/cancel').json()['cancelled']
        assert client.get('/v1/stream/jobs/queued').json()['state'] == 'cancelled'
        assert client.post('/v1/stream/jobs', content=b'x' * 65537).status_code == 413
        # One stalled turn, one IRC message and one EventSub notification own admission.
        for index in range(29):
            waiting = {**queued, 'event_id': f'load-{index}'}
            accepted = post_job(waiting)
            assert accepted.status_code == 202, accepted.text
        assert client.get('/v1/stream/jobs/status').json()['active_count'] == 32
        assert post_job({**queued, 'event_id': 'overflow'}).status_code == 503
        assert client.get('/v1/stream/jobs/overflow').status_code == 404
        assert client.post('/v1/stream/jobs/load-0/cancel').json()['cancelled']
        assert post_job({**queued, 'event_id': 'replacement'}).status_code == 202
        assert client.get('/v1/stream/jobs/status').json()['active_count'] == 32
        assert client.get('/v1/brain/status').json()['active']
        # Kill only this fixture core while its job is active. Never replay on restart.
        server.kill()
        server.wait(timeout=5)
        server = launch()
        wait_for(lambda: client.get('/health').status_code == 200)
        recovered = client.get('/v1/stream/jobs/blocked').json()
        assert recovered['state'] == 'uncertain' and recovered['reason_code'] == 'owner_exited'
        assert post_job(event).json()['duplicate']
        (tmp_path / 'release').touch()
        queued['event_id'] = 'after-restart'
        assert post_job(queued).status_code == 202
        wait_for(lambda: client.get('/v1/stream/jobs/after-restart').json()['state'] == 'completed')
        assert client.get('/v1/stream/jobs/status').json()['active_count'] == 0
    finally:
        (tmp_path / 'release').touch()
        server.terminate()
        server.wait(timeout=15)
        client.close()
        log.close()


def test_uncertain_owner_checks_preserve_live_foreign_and_unknown(tmp_path):
    import psutil
    runtime = StreamIngress(tmp_path / 'receipts.db')
    runtime.store.claim('live', 'a', runtime.owner)
    runtime.store.claim('foreign', 'b', json.dumps(['different-host', 12, 1.0]))
    runtime.reconcile()
    assert runtime.store.stats()['active_count'] == 2
    with patch('gamma.stream.ingress.psutil.Process', side_effect=psutil.AccessDenied()):
        runtime.reconcile()
    assert runtime.store.stats()['active_count'] == 2


def test_raised_cancellation_does_not_strand_running_receipt(tmp_path):
    from concurrent.futures import CancelledError
    runtime = StreamIngress(tmp_path / 'receipts.db')
    queue = BrainQueue()
    def decide():
        raise CancelledError()
    try:
        runtime.submit(StreamInputEvent(event_id='cancel-error', kind='system'), speech=False, fast=True,
                       submit=lambda wrap: queue.submit(lambda: wrap(decide)))
        queue.submit(lambda: None).result(2)
        assert runtime.receipt('cancel-error')['state'] == 'failed'
        assert runtime.store.stats()['active_count'] == 0
    finally:
        queue.close()
