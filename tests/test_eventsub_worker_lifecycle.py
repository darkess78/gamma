import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
from uuid import uuid4

import httpx
import psutil
import websockets

from gamma.integrations.twitch.eventsub_readiness import EventSubReadiness


def test_subscription_readiness_and_revocation_are_not_pid_liveness():
    ready = EventSubReadiness()
    def observe(status, good=1, failed=0):
        return ready.observe(status, connected=True, subscriptions_ok=good, subscriptions_failed=failed)
    assert observe('connected', 0) == 'degraded'
    assert observe('connected', 1, 1) == 'degraded'
    assert observe('connected') == 'ready'
    assert observe('revoked') == 'degraded'
    assert observe('connected') == 'degraded'
    assert observe('connecting') == 'starting'
    assert observe('connected') == 'ready'
    assert observe('subscription_error') == 'failed'


def test_dashboard_controls_real_eventsub_child_with_local_protocol_servers(tmp_path):
    source = Path(__file__).resolve().parents[1]
    # Copy only tracked-style source, then redirect the three external endpoint
    # constants in this disposable fixture. Worker behavior is otherwise unchanged.
    shutil.copytree(source / 'src', tmp_path / 'src', ignore=shutil.ignore_patterns('__pycache__'))
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/app.example.toml').write_text('')
    (tmp_path / 'pyproject.toml').write_text('')
    allow_subscription, subscription_entered, revoke, stop, disconnect = (threading.Event() for _ in range(5))
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def reply(self, code, payload):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_GET(self):
            self.reply(200, {'client_id': 'fixture', 'user_id': '42', 'scopes': ['moderator:read:followers']})
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            subscription_entered.set()
            allow_subscription.wait(7)
            self.reply(202, {'data': [{'id': 'fixture-sub', 'status': 'enabled'}]})
    http_server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    http_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
    http_thread.start()
    ws_ready = threading.Event()
    ws_port = []
    async def connection(ws):
        await ws.send(json.dumps({'metadata': {'message_type': 'session_welcome'},
                                  'payload': {'session': {'id': uuid4().hex}}}))
        sent_revocation = False
        try:
            while not stop.is_set():
                if disconnect.is_set():
                    disconnect.clear()
                    await ws.close()
                    return
                if revoke.is_set() and not sent_revocation:
                    await ws.send(json.dumps({'metadata': {'message_type': 'revocation'},
                                              'payload': {'subscription': {'id': 'fixture-sub', 'type': 'channel.follow'}}}))
                    sent_revocation = True
                await ws.send(json.dumps({'metadata': {'message_type': 'session_keepalive'}}))
                await asyncio.sleep(.1)
        except websockets.ConnectionClosed:
            pass
    async def serve():
        async with websockets.serve(connection, '127.0.0.1', 0) as server:
            ws_port.append(server.sockets[0].getsockname()[1])
            ws_ready.set()
            while not stop.is_set():
                await asyncio.sleep(.05)
    ws_thread = threading.Thread(target=lambda: asyncio.run(serve()), daemon=True)
    ws_thread.start()
    assert ws_ready.wait(3)
    module_path = tmp_path / 'src/gamma/integrations/twitch/eventsub.py'
    text = module_path.read_text().replace('wss://eventsub.wss.twitch.tv/ws', f'ws://127.0.0.1:{ws_port[0]}')
    base = f'http://127.0.0.1:{http_server.server_port}'
    text = text.replace('https://api.twitch.tv/helix/eventsub/subscriptions', base + '/subscriptions')
    text = text.replace('https://id.twitch.tv/oauth2/validate', base + '/validate')
    module_path.write_text(text)
    with socket.socket() as reserve:
        reserve.bind(('127.0.0.1', 0))
        port = reserve.getsockname()[1]
    password = uuid4().hex
    env = dict(os.environ, SHANA_PROJECT_ROOT=str(tmp_path), PYTHONPATH=str(tmp_path / 'src'),
               SHANA_DASHBOARD_AUTH_ENABLED='true', SHANA_DASHBOARD_AUTH_USERNAME='fixture',
               SHANA_DASHBOARD_AUTH_PASSWORD=password, SHANA_DASHBOARD_SESSION_SECRET=uuid4().hex,
               SHANA_DASHBOARD_COOKIE_SECURE='false', SHANA_TWITCH_CLIENT_ID='fixture',
               SHANA_TWITCH_OAUTH_TOKEN='fictional-token', SHANA_TWITCH_BROADCASTER_USER_ID='42',
               SHANA_TWITCH_MODERATOR_USER_ID='42', GAMMA_LIFECYCLE_MANAGER='')
    log = (tmp_path / 'dashboard.log').open('wb')
    dashboard = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'gamma.dashboard.main:app', '--host', '127.0.0.1',
                                  '--port', str(port), '--no-access-log'], cwd=tmp_path, env=env, stdout=log, stderr=log)
    children = []
    client = httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=15)
    def wait_for(check):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            try:
                if check():
                    return
            except httpx.ConnectError:
                pass
            time.sleep(.05)
        raise AssertionError('fixture condition timed out')
    def status():
        return next(item for item in client.get('/api/workers').json()['workers'] if item['worker_id'] == 'twitch_eventsub')
    try:
        wait_for(lambda: client.get('/health').status_code == 200)
        assert client.post('/api/workers/twitch_eventsub/start').status_code == 401
        assert client.post('/login', data={'username': 'fixture', 'password': password}).status_code == 303
        started = client.post('/api/workers/twitch_eventsub/start').json()
        assert started['ok'], started
        first = psutil.Process(started['worker']['pid'])
        children.append(first)
        assert first.ppid() == dashboard.pid
        assert subscription_entered.wait(3)
        wait_for(lambda: status()['observed_state'] == 'starting')
        allow_subscription.set()
        wait_for(lambda: status()['observed_state'] == 'ready')
        assert requests[0]['transport']['method'] == 'websocket'
        allow_subscription.clear()
        disconnect.set()
        wait_for(lambda: status()['observed_state'] == 'degraded')
        wait_for(lambda: len(requests) == 2)
        wait_for(lambda: status()['observed_state'] == 'starting')
        allow_subscription.set()
        wait_for(lambda: status()['observed_state'] == 'ready')
        revoke.set()
        wait_for(lambda: status()['observed_state'] == 'degraded')
        time.sleep(1.1)
        assert status()['observed_state'] == 'degraded'
        logs = client.get('/api/worker-logs', params={'workers': 'twitch_eventsub', 'event_type': 'connection',
                                                     'trace_id': started['trace_id']}).json()
        assert logs['events'] and 'fictional-token' not in json.dumps(logs)
        assert all(event['worker_id'] == 'twitch_eventsub' and event['trace_id'] == started['trace_id'] for event in logs['events'])
        revoke.clear()
        restarted = client.post('/api/workers/twitch_eventsub/restart').json()
        assert restarted['ok'], restarted
        second = psutil.Process(restarted['worker']['pid'])
        children.append(second)
        assert second.pid != first.pid and not first.is_running()
        wait_for(lambda: status()['observed_state'] == 'ready')
        assert client.post('/api/twitch/eventsub/stop').json()['ok']
        assert not second.is_running() and status()['desired_state'] == 'stopped'
        legacy = client.post('/api/twitch/eventsub/start').json()
        assert legacy['ok'] and legacy['worker'] == 'twitch_eventsub'
        third = psutil.Process(legacy['process']['pid'])
        children.append(third)
        wait_for(lambda: status()['observed_state'] == 'ready')
        assert client.post('/api/workers/twitch_eventsub/stop').json()['ok']
        assert not third.is_running() and status()['observed_state'] == 'stopped'
        assert client.get('/health').status_code == 200
    finally:
        allow_subscription.set()
        try:
            client.post('/api/workers/twitch_eventsub/stop')
        except httpx.HTTPError:
            for child in children:
                if child.is_running() and child.status() != psutil.STATUS_ZOMBIE:
                    child.terminate()
        dashboard.terminate()
        dashboard.wait(timeout=10)
        client.close()
        log.close()
        stop.set()
        ws_thread.join(5)
        http_server.shutdown()
        http_server.server_close()
        http_thread.join(3)
