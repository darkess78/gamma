"""Real dashboard HTTP/middleware -> service/client -> isolated Shana HTTP."""
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from gamma.config import settings
from gamma.coordination.app import build_app
from gamma.coding.durable_practice import PracticeRunner
from gamma.dashboard import main as dashboard
from gamma.dashboard.auth import session_cookie_value
from gamma.dashboard.service import DashboardService
from gamma.dashboard.shana_client import ShanaApiClient
from gamma.schemas.response import AssistantResponse


def test_authenticated_private_monitor_proxy_preserves_contract_and_no_stream_or_speech(tmp_path):
    drafts = []
    def draft(text, context):
        drafts.append(text)
        return AssistantResponse(display_text='Private reply.', delivery_mode='text_only')
    backend_app = build_app(tmp_path, bearer_token='test-owner', draft_factory=lambda: draft,
                            runner_factory=lambda store: PracticeRunner(store, propose=lambda *_: {}, check=lambda *_: {}),
                            run_background=False)
    with TestClient(backend_app) as backend:
        paths = []
        def transport(request):
            paths.append(request.url.path)
            response = backend.request(request.method, str(request.url), headers=request.headers, content=request.content)
            return httpx.Response(response.status_code, content=response.content, headers={'content-type': 'application/json'})
        with patch.object(settings, 'api_auth_enabled', True), patch.object(settings, 'api_bearer_token', 'test-owner'):
            service = DashboardService.__new__(DashboardService)
            service._shana = ShanaApiClient(base_url='http://isolated', transport=httpx.MockTransport(transport))
        with (patch.object(dashboard, 'get_dashboard_service', return_value=service),
              patch.object(settings, 'dashboard_auth_enabled', True),
              patch.object(settings, 'dashboard_auth_username', 'owner'),
              patch.object(settings, 'dashboard_auth_password', 'test-password'),
              patch.object(settings, 'dashboard_session_secret', 'test-session'),
              TestClient(dashboard.app) as client):
            payload = {'request_id': 'proxy', 'input_mode': 'private_text', 'text': 'private hello',
                       'session_id': 'owner-session', 'audience': 'owner_private',
                       'synthesize_speech': True, 'output_target_policy': 'stream_public'}
            assert client.post('/api/monitor/input', json=payload).status_code == 401
            client.cookies.set('gamma_dashboard_session', session_cookie_value('owner'))
            page = client.get('/dashboard/monitor')
            assert page.status_code == 200 and '<option value="private_text">' not in page.text
            assert '/static/coordination.js' in page.text
            assert client.get('/api/coordination/status').status_code == 422
            result = client.post('/api/monitor/input', json=payload)
            assert result.status_code == 200, result.text
            data = result.json()
            assert data['result']['assistant_response']['display_text'] == 'Private reply.'
            assert data['result']['assistant_response']['speech_text'] == ''
            assert data['event']['metadata']['output_target_policy'] == 'dashboard_monitor'
            assert client.post('/api/monitor/input', json=payload).json() == data
            assert drafts == ['private hello']
            assert client.get('/api/coordination/status?audience=owner_private').status_code == 200
            rejected = client.post('/api/monitor/input', json={**payload, 'request_id': 'unknown', 'audience': 'unknown'})
            assert rejected.status_code == 400
            assert set(paths) <= {'/v1/coordination/input', '/v1/coordination/status'}
            assert client.get('/static/coordination.js').status_code == 200
        service._shana.close()


def test_isolated_monitor_exposes_no_service_or_live_executor_controls():
    from gamma.coordination.dashboard import build_app as monitor_app
    with (patch.object(settings, 'dashboard_auth_enabled', True),
          patch.object(settings, 'dashboard_auth_username', 'owner'),
          patch.object(settings, 'dashboard_auth_password', 'test-password'),
          patch.object(settings, 'dashboard_session_secret', 'test-session')):
        with TestClient(monitor_app(ShanaApiClient(base_url='http://isolated', transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={'ok': True}))))) as client:
            client.cookies.set('gamma_dashboard_session', session_cookie_value('owner'))
            page = client.get('/dashboard/monitor')
            assert page.status_code == 200
            assert 'GAMMA_PRIVATE_COORDINATION = true' in page.text
            assert '<option value="private_text" selected>' in page.text
            assert 'id="coordinationActivityPanel" style="display:none"' not in page.text
            for path in ['/api/shana/restart', '/api/coding/work', '/api/providers/tts/start', '/api/voice/live']:
                assert client.post(path, json={}).status_code == 404


def test_private_monitor_rejects_legacy_modes_and_public_output_before_remote_call():
    from gamma.coordination.dashboard import build_app as monitor_app
    calls = []
    def transport(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={'ok': True})
    with (patch.object(settings, 'dashboard_auth_enabled', True),
          patch.object(settings, 'dashboard_auth_username', 'owner'),
          patch.object(settings, 'dashboard_auth_password', 'test-password'),
          patch.object(settings, 'dashboard_session_secret', 'test-session'),
          TestClient(monitor_app(ShanaApiClient(base_url='http://isolated', transport=httpx.MockTransport(transport)))) as client):
        client.cookies.set('gamma_dashboard_session', session_cookie_value('owner'))
        value = {'request_id': 'test', 'audience': 'owner_private', 'input_mode': 'private_text', 'text': 'hello'}
        for change in ({'input_mode': 'owner_mic'}, {'input_mode': 'chat'}, {'audience': 'shared'},
                       {'output_target_policy': 'stream_public'}, {'synthesize_speech': True}):
            assert client.post('/api/monitor/input', json={**value, **change}).status_code == 422
        assert calls == []
        assert client.post('/api/monitor/input', json=value).status_code == 200
        assert calls == ['/v1/coordination/input']
