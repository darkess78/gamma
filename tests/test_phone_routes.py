"""Authenticated actual HTTP transport and dashboard proxy validation."""
import json

from fastapi.testclient import TestClient
import pytest

from gamma.coordination.app import build_app
from gamma.coordination.phone import PhoneAccess
from gamma.coordination.phone_routes import add_backend_routes, add_dashboard_routes
from gamma.coding.durable_practice import PracticeRunner
from gamma.schemas.response import AssistantResponse


def application(tmp_path):
    return build_app(tmp_path, bearer_token='private-owner', draft_factory=lambda:lambda *_:AssistantResponse(
        display_text='Connected same-Core response.', delivery_mode='text_only'), run_background=False,
        runner_factory=lambda store:PracticeRunner(store, propose=lambda *_:{}, check=lambda *_:{}))


def test_private_http_requires_owner_auth_audience_device_and_current_epoch(tmp_path):
    app = application(tmp_path)
    # Root app integration is independently exercised if present; this fallback registers only the owned route module.
    if not any(getattr(r, 'path', '') == '/v1/coordination/phone/register' for r in app.routes):
        add_backend_routes(app)
    with TestClient(app) as client:
        if not hasattr(app.state, 'phone'):
            app.state.phone = PhoneAccess(app.state.core)
        prefix = '/v1/coordination/phone'
        body = {'audience':'owner_private', 'request_id':'register', 'device_id':'android-test', 'device_secret':'S' * 43}
        assert client.post(prefix + '/register', json=body).status_code == 401
        assert client.get(prefix + '/devices?audience=owner_private').status_code == 401
        assert client.post(prefix + '/revoke-device', json={'audience':'owner_private', 'request_id':'revoke', 'device_id':'android-test'}).status_code == 401
        client.headers['Authorization'] = 'Bearer private-owner'
        assert client.post(prefix + '/register', json={**body, 'audience':'public'}).status_code == 422
        assert client.post(prefix + '/register', json={**body, 'grants_authority':True}).status_code == 422
        assert client.post(prefix + '/register', json=body).status_code == 200
        connection = client.post(prefix + '/connect', json={**body, 'request_id':'connect'})
        assert connection.status_code == 200
        epoch = connection.json()['device']['epoch']
        bound = {**body, 'request_id':'state', 'epoch':epoch}
        assert client.post(prefix + '/state', json={**bound, 'device_secret':'X' * 43}).status_code == 400
        assert client.post(prefix + '/state', json={**bound, 'epoch':True}).status_code == 422
        result = client.post(prefix + '/input', json={**bound, 'request_id':'one', 'text':'How is our work?'})
        assert result.status_code == 200 and result.json()['status'] == 'completed'
        assert result.headers['cache-control'] == 'no-store'
        history = client.post(prefix + '/conversation', json=bound).json()
        assert history['turns'][0]['assistant'] == 'Connected same-Core response.'
        assert 'S' * 43 not in json.dumps(history)
        assert client.post(prefix + '/state', json={**bound, 'audience':'public'}).status_code == 422
        assert client.post(prefix + '/control', json={**bound, 'action':'availability', 'value':{}}).status_code == 400
        assert client.post(prefix + '/receipt', json={**bound, 'message_id':'reminder-' + 'a' * 32, 'status':'heard'}).status_code == 422
        assert client.post(prefix + '/apply', json={**bound, 'decision':'send', 'text':'Injected contact.'}).status_code == 404
        assert client.post(prefix + '/input', json={**bound, 'text':'Hello', 'state_updates':{'activity_action':'start_work'}}).status_code == 422
        devices = client.get(prefix + '/devices?audience=owner_private')
        assert devices.status_code == 200 and 'secret' not in devices.text and 'sha256' not in devices.text
        assert client.get(prefix + '/devices?audience=public').status_code == 422
        assert client.post(prefix + '/revoke-device', json={'audience':'public', 'request_id':'revoke', 'device_id':'android-test'}).status_code == 422
        revoke = client.post(prefix + '/revoke-device', json={'audience':'owner_private', 'request_id':'revoke', 'device_id':'android-test'})
        assert revoke.status_code == 200 and revoke.json()['revoked']
        assert client.post(prefix + '/state', json=bound).status_code == 400
        assert app.state.core.store.read('turn', 'one')['status'] == 'completed'


def test_owned_dashboard_page_and_proxy_are_typed_no_store_and_have_no_secret_urls():
    from fastapi import FastAPI
    seen = []
    class Client:
        def request_json(self, method, path, **kwargs):
            seen.append((method, path, kwargs)); return {'ok':True}
    app = FastAPI(); add_dashboard_routes(app, Client())
    with TestClient(app) as client:
        page = client.get('/dashboard/phone')
        assert page.status_code == 200
        assert 'viewport' in page.text and '/static/phone.js' in page.text
        assert 'type="checkbox"' in page.text and 'aria-label="Private access"' in page.text
        assert page.headers['cache-control'] == 'no-store'
        worker = client.get('/dashboard/phone-worker.js')
        assert worker.status_code == 200
        assert worker.headers['content-type'].startswith('application/javascript')
        assert worker.headers['cache-control'] == 'no-store'
        assert worker.headers['service-worker-allowed'] == '/dashboard/phone'
        assert worker.headers['x-content-type-options'] == 'nosniff'
        assert 'notificationclick' in worker.text
        body = {'audience':'owner_private', 'request_id':'read', 'device_id':'phone', 'device_secret':'S' * 43, 'epoch':1}
        assert client.post('/api/coordination/phone/state', json=body).status_code == 200
        assert seen[-1][1] == '/v1/coordination/phone/state'
        assert seen[-1][2]['json_payload']['device_secret'] == 'S' * 43
        assert 'params' not in seen[-1][2]
        assert client.post('/api/coordination/phone/state', json={**body, 'admin':True}).status_code == 422
        assert len(seen) == 1


@pytest.mark.parametrize('mode,expected', [('mock', 'responses are synthetic'), ('real', 'separate state')])
def test_phone_uses_shared_review_banner_without_duplicate_navigation_assets(monkeypatch, mode, expected):
    from fastapi import FastAPI
    monkeypatch.setenv('GAMMA_COORDINATION_REVIEW_MODE', mode)
    app = FastAPI(); add_dashboard_routes(app, object())
    with TestClient(app) as client:
        page = client.get('/dashboard/phone')
        assert page.status_code == 200 and expected in page.text
        assert page.text.count('id="coordinationReviewMode"') == 1
        assert page.text.count('/static/coordination_shell.js') == 1
