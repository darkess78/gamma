"""Real-browser regression for selecting older paused work, without inference."""
import json
import os
from pathlib import Path
import secrets
import select
import subprocess
import time
import uuid


def main():
    project = Path(__file__).resolve().parents[1]
    if os.name != 'posix' or project.name in {'gamma-main', 'gamma-modular-phase1'}:
        raise RuntimeError('use the isolated Gamma checkout')
    state = project / 'data/coordination' / ('review-ui-' + uuid.uuid4().hex)
    state.mkdir(mode=0o700)
    token, password = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    os.environ.update({'SHANA_PROJECT_ROOT': str(project), 'GAMMA_ISOLATED_COORDINATION': '1',
        'GAMMA_COORDINATION_REVIEW_MODE': 'mock', 'SHANA_API_AUTH_ENABLED': 'true', 'SHANA_API_BEARER_TOKEN': token,
        'SHANA_DASHBOARD_AUTH_ENABLED': 'true', 'SHANA_DASHBOARD_AUTH_USERNAME': 'isolated-owner',
        'SHANA_DASHBOARD_AUTH_PASSWORD': password, 'SHANA_DASHBOARD_SESSION_SECRET': secrets.token_urlsafe(32),
        'SHANA_DASHBOARD_COOKIE_SECURE': 'false', 'SHANA_DATABASE_URL': 'sqlite:///' + str(state/'conversation.sqlite3')})
    from gamma.config import settings
    from gamma.schemas.response import AssistantResponse
    from gamma.coordination.app import build_app
    from gamma.coordination.dashboard import create_app as dashboard_app
    from gamma.coordination.hosting import loopback_server
    from gamma.coding.durable_practice import PracticeRunner
    from gamma.coding.sandbox import run_practice_check
    def no_inference(*_args):
        raise AssertionError('UI regression must not request inference')
    app = build_app(state, bearer_token=token, draft_factory=lambda:lambda *_:AssistantResponse(display_text='Fixture',delivery_mode='text_only'),
                    runner_factory=lambda store:PracticeRunner(store, propose=no_inference, check=run_practice_check), run_background=False)
    report = {'scope':'isolated_owner_private', 'model':'no_inference_ui_fixture', 'checks':{}}
    browser = None
    with (state/'browser.log').open('w') as log:
        try:
            with loopback_server(app) as api:
                os.environ['GAMMA_COORDINATION_API_ORIGIN'] = api
                settings.shana_port = int(api.rsplit(':',1)[1])
                core = app.state.core
                first = core.control('review-a','practice',exercise_id='retry_budget')['activity']['id']
                core.control('pause-a','pause',run_id=first); core.runner.run(first)
                second = core.control('review-b','practice',exercise_id='message_chunks')['activity']['id']
                core.control('pause-b','pause',run_id=second); core.runner.run(second)
                before = core.store.read('run',first)
                with loopback_server(dashboard_app()) as dashboard:
                    browser = subprocess.Popen(['node',str(project/'scripts/coordination_browser_probe.cjs')],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True)
                    def ui(value):
                        browser.stdin.write(json.dumps(value)+'\n'); browser.stdin.flush()
                        if not select.select([browser.stdout],[],[],40)[0]:
                            raise AssertionError('browser UI observation timed out')
                        result = json.loads(browser.stdout.readline())
                        assert result.get('ok'), result
                        return result
                    ui({'op':'start','url':dashboard,'username':'isolated-owner','password':password})
                    ui({'op':'control','action':'resume','expect_no_request':True})
                    result = ui({'op':'control','action':'resume','target':first})
                    assert result['request']['run_id'] == first
                    assert core.store.read('run',first)['desired_state'] == 'running'
                    assert core.store.read('run',second)['desired_state'] == 'pause'
                    result = ui({'op':'control','action':'pause','target':first})
                    assert result['request']['run_id'] == first
                    core.runner.run(first)
                    after = core.store.read('run',first)
                    assert after['status'] == 'paused'
                    assert all(after[k] == before[k] for k in ('checkpoint_ref','expires_at','maximum_attempts'))
                    ui({'op':'activity_observation','id':first,'status':'paused'})
                    ui({'op':'chat','text':'This is a private fixture message.'})
                    ui({'op':'audience_visibility','restore_target':first})
                    result = ui({'op':'finish','screenshot':str(state/'monitor-selected.png')})
                    assert result['errors'] == [] and result['sockets'] == []
                    report.update(browser=result, first_activity=first, second_activity=second)
                    report['checks'].update(login_redirect=True, explicit_older_activity_selection=True,
                        ambiguous_controls_blocked=True, correct_target_only=True, saved_state_and_limits_retained=True,
                        private_display_hidden_and_draft_retained=True)
        except BaseException as exc:
            report['failure'] = {'type':type(exc).__name__, 'message':str(exc)[:1000]}
            if browser and browser.poll() is None and 'ui' in locals():
                try:
                    report['browser'] = ui({'op':'finish','screenshot':str(state/'monitor-failure.png')})
                except Exception:
                    pass
            raise
        finally:
            if browser:
                browser.stdin.close()
                try:
                    browser.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    browser.kill(); browser.wait(timeout=10)
            (state/'report.json').write_text(json.dumps(report,indent=2))
            print(json.dumps({'report':str(state/'report.json')}),flush=True)
    print(json.dumps({'report':str(state/'report.json'), 'checks':report['checks']}))


if __name__ == '__main__':
    main()
