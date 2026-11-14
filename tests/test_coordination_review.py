from contextlib import asynccontextmanager
import asyncio
import importlib.machinery
import importlib.util
from pathlib import Path
import socket
import threading

from fastapi import FastAPI
import pytest

from gamma.coordination.hosting import loopback_server
from gamma.coordination.store import process_lock


def test_startup_timeout_closes_actual_listener_thread_and_owner_lease(tmp_path):
    release = threading.Event()
    exited = threading.Event()
    lock = tmp_path / 'review.lock'
    @asynccontextmanager
    async def lifespan(_app):
        with process_lock(lock):
            try:
                while not release.is_set():
                    await asyncio.sleep(.005)
                yield
            finally:
                exited.set()
    app = FastAPI(lifespan=lifespan)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    timer = threading.Timer(.2, release.set)
    timer.start()
    try:
        with pytest.raises(RuntimeError, match='failed to start'):
            with loopback_server(app, port, startup_timeout=.05, shutdown_timeout=2):
                pytest.fail('timed-out startup was reported ready')
    finally:
        release.set()
        timer.join(2)
    assert exited.wait(1)
    assert not any(t.name == f'coordination-http-{port}' for t in threading.enumerate())
    with process_lock(lock), socket.socket() as probe:
        probe.bind(('127.0.0.1', port))


def test_loopback_host_refuses_an_occupied_port_without_displacing_its_owner():
    import httpx
    app = FastAPI()
    @app.get('/health')
    def health():
        return {'owner':'original'}
    with loopback_server(app) as origin:
        port = int(origin.rsplit(':',1)[1])
        with pytest.raises(OSError):
            with loopback_server(FastAPI(),port):
                pytest.fail('an occupied listener was replaced')
        assert httpx.get(origin+'/health').json() == {'owner':'original'}


def test_saved_ready_record_requires_same_live_controller_and_unexpired_duration():
    path = Path(__file__).parents[1] / 'scripts/review_coordination_windows.pyw'
    loader = importlib.machinery.SourceFileLoader('review_controller_contract', str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    ready = dict(phase='ready', controller_pid=77, controller_creation_time='original', expires_at=1000)
    assert module.readiness(ready, identity=lambda _:None, now=900)['phase'] == 'unverified'
    assert module.readiness(ready, identity=lambda _:'replacement', now=900)['phase'] == 'unverified'
    assert module.readiness(ready, identity=lambda _:'original', now=1001)['phase'] == 'stopping'
    assert module.readiness(ready, identity=lambda _:'original', now=900)['phase'] == 'ready'
    assert ready['phase'] == 'ready'


@pytest.mark.parametrize('private_voice',[False,True])
def test_mock_review_never_contacts_model_endpoint_and_cleans_up(tmp_path,private_voice):
    import json
    import os
    import secrets
    import select
    import subprocess
    import sys
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import uuid
    import httpx
    observed = []
    class Trap(BaseHTTPRequestHandler):
        def do_GET(self):
            observed.append(self.path)
            self.send_response(500); self.end_headers()
        do_POST = do_GET
        def log_message(self, *_args):
            pass
    trap = ThreadingHTTPServer(('127.0.0.1',0),Trap)
    trap_thread = threading.Thread(target=trap.serve_forever,daemon=True)
    trap_thread.start()
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0)); port = probe.getsockname()[1]
    project = Path(__file__).parents[1]
    credentials = {key:secrets.token_urlsafe(32) for key in ('api_token','password','session_secret')}
    with (tmp_path/'review.log').open('w') as errors:
        child = subprocess.Popen([sys.executable,str(project/'scripts/review_coordination.py'),
            '--session-id',uuid.uuid4().hex,'--model','fixture','--mock','--dashboard-port',str(port),
            '--endpoint',f'http://127.0.0.1:{trap.server_port}','--duration','15']+(['--private-voice'] if private_voice else []),
            cwd=project,env={**os.environ,'PYTHONPATH':str(project/'src')},
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=errors,text=True)
        try:
            child.stdin.write(json.dumps(credentials)+'\n'); child.stdin.flush()
            assert select.select([child.stdout],[],[],25)[0], 'review did not signal readiness'
            ready = json.loads(child.stdout.readline())
            assert ready['phase'] == 'ready' and ready['mock'] is True
            with httpx.Client(base_url=ready['dashboard'],timeout=10,follow_redirects=True) as client:
                login = client.post('/login',data={'username':'isolated-owner','password':credentials['password']})
                assert login.status_code == 200 and login.url.path == '/dashboard/home'
                assert 'responses are synthetic' in login.text
                assert 'Open conversation' in login.text and 'System and recovery' in login.text
                monitor = client.get('/dashboard/monitor')
                assert monitor.status_code == 200 and 'responses are synthetic' in monitor.text
                for path in ('/dashboard/activities','/dashboard/rehearsal','/dashboard/phone'):
                    assert client.get(path).status_code == 200
                response = client.post('/api/coordination/control',json={'audience':'owner_private',
                    'request_id':'mock-work','action':'practice','exercise_id':'retry_budget'})
                assert response.status_code == 200
                import time
                end = time.monotonic()+7
                while time.monotonic() < end:
                    activities = client.get('/api/coordination/status?audience=owner_private').json()['activities']
                    if activities and activities[0]['status'] in {'blocked','needs_checker'}:
                        break
                    time.sleep(.1)
                assert activities[0]['status'] in {'blocked','needs_checker'}
            assert observed == [], 'mock review attempted model traffic'
            child.stdin.write('stop\n'); child.stdin.flush()
            child.wait(timeout=20)
            assert child.returncode == 0
            closed = json.loads(child.stdout.readline())
            assert closed['phase'] == 'closed'
            with pytest.raises(httpx.ConnectError):
                httpx.get(ready['dashboard']+'/health',timeout=2)
        finally:
            if child.poll() is None:
                child.stdin.close()
                try:
                    child.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    child.kill(); child.wait(timeout=10)
            trap.shutdown(); trap.server_close(); trap_thread.join(2)


@pytest.mark.parametrize('name',['review_coordination.py','review_coordination_windows.pyw'])
def test_awake_session_duration_requires_explicit_opt_in_and_remains_finite(name):
    import ast
    source=(Path(__file__).parents[1]/'scripts'/name).read_text()
    tree=ast.parse(source)
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='valid_review_duration')
    namespace={}
    exec(compile(ast.Module(body=[fn],type_ignores=[]),name,'exec'),namespace)
    allowed=namespace['valid_review_duration']
    assert allowed(5) and allowed(1800)
    assert not allowed(1801) and not allowed(43200)
    assert allowed(43200,True) and allowed(57600,True)
    for value in (4,57601,True,5.0,'600'):
        assert not allowed(value,True)
    assert not allowed(43200,1)
