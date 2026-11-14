"""Opt-in real-model private Monitor journey with a real isolated process crash.

Two explicit test gates hold a durably saved edit and a check boundary. They
control interruption timing, not model choices, proposals, checks or evaluation.
No production service, listener, database or configuration is changed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import select
import subprocess
import sys
import time
import urllib.request
import uuid


def publish(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temp.replace(path)


def until(read, accept, timeout=150):
    end = time.monotonic() + timeout
    value = None
    while time.monotonic() < end:
        value = read()
        if accept(value):
            return value
        time.sleep(.1)
    raise AssertionError('bounded observation timed out: ' + str(value)[:500])


def child(project, state):
    from verify_coordination_slice import server
    from gamma.config import settings
    from gamma.coordination.app import create_app
    from gamma.coordination.dashboard import create_app as dashboard_app
    import gamma
    assert Path(gamma.__file__).resolve().is_relative_to(project / 'src')
    app = create_app()
    with server(app) as api:
        os.environ['GAMMA_COORDINATION_API_ORIGIN'] = api
        if os.environ.get('GAMMA_COORDINATION_PROBE_SEED') == '1':
            with app.state.core.store.transaction(app.state.core.token) as db:
                brain = app.state.core._brain(db)
                brain['working_notes'].setdefault('authored_starting_practice_intention',
                    'I want to improve my handling of recoverable failures. Quiet practice would be worthwhile when permitted.')
                app.state.core.store.put(db, 'brain', 'state', brain)
        runner = app.state.core.runner
        if os.environ.get('GAMMA_COORDINATION_PROBE_DETERMINISTIC') == '1':
            from gamma.schemas.response import AssistantResponse, BoundedStateUpdates
            def decisions(text, context):
                values = json.loads(context)
                activities = values['activities']
                pending = next((a['id'] for a in activities if a['worker_run'] is None), None)
                original = next((a['id'] for a in activities if a['goal'] == 'retry_budget' and a['worker_run']), None)
                if text.startswith('Quiet-time review'):
                    action, target, exercise = 'practice', None, 'retry_budget'
                elif text.startswith('When your current'):
                    action, target, exercise = 'request_practice', None, 'message_chunks'
                elif text.startswith('Actually, switch'):
                    action, target, exercise = 'switch', pending, None
                elif text.startswith('Pause the new'):
                    action, target, exercise = 'pause', pending, None
                elif text.startswith('Resume the original'):
                    action, target, exercise = 'resume', original, None
                elif text.startswith('How did the original'):
                    action, target, exercise = 'report_status', original, None
                else:
                    return AssistantResponse(display_text='Here with you.', delivery_mode='text_only')
                return AssistantResponse(delivery_mode='silent', response_action='stay_silent',
                    state_updates=BoundedStateUpdates(activity_action=action, activity_run_id=target, practice_exercise=exercise))
            app.state.core.draft = decisions
            runner.propose = lambda *_: {'solution.py': 'def should_retry(error, attempt, maximum_attempts):\n    return isinstance(error, str) and error in ("busy", "rate_limit") and type(attempt) is int and type(maximum_attempts) is int and 1 <= attempt < maximum_attempts\n'}
        original_save, original_check = runner._save, runner.check
        def save(run_id, checkpoint, **values):
            result = original_save(run_id, checkpoint, **values)
            if ((state / 'hold-candidate').exists() and not (state / 'candidate-ready.json').exists()
                    and any(t['stage'] == 'candidate_saved' for t in checkpoint['tasks'])):
                publish(state / 'candidate-ready.json', {'run_id': run_id, 'checkpoint_ref': result['checkpoint_ref']})
                until(lambda: (state / 'hold-candidate').exists(), lambda held: not held, 300)
            return result
        def check(workspace, spec):
            if (state / 'hold-checks').exists():
                publish(state / 'checks-ready.json', {'check': spec.name})
                until(lambda: (state / 'hold-checks').exists(), lambda held: not held, 180)
            return original_check(workspace, spec)
        runner._save, runner.check = save, check
        settings.shana_port = int(api.rsplit(':', 1)[1])
        with server(dashboard_app()) as dashboard:
            publish(state / 'ready.json', {'pid': os.getpid(), 'api': api, 'dashboard': dashboard,
                                          'source': str(Path(gamma.__file__).resolve())})
            while True:
                time.sleep(.2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--endpoint', required=True)
    parser.add_argument('--child-state', type=Path)
    parser.add_argument('--buttons-only', action='store_true', help='Actual browser controls with mock inference and no work dispatch')
    parser.add_argument('--deterministic', action='store_true', help='Scripted decisions/proposal, actual HTTP/browser/checks/process recovery')
    parser.add_argument('--seed-practice-intention', action='store_true', help='Authored starting intention fixture, not evidence of learned interest')
    parser.add_argument('--accept-quiet-wait', action='store_true',
                        help='retain a real wait choice, then request the connected exercise explicitly')
    parser.add_argument('--continue-state', type=Path, help='Continue the paused user request from a completed isolated proof')
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    if os.name != 'posix' or project.name in {'gamma-main', 'gamma-modular-phase1'}:
        raise RuntimeError('use the separate Gamma-PC checkout')
    if args.child_state:
        child(project, args.child_state)
        return
    if not args.buttons_only and not args.deterministic:
        with urllib.request.urlopen(args.endpoint.rstrip('/') + '/api/ps', timeout=5) as response:
            if json.load(response).get('models'):
                raise RuntimeError('selected endpoint occupied; no inference sent')
    from gamma.coordination.store import private_path
    state = private_path(args.continue_state) if args.continue_state else project / 'data' / 'coordination' / ('foundation-' + uuid.uuid4().hex)
    if not state.is_relative_to(project / 'data' / 'coordination'):
        raise RuntimeError('continuation must remain in isolated coordination state')
    state.mkdir(parents=True, mode=0o700, exist_ok=bool(args.continue_state))
    report_path = state / ('continuation-' + uuid.uuid4().hex + '.json' if args.continue_state else 'report.json')
    token, password = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    env = {**os.environ,
        'PYTHONPATH': str(project / 'src'), 'SHANA_PROJECT_ROOT': str(project),
        'GAMMA_ISOLATED_COORDINATION': '1', 'GAMMA_COORDINATION_STATE_DIR': str(state),
        'SHANA_API_AUTH_ENABLED': 'true', 'SHANA_API_BEARER_TOKEN': token,
        'SHANA_DASHBOARD_AUTH_ENABLED': 'true', 'SHANA_DASHBOARD_AUTH_USERNAME': 'isolated-owner',
        'SHANA_DASHBOARD_AUTH_PASSWORD': password, 'SHANA_DASHBOARD_SESSION_SECRET': secrets.token_urlsafe(32),
        'SHANA_DASHBOARD_COOKIE_SECURE': 'false', 'SHANA_LLM_PROVIDER': 'mock' if args.buttons_only or args.deterministic else 'local',
        'SHANA_LLM_ROUTER_ENABLED': 'false', 'SHANA_LOCAL_LLM_ENDPOINT': args.endpoint,
        'SHANA_LOCAL_LLM_MODEL': args.model, 'SHANA_LOCAL_LLM_ENABLE_ROUTING': 'false',
        'SHANA_LOCAL_LLM_KEEP_ALIVE': '0', 'SHANA_LOCAL_LLM_TIMEOUT_SECONDS': '90',
        'GAMMA_PRACTICE_MODEL': args.model,
        'SHANA_DATABASE_URL': 'sqlite:///' + str(state / 'conversation.sqlite3')}
    if args.deterministic:
        env['GAMMA_COORDINATION_PROBE_DETERMINISTIC'] = '1'
    if args.seed_practice_intention:
        env['GAMMA_COORDINATION_PROBE_SEED'] = '1'
    import httpx
    from gamma.coordination.store import Store
    from gamma.coding.practice_artifacts import ArtifactStore
    store = Store(state)
    artifacts = ArtifactStore(state / 'artifacts')
    report = {'scope': 'isolated_owner_private', 'model': args.model, 'production_verified': False,
              'checks': {}, 'interactions': [], 'processes': [], 'fixture_gates': ['candidate_saved', 'before_check']}
    if args.buttons_only:
        report.update(model='mock_no_inference', fixture_gates=[])
    if args.deterministic:
        report.update(model='scripted_fixture_no_inference', fixture_gates=['scripted_decisions', 'scripted_proposal', 'candidate_saved', 'before_check'])
    if args.seed_practice_intention:
        report['starting_state'] = {'authored_practice_intention': True, 'learned_interest_claim': False}
    process = None
    log = None
    client = None
    browser_log = (state / 'browser.log').open('w')
    browser = subprocess.Popen(['node', str(project / 'scripts' / 'coordination_browser_probe.cjs')],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=browser_log, text=True)
    report['browser_pid'] = browser.pid
    def ui(value):
        browser.stdin.write(json.dumps(value) + '\n')
        browser.stdin.flush()
        if not select.select([browser.stdout], [], [], 180)[0]:
            raise AssertionError('browser observation timed out')
        result = json.loads(browser.stdout.readline())
        assert result.get('ok'), result
        return result
    def record(name, value=True):
        report['checks'][name] = value
        publish(report_path, report)
        print(json.dumps({'step': name, 'value': value}), flush=True)
    def start():
        nonlocal process, log, client
        (state / 'ready.json').unlink(missing_ok=True)
        log = (state / ('host-' + str(len(report['processes'])) + '.log')).open('w')
        process = subprocess.Popen([sys.executable, __file__, '--model', args.model, '--endpoint', args.endpoint,
                                    '--child-state', str(state)], cwd=project, env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=log)
        ready = until(lambda: json.loads((state / 'ready.json').read_text()) if (state / 'ready.json').exists() else None,
                      lambda value: value is not None, 30)
        assert ready['pid'] == process.pid
        report['processes'].append(ready)
        client = httpx.Client(base_url=ready['dashboard'], timeout=150)
        assert client.post('/api/monitor/input', json={}).status_code == 401
        assert client.post('/login', data={'username': 'isolated-owner', 'password': password}).status_code in {200, 303}
        assert client.get('/dashboard/monitor').status_code == 200
        for path in ('/api/shana/restart', '/api/coding/work'):
            assert client.post(path, json={}).status_code == 404
        ui({'op': 'start', 'url': ready['dashboard'], 'username': 'isolated-owner', 'password': password})
        return ready
    def control(action, **values):
        response = client.post('/api/coordination/control', json={
            'request_id': uuid.uuid4().hex, 'audience': 'owner_private', 'action': action, **values})
        response.raise_for_status()
        return response.json()
    def chat(text, request_id=None):
        before = time.monotonic()
        if request_id:
            response = client.post('/api/monitor/input', json={
                'request_id': request_id, 'audience': 'owner_private', 'input_mode': 'private_text',
                'text': text, 'session_id': 'monitor-private'})
            response.raise_for_status()
            value = response.json()['result']['assistant_response']
        else:
            observed = ui({'op': 'chat', 'text': text})
            value = observed['body']['result']['assistant_response']
            request_id = observed['request']['request_id']
            assert observed['request']['synthesize_speech'] is False
            if value['display_text']:
                assert value['display_text'] in observed['visible']
        assert not value['speech_text'] and not value['audio_path']
        assert not value['reason_code'].startswith('contract_fallback'), value
        report['interactions'].append({'request_id': request_id, 'input': text, 'response': value,
                                       'elapsed_ms': round((time.monotonic() - before) * 1000, 1)})
        publish(report_path, report)
        return value
    def runs():
        with store.transaction() as db:
            return store.list(db, 'run')
    try:
        if args.continue_state:
            previous = json.loads((state / 'report.json').read_text())
            assert previous['checks']['complete_connected_journey'] is True
            target = previous['requested_activity_id']
            intention = store.read('activity', target)
            original = store.read('run', previous['original_run_id'])
            assert intention['status'] == 'paused' and intention['worker_run'] is None
            report.update(continued_from=str(state / 'report.json'), requested_activity_id=target, fixture_gates=[])
            start()
            reply = chat(f'Resume the {intention["goal"]} request, {target}, now. This is the paused request we already authorized.')
            assert reply['reason_code'] == 'activity_control', reply
            final = until(lambda: store.read('run', target), lambda run: run is not None and run['status'] in {'completed', 'blocked', 'needs_checker', 'stopped'}, 240)
            assert final['expires_at'] == intention['expires_at'] and final['maximum_attempts'] == intention['maximum_attempts']
            assert store.read('run', original['id'])['checkpoint_ref'] == original['checkpoint_ref']
            reply = chat(f'How did that {intention["goal"]} request turn out? Give me the actual outcome.')
            assert reply['reason_code'] == 'practice_status' and target in reply['display_text'], reply
            assert ('passed its protected checks' in reply['display_text']) == (final['status'] == 'completed')
            report['final_run'] = final
            report['browser'] = ui({'op': 'finish', 'screenshot': str(state / 'monitor-continuation.png')})
            assert report['browser']['errors'] == [] and report['browser']['sockets'] == []
            record('deferred_user_request_executed_and_reported_with_original_limits')
            return
        if args.buttons_only:
            start()
            for action, focus, expected in [('focus', 'focused', 'focused'), ('sleep', None, 'Asleep'), ('wake', None, 'Awake')]:
                result = ui({'op': 'control', 'action': action, 'focus': focus, 'expected': expected})
                assert result['body']['ok'] and expected in result['visible']
            assert store.read('brain', 'state')['awake'] and store.read('brain', 'state')['focus'] == 'focused'
            result = ui({'op': 'finish', 'screenshot': str(state / 'monitor-buttons.png')})
            assert result['errors'] == [] and result['sockets'] == []
            report['browser'] = result
            record('real_browser_focus_sleep_wake_buttons_without_inference')
            return
        (state / 'hold-candidate').touch()
        start()
        record('authenticated_private_hosts')
        control('focus', focus='focused')
        chat('I am concentrating on lighthouse homework for a while. Keep interruptions down; we can still chat if I address you. '
             'Our standing direction is for you to develop your coding ability. You have room to choose useful quiet activities while I study.')
        control('permit', exercise_ids=['retry_budget', 'message_chunks'], maximum_runs=2,
                maximum_attempts=3, budget_seconds=1500)
        reviewed = until(lambda: store.read('brain', 'state'),
                         lambda brain: brain['permit']['remaining_reviews'] < 3 and
                         brain.get('last_decision', {}).get('observed_at', 0) > brain['permit']['expires_at'] - 1500,
                         150)
        report['quiet_review'] = reviewed['last_decision']
        publish(report_path, report)
        self_started = reviewed['last_decision']['accepted_activity'] == 'practice'
        if not self_started and args.accept_quiet_wait:
            assert reviewed['last_decision']['accepted_activity'] == 'wait', reviewed['last_decision']
            record('real_model_chose_wait_without_dispatch')
            reply = chat('Please start the retry_budget exercise now under the existing permission. Keep it quiet while I study.')
            assert reply['reason_code'] in {'activity_waiting', 'practice_queued'}, reply
        else:
            assert self_started, reviewed['last_decision']
        original = until(runs, lambda values: bool(values), 150)[0]
        original_id = original['id']
        report['original_run_id'] = original_id
        assert store.read('activity', original_id)['origin'] == ('authorized_quiet_time' if self_started else 'owner_request')
        record('scripted_quiet_start' if args.deterministic else
               'real_model_self_started_while_focused' if self_started else 'real_model_accepted_requested_quiet_work')
        until(lambda: artifacts.get(store.read('run', original_id)['checkpoint_ref']),
              lambda cp: any(t['stage'] in {'inference_started', 'candidate_saved'} for t in cp['tasks']))
        chat('Are you still here? A quick ordinary reply is fine while you work.')
        record('conversation_during_authorized_work')
        until(lambda: (state / 'candidate-ready.json').exists(), bool)
        next_exercise = 'message_chunks' if original['exercise_id'] == 'retry_budget' else 'retry_budget'
        reply = chat(f'When your current exercise is finished, please do the {next_exercise} exercise next. No need to switch immediately.')
        assert reply['reason_code'] == 'activity_waiting', reply
        activities = client.get('/api/coordination/status?audience=owner_private').json()['activities']
        pending = next(a for a in activities if a['worker_run'] is None and a['goal'] == next_exercise)
        report['requested_activity_id'] = pending['id']
        reply = chat(f'Actually, switch to that waiting {next_exercise} request now. Preserve your original work so we can return to it.')
        assert reply['reason_code'] == 'activity_control', reply
        assert store.read('run', original_id)['desired_state'] == 'pause'
        reply = chat(f'Pause the new {next_exercise} request too, before it starts. Keep both activities for later.')
        assert reply['reason_code'] == 'activity_control', reply
        assert store.read('activity', pending['id'])['status'] == 'paused'
        (state / 'hold-candidate').unlink()
        paused = until(lambda: store.read('run', original_id), lambda r: r['status'] == 'paused')
        checkpoint = artifacts.get(paused['checkpoint_ref'])
        report['paused_checkpoint'] = checkpoint
        report['retained_limits'] = {k: paused[k] for k in ('expires_at', 'maximum_attempts', 'budget_seconds', 'initial_checkpoint_ref')}
        assert any(t['stage'] == 'candidate_saved' and t['attempt_count'] > 0 for t in checkpoint['tasks'])
        record('conversational_request_switch_and_acknowledged_pause')
        old_generation = store.read('brain', 'state')['generation']
        client.close()
        process.kill()
        process.wait(timeout=15)
        log.close()
        (state / 'hold-checks').touch()
        start()
        reopened = store.read('run', original_id)
        assert reopened['checkpoint_ref'] == paused['checkpoint_ref']
        assert all(reopened[k] == value for k, value in report['retained_limits'].items())
        assert store.read('brain', 'state')['generation'] > old_generation
        assert report['processes'][0]['pid'] != report['processes'][1]['pid']
        record('actual_process_crash_reopen_preserved_candidate_and_limits')
        reply = chat(f'Resume the original {original["exercise_id"]} activity, {original_id}. Leave the other request paused.')
        assert reply['reason_code'] == 'activity_control', reply
        until(lambda: (state / 'checks-ready.json').exists(), bool)
        control('sleep')
        (state / 'hold-checks').unlink()
        completed = until(lambda: store.read('run', original_id), lambda r: r['status'] in {'completed', 'blocked', 'needs_checker', 'stopped'})
        report['final_run'] = completed
        assert completed['status'] == 'completed', completed
        final_checkpoint = artifacts.get(completed['checkpoint_ref'])
        assert [t['attempt_count'] for t in final_checkpoint['tasks']] == [t['attempt_count'] for t in checkpoint['tasks']]
        assert [t['source_ref'] for t in final_checkpoint['tasks']] == [t['source_ref'] for t in checkpoint['tasks']]
        assert all(completed[k] == value for k, value in report['retained_limits'].items())
        record('saved_edit_checked_without_regeneration_during_sleep')
        sleeping = client.get('/api/coordination/status?audience=owner_private').json()
        assert not sleeping['awake'] and sleeping['catch_up'] == []
        wake = control('wake')
        assert sum(a['id'] == original_id and a['status'] == 'completed' for a in wake['catch_up']) == 1
        assert client.get('/api/coordination/status?audience=owner_private').json()['catch_up'] == []
        record('wake_catch_up_exactly_once')
        query = f'How did the original {original["exercise_id"]} exercise turn out? Tell me the actual result.'
        reply = chat(query)
        request_id = report['interactions'][-1]['request_id']
        assert reply['reason_code'] == 'practice_status' and original_id in reply['display_text'], reply
        assert 'passed' in reply['display_text'] and pending['id'] not in reply['display_text']
        assert chat(query, request_id) == reply
        record('natural_targeted_report_and_duplicate_input')
        report['final_checkpoint'] = final_checkpoint
        browser_result = ui({'op': 'finish', 'screenshot': str(state / 'monitor-final.png')})
        assert browser_result['errors'] == [] and browser_result['sockets'] == [], browser_result
        assert set(browser_result['requests']) <= {'/api/monitor/input', '/api/coordination/status', '/api/coordination/control'}, browser_result
        report['browser'] = browser_result
        record('real_browser_input_rendering_and_no_legacy_connections')
        record('complete_connected_journey')
    except BaseException as exc:
        report['failure'] = {'type': type(exc).__name__, 'message': str(exc)[:2500]}
        report['last_decision'] = store.read('brain', 'state').get('last_decision')
        publish(report_path, report)
        raise
    finally:
        if client:
            client.close()
        if process and process.poll() is None:
            process.kill()
            process.wait(timeout=15)
        if log and not log.closed:
            log.close()
        if browser.poll() is None:
            browser.stdin.close()
            try:
                browser.wait(timeout=15)
            except subprocess.TimeoutExpired:
                browser.kill()
                browser.wait(timeout=15)
        browser_log.close()
        print(json.dumps({'report': str(report_path)}), flush=True)


if __name__ == '__main__':
    main()
