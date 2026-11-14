"""Opt-in isolated HTTP/local-model smoke. Never starts a production service."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import socket
import threading
import time
import urllib.request
import uuid


@contextmanager
def server(app):
    import uvicorn
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    runner = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
    thread = threading.Thread(target=runner.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not runner.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(.02)
    if not runner.started:
        raise RuntimeError('isolated server failed to start')
    try:
        yield f'http://127.0.0.1:{port}'
    finally:
        runner.should_exit = True
        thread.join(timeout=150)
        if thread.is_alive():
            raise RuntimeError('isolated server did not finish bounded work')
        listener.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--endpoint', required=True)
    parser.add_argument('--quiet-review', action='store_true', help='Also observe one actual due discretionary review')
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    if project.name in {'gamma-main', 'gamma-modular-phase1'}:
        raise RuntimeError('run from the isolated checkout only')
    # Do not take over a resident endpoint. A race against unrelated applications
    # remains possible; backend_slot serializes cooperating Gamma requests.
    with urllib.request.urlopen(args.endpoint.rstrip('/') + '/api/ps', timeout=5) as response:
        if json.load(response).get('models') != []:
            raise RuntimeError('selected endpoint is occupied; smoke not started')
    token, password = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    os.environ.update({
        'SHANA_PROJECT_ROOT': str(project), 'GAMMA_ISOLATED_COORDINATION': '1',
        'SHANA_API_AUTH_ENABLED': 'true', 'SHANA_API_BEARER_TOKEN': token,
        'SHANA_DASHBOARD_AUTH_ENABLED': 'true', 'SHANA_DASHBOARD_AUTH_USERNAME': 'isolated-owner',
        'SHANA_DASHBOARD_AUTH_PASSWORD': password, 'SHANA_DASHBOARD_SESSION_SECRET': secrets.token_urlsafe(32),
        'SHANA_DASHBOARD_COOKIE_SECURE': 'false', 'SHANA_LLM_PROVIDER': 'local',
        'SHANA_LLM_ROUTER_ENABLED': 'false', 'SHANA_LOCAL_LLM_ENDPOINT': args.endpoint,
        'SHANA_LOCAL_LLM_MODEL': args.model, 'SHANA_LOCAL_LLM_ENABLE_ROUTING': 'false',
        'SHANA_LOCAL_LLM_KEEP_ALIVE': '0', 'SHANA_LOCAL_LLM_TIMEOUT_SECONDS': '90',
        'GAMMA_PRACTICE_MODEL': args.model,
        'SHANA_DATABASE_URL': 'sqlite:///' + str(project / 'data' / 'coordination' / 'conversation.sqlite3'),
    })
    import httpx
    from gamma.config import settings
    from gamma.coordination.app import create_app
    from gamma.coordination.dashboard import create_app as dashboard_app
    backend = create_app()
    report = {'scope': 'isolated_owner_private', 'model': args.model, 'production_verified': False,
              'checks': {}, 'replies': [], 'run_ids': []}
    session_id = 'isolated-smoke-' + uuid.uuid4().hex
    with server(backend) as api_url:
        os.environ['GAMMA_COORDINATION_API_ORIGIN'] = api_url
        settings.shana_port = int(api_url.rsplit(':', 1)[1])
        with server(dashboard_app()) as dashboard_url:
            with httpx.Client(base_url=dashboard_url, timeout=150) as client:
                assert client.post('/api/monitor/input', json={}).status_code == 401
                login = client.post('/login', data={'username': 'isolated-owner', 'password': password})
                assert login.status_code in {200, 303}, login.status_code
                assert client.get('/dashboard/monitor').status_code == 200
                for path in ('/api/shana/restart', '/api/coding/work'):
                    assert client.post(path, json={}).status_code == 404
                report['checks']['authenticated_monitor_and_no_live_controls'] = True
                def control(action, **values):
                    response = client.post('/api/coordination/control', json={
                        'request_id': uuid.uuid4().hex, 'audience': 'owner_private', 'action': action, **values})
                    response.raise_for_status()
                    return response.json()
                def chat(text):
                    response = client.post('/api/monitor/input', json={
                        'request_id': uuid.uuid4().hex, 'audience': 'owner_private', 'input_mode': 'private_text',
                        'text': text, 'session_id': session_id})
                    response.raise_for_status()
                    value = response.json()['result']['assistant_response']
                    assert not value['speech_text'] and not value['audio_path']
                    report['replies'].append(value['display_text'])
                    return value
                control('wake')
                control('focus', focus='focused')
                chat('I am doing focused homework. Stay quiet unless I ask you. Acknowledge briefly.')
                before = time.monotonic()
                accepted = control('practice', exercise_id='retry_budget', maximum_attempts=3, budget_seconds=240)
                report['dispatch_ms'] = round((time.monotonic() - before) * 1000, 1)
                run_id = accepted['activity']['worker_run']
                report['run_ids'].append(run_id)
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline:
                    run = backend.state.core.store.read('run', run_id)
                    if run['status'] == 'running':
                        break
                    time.sleep(.05)
                report['worker_status_at_chat_send'] = run['status']
                before = time.monotonic()
                chat('Are you still here while that practice runs? One short sentence.')
                report['chat_during_work_ms'] = round((time.monotonic() - before) * 1000, 1)
                control('sleep')
                deadline = time.monotonic() + 240
                while time.monotonic() < deadline:
                    run = backend.state.core.store.read('run', run_id)
                    if run['status'] in {'completed', 'blocked', 'needs_checker', 'stopped'}:
                        break
                    time.sleep(.1)
                assert run['status'] in {'completed', 'blocked', 'needs_checker', 'stopped'}, run['status']
                report['practice_status'] = run['status']
                report['practice_reason'] = run['reason']
                report['checkpoint_ref'] = run['checkpoint_ref']
                wake = control('wake')
                report['wake_catch_up'] = wake['catch_up']
                status = client.get('/api/coordination/status?audience=owner_private')
                status.raise_for_status()
                assert status.json()['catch_up'] == []
                report['checks']['duplicate_catch_up_suppressed'] = True
                natural = chat('Report only the actual practice result and any blocker. Do not claim production changes or general coding competence.')
                report['checks']['natural_status_capability_selected'] = natural['reason_code'] == 'practice_status'
                canonical = chat('practice status')
                assert canonical['reason_code'] == 'practice_status'
                report['checks']['canonical_status_from_evidence'] = True
                if args.quiet_review:
                    control('focus', focus='relaxed')
                    control('permit', exercise_ids=['message_chunks'], maximum_attempts=3, budget_seconds=240)
                    deadline = time.monotonic() + 150
                    while time.monotonic() < deadline:
                        brain = backend.state.core.store.read('brain', 'state')
                        if brain['permit']['remaining_reviews'] < 3 and brain.get('last_decision', {}).get('observed_at', 0) > brain['permit']['expires_at'] - 240:
                            break
                        time.sleep(.1)
                    report['quiet_review'] = brain.get('last_decision')
                    report['checks']['due_quiet_review_observed'] = brain['permit']['remaining_reviews'] < 3
                    if brain.get('last_decision', {}).get('accepted_activity') == 'practice':
                        quiet_run = brain['last_decision']['worker_run']
                        report['run_ids'].append(quiet_run)
                        deadline = time.monotonic() + 240
                        while time.monotonic() < deadline:
                            quiet = backend.state.core.store.read('run', quiet_run)
                            if quiet['status'] in {'completed', 'blocked', 'needs_checker', 'stopped'}:
                                break
                            time.sleep(.1)
                        report['quiet_practice_status'] = quiet['status']
    path = project / 'data' / 'coordination' / ('smoke-' + uuid.uuid4().hex + '.json')
    path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'report': str(path), **report}, indent=2))


if __name__ == '__main__':
    main()
