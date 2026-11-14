"""Review mount isolation against real dashboard auth, private Core and audio."""
from contextlib import contextmanager
from time import time
from unittest.mock import patch
from pathlib import Path
import subprocess

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import Response, JSONResponse, RedirectResponse
from fastapi.testclient import TestClient

from gamma.coding.durable_practice import PracticeRunner
from gamma.config import settings
from gamma.coordination.app import build_app
from gamma.coordination.dashboard import build_app as dashboard_app
from gamma.coordination.interaction import PrivateInteraction
from gamma.coordination.review_ingress import ReviewIngress, prefix_url, rewrite_literals
from gamma.dashboard.auth import session_cookie_value
from gamma.dashboard.shana_client import ShanaApiClient
from gamma.schemas.response import AssistantResponse
from test_channel_audio import wav


PREFIX = '/review/shana'
COOKIE = 'gamma_review_shana_session'
PRIVATE = '/api/private quote and /dashboard/phone are literal private words.'


@contextmanager
def connected(tmp_path):
    app = build_app(tmp_path, bearer_token='review-owner', draft_factory=lambda: lambda *_: AssistantResponse(
        display_text=PRIVATE, speech_text='This is a generated audio fixture.', delivery_mode='text_only'),
        run_background=False,
        runner_factory=lambda store: PracticeRunner(store, propose=lambda *_: {}, check=lambda *_: {}),
        interaction_factory=lambda core: PrivateInteraction(core, synthesize=lambda *_: (wav(), 'audio/wav')))
    calls = []
    with TestClient(app) as backend:
        def transport(request):
            calls.append(request.url.path)
            response = backend.request(request.method, str(request.url), headers=request.headers, content=request.content)
            return httpx.Response(response.status_code, content=response.content, headers={'content-type': 'application/json'})
        with patch.object(settings, 'api_auth_enabled', True), patch.object(settings, 'api_bearer_token', 'review-owner'):
            bridge = ShanaApiClient(base_url='http://isolated', transport=httpx.MockTransport(transport))
        with (patch.object(settings, 'dashboard_auth_enabled', True),
              patch.object(settings, 'dashboard_auth_username', 'owner'),
              patch.object(settings, 'dashboard_auth_password', 'test-password'),
              patch.object(settings, 'dashboard_session_secret', 'test-session')):
            inner = dashboard_app(bridge)
            outer = ReviewIngress(inner, PREFIX)
            with TestClient(outer, base_url='https://review.test') as client:
                yield client, app, inner, outer, bridge, calls


def login(client):
    response = client.post(PREFIX + '/login', data={'username': 'owner', 'password': 'test-password'}, follow_redirects=False)
    assert response.status_code == 303
    return response


def test_login_cookie_is_scoped_and_normal_cookie_never_authorizes_review(tmp_path):
    with connected(tmp_path) as (client, app, inner, outer, bridge, calls):
        normal = session_cookie_value('owner')
        client.cookies.set('gamma_dashboard_session', normal, domain='review.test', path='/')
        assert client.get(PREFIX + '/api/coordination/status?audience=owner_private').status_code == 401
        assert calls == []
        denied = client.get(PREFIX + '/dashboard/phone', follow_redirects=False)
        assert denied.status_code == 303 and denied.headers['location'] == PREFIX + '/login'
        response = login(client)
        assert response.headers['location'] == PREFIX + '/dashboard'
        cookie = response.headers['set-cookie']
        assert COOKIE + '=' in cookie and 'Path=' + PREFIX in cookie and 'Secure' in cookie
        assert 'HttpOnly' in cookie and 'SameSite=lax' in cookie
        assert 'gamma_dashboard_session=' not in cookie
        assert client.cookies.get('gamma_dashboard_session') == normal
        assert client.get(PREFIX + '/api/coordination/status?audience=owner_private').status_code == 200
        assert outer.state is inner.state and outer.router is inner.router
        logout = client.get(PREFIX + '/logout', follow_redirects=False)
        assert logout.headers['location'] == PREFIX + '/login'
        assert 'Max-Age=0' in logout.headers['set-cookie'] and 'Path=' + PREFIX in logout.headers['set-cookie']
        assert client.cookies.get('gamma_dashboard_session') == normal
        assert client.get(PREFIX + '/api/coordination/status?audience=owner_private').status_code == 401


def test_existing_unprefixed_app_and_cookie_contract_are_unchanged(tmp_path):
    with connected(tmp_path) as (_, app, inner, outer, bridge, calls):
        with TestClient(inner, base_url='https://normal.test') as normal:
            normal.cookies.set('gamma_dashboard_session', session_cookie_value('owner'))
            assert normal.get('/api/coordination/status?audience=owner_private').status_code == 200
            assert 'href="/dashboard/monitor"' in normal.get('/dashboard/home').text
            response = normal.post('/login', data={'username': 'owner', 'password': 'test-password'}, follow_redirects=False)
            assert response.headers['location'] == '/dashboard'
            assert 'gamma_dashboard_session=' in response.headers['set-cookie'] and 'Path=/' in response.headers['set-cookie']


@pytest.mark.parametrize('path', ['/dashboard/home', '/dashboard/activities', '/dashboard/rehearsal',
    '/dashboard/phone', '/dashboard/presence', '/dashboard/work', '/dashboard/personal', '/dashboard/growth',
    '/dashboard/monitor', '/dashboard/designs'])
def test_every_review_page_keeps_owned_links_and_scripts_under_prefix(tmp_path, path):
    with connected(tmp_path) as (client, *_):
        login(client)
        response = client.get(PREFIX + path)
        assert response.status_code == 200, response.text
        assert '/review/shana/static/coordination_shell.js' in response.text
        import re
        assert not re.search(r'''(?:href|src|action)=["']/(?:api|static|dashboard|login|logout|monitor)(?:/|["'?])''', response.text)
        assert int(response.headers['content-length']) == len(response.content)
        assert response.headers['cache-control'] == 'no-store'


def test_phone_worker_code_scope_design_links_and_template_urls_are_rewritten(tmp_path):
    with connected(tmp_path) as (client, *_):
        login(client)
        phone = client.get(PREFIX + '/static/phone.js')
        assert PREFIX + '/api/coordination/phone' in phone.text
        assert PREFIX + '/dashboard/phone-worker.js' in phone.text
        assert "scope: '" + PREFIX + '/dashboard/phone' in phone.text
        worker = client.get(PREFIX + '/dashboard/phone-worker.js')
        assert worker.headers['service-worker-allowed'] == PREFIX + '/dashboard/phone'
        assert "new URL('" + PREFIX + '/dashboard/phone' in worker.text
        design = client.get(PREFIX + '/static/coordination_designs.js')
        assert design.status_code == 200
        assert PREFIX + '/dashboard/phone' in design.text
        audio = client.get(PREFIX + '/static/channel_audio_player.js')
        assert PREFIX + '/api/coordination/interaction/audio/' in audio.text
        assert 'etag' not in phone.headers and 'last-modified' not in phone.headers


def test_real_core_private_text_and_real_pcm_http_body_are_not_rewritten(tmp_path):
    with connected(tmp_path) as (client, app, *_):
        login(client)
        root = PREFIX + '/api/coordination'
        response = client.post(root + '/monitor/input', json={})
        assert response.status_code == 404
        turn = client.post(PREFIX + '/api/monitor/input', json={'audience': 'owner_private',
            'request_id': 'literal-private', 'input_mode': 'private_text', 'text': PRIVATE})
        assert turn.status_code == 200, turn.text
        assert turn.json()['result']['assistant_response']['display_text'] == PRIVATE
        consumer = {'audience': 'owner_private', 'consumer_id': 'review-listener'}
        session = client.post(root + '/stream/start', json={'audience': 'owner_private', 'request_id': 'stream-start',
            'format': 'coding', 'broad_plan': 'Review fixture boundaries.'}).json()['id']
        stream = {**consumer, 'session_id': session}
        assert client.post(root + '/stream/connect', json=stream).status_code == 200
        assert client.post(root + '/channel-audio/connect', json=consumer).status_code == 200
        assert client.post(root + '/channel-audio/enable', json={**consumer, 'enabled': True}).status_code == 200
        result = client.post(root + '/stream/event', json={**stream, 'event_id': 'q', 'kind': 'chat',
            'source': 'replay', 'observed_at': time(), 'text': 'What should we review?', 'actor_alias': 'FixtureViewer'})
        source = result.json()['output_id']
        rendered = client.post(root + '/channel-audio/render', json={**consumer, 'stream_output_id': source})
        assert rendered.status_code == 200, rendered.text
        output = client.get(root + '/interaction/poll', params=consumer).json()['output']
        assert output is not None
        audio = client.get(root + '/interaction/audio/' + output['id'], params={**consumer,
            'generation': output['generation'], 'output_epoch': output['output_epoch']})
        assert audio.status_code == 200 and audio.content == wav() and audio.headers['content-type'] == 'audio/wav'
        assert 'content-encoding' not in audio.headers
        assert app.state.core.store.read('channel_audio_consent', consumer['consumer_id'])['enabled']
    assert app.state.core.store.read('stream_session', session)['status'] == 'stopped'


@pytest.mark.parametrize('path', ['/dashboard', '/api/coordination/status', '/v1/coordination/status',
    '/review/shana-other/dashboard', PREFIX + '/v1/coordination/status', PREFIX + '/health',
    PREFIX + '/static/%2e%2e/v1/x', PREFIX + '/api/%2f/v1/x'])
def test_outside_backend_and_ambiguous_paths_are_unavailable(tmp_path, path):
    with connected(tmp_path) as (client, *_):
        assert client.get(path, follow_redirects=False).status_code == 404


@pytest.mark.parametrize('value', ['', '/', '/review/Shana', '/review/shana/', '/review/a_b',
    '/review/a/b', '/review/' + 'a' * 42])
def test_prefix_is_narrow_and_explicit(value):
    with pytest.raises(ValueError):
        ReviewIngress(FastAPI(), value)


def test_literal_transform_is_idempotent_and_does_not_touch_other_text():
    body = '''const base=`/api/coordination`; scope:'/dashboard/phone'; url(/static/a.png); /api/.test(x); 'private /api/quote'; '/v1/no'; '//external/api'; '/static/../v1/a';'''
    value = rewrite_literals(body, PREFIX)
    assert '`' + PREFIX + '/api/coordination`' in value
    assert "scope:'" + PREFIX + '/dashboard/phone' in value
    assert 'url(' + PREFIX + '/static/a.png)' in value
    for unchanged in ['/api/.test(x)', "'private /api/quote'", "'/v1/no'", "'//external/api'", "'/static/../v1/a'"]:
        assert unchanged in value
    assert rewrite_literals(value, PREFIX) == value


def test_json_only_schema_owned_url_location_changes_and_duplicate_headers_survive():
    app = FastAPI()
    raw = {'output': {'audio_url': '/api/coordination/interaction/audio/out', 'text': '/api/quote'},
           'text': '/dashboard/phone', 'metadata': {'audio_url': '/api/private-quote'}}
    @app.get('/api/coordination/interaction/poll')
    def poll():
        response = JSONResponse(raw)
        response.raw_headers.extend([(b'x-evidence', b'one'), (b'x-evidence', b'two')])
        return response
    @app.get('/api/coordination/personal')
    def personal():
        return Response(b'{ "text": "/api/quote", "audio_url": "/api/private" }', media_type='application/json')
    with TestClient(ReviewIngress(app, PREFIX)) as client:
        response = client.get(PREFIX + '/api/coordination/interaction/poll')
        result = response.json()
        assert result['output']['audio_url'] == PREFIX + raw['output']['audio_url']
        assert result['output']['text'] == '/api/quote'
        assert result['metadata'] == raw['metadata'] and result['text'] == raw['text']
        assert response.headers.get_list('x-evidence') == ['one', 'two']
        assert client.get(PREFIX + '/api/coordination/personal').content == b'{ "text": "/api/quote", "audio_url": "/api/private" }'


@pytest.mark.parametrize('mode', ['compressed', 'too-large', 'stream-large', 'external-location', 'backend-location'])
def test_unsupported_transform_sources_and_redirects_fail_closed(mode):
    async def app(scope, receive, send):
        if scope['type'] == 'lifespan':
            while True:
                message = await receive()
                if message['type'] == 'lifespan.startup':
                    await send({'type': 'lifespan.startup.complete'})
                else:
                    await send({'type': 'lifespan.shutdown.complete'})
                    return
        headers = [(b'content-type', b'text/html')]
        body = b'<a href="/dashboard">Go</a>'
        if mode == 'compressed': headers.append((b'content-encoding', b'gzip'))
        if mode == 'too-large': headers.append((b'content-length', b'2000001'))
        if mode == 'stream-large': body = b'x' * 2_000_001
        if mode.endswith('location'):
            headers.append((b'location', b'https://outside.test/login' if mode == 'external-location' else b'/v1/secret'))
        await send({'type': 'http.response.start', 'status': 200, 'headers': headers})
        await send({'type': 'http.response.body', 'body': body})
    with TestClient(ReviewIngress(app, PREFIX)) as client:
        response = client.get(PREFIX + '/dashboard', follow_redirects=False)
        assert response.status_code == 502


def test_websockets_are_denied_without_entering_app():
    app = FastAPI()
    with TestClient(ReviewIngress(app, PREFIX)) as client:
        from starlette.websockets import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect(PREFIX + '/api/private'):
                pass
        assert error.value.code == 1008


def test_duplicate_review_cookies_fail_closed_before_inner_and_regular_cookie_is_removed():
    seen = []
    app = FastAPI()
    @app.get('/api/check')
    def check(request: Request):
        seen.append(request.headers.get('cookie'))
        return {'ok': True}
    with TestClient(ReviewIngress(app, PREFIX), base_url='https://review.test') as client:
        response = client.get(PREFIX + '/api/check', headers={'cookie': COOKIE + '=a; ' + COOKIE + '=b'})
        assert response.status_code == 400 and seen == []
        client.get(PREFIX + '/api/check', headers={'cookie': 'gamma_dashboard_session=production; unrelated=value'})
        assert seen == [None]


def test_compressed_source_is_rejected_through_real_fastapi_middleware():
    app = FastAPI()
    @app.middleware('http')
    async def passthrough(request, call_next):
        return await call_next(request)
    @app.get('/dashboard')
    def page():
        return Response(b'compressed bytes', media_type='text/html', headers={'Content-Encoding': 'gzip'})
    with TestClient(ReviewIngress(app, PREFIX)) as client:
        response = client.get(PREFIX + '/dashboard')
        assert response.status_code == 502


def test_review_csp_is_origin_and_path_scoped_and_intersects_inner_policy():
    app = FastAPI()
    @app.get('/dashboard')
    def page():
        return Response('<p>Review</p>', media_type='text/html',
            headers={'Content-Security-Policy': "default-src 'self'; object-src 'none'"})
    with TestClient(ReviewIngress(app, PREFIX), base_url='https://review.test') as client:
        response = client.get(PREFIX + '/dashboard')
        policies = response.headers.get_list('content-security-policy')
        assert policies == ["default-src 'self'; object-src 'none'", 'connect-src https://review.test/review/shana/']
        assert "'self'" not in policies[-1]
        assert response.headers['content-type'].startswith('text/html')


@pytest.mark.parametrize('host', ['review.test;evil', 'review.test@outside.test', 'review.test/path',
    'review.test%0d', 'review.test bad', 'review.test:bad', 'review.test:0', '-bad.test', 'bad..test'])
def test_review_csp_rejects_ambiguous_or_invalid_current_host(host):
    app = FastAPI()
    @app.get('/login')
    def page(): return Response('<p>Review</p>', media_type='text/html')
    with TestClient(ReviewIngress(app, PREFIX)) as client:
        assert client.get(PREFIX + '/login', headers={'host': host}).status_code == 502


def test_served_phone_js_uses_separate_storage_and_preserves_normal_saved_device(tmp_path):
    with connected(tmp_path / 'state') as (client, *_):
        login(client)
        response = client.get(PREFIX + '/static/phone.js')
        assert response.status_code == 200
        path = tmp_path / 'served-review-phone.js'
        path.write_bytes(response.content)
        result = subprocess.run(['node', str(Path(__file__).with_name('review_ingress_phone_contract.cjs')), str(path)],
            capture_output=True, text=True, timeout=15, check=False)
        assert result.returncode == 0, result.stdout + result.stderr


def test_exact_owned_preference_literals_are_separate_without_changing_other_assets(tmp_path):
    with connected(tmp_path) as (client, *_):
        login(client)
        for filename, keys in {'monitor.js': ['gammaMonitorTheme', 'gammaMonitor.statusPanel'],
                'private_presence.js': ['shana-presence-placement']}.items():
            response = client.get(PREFIX + '/static/' + filename)
            for key in keys:
                assert 'shana-review-shana-' + key in response.text
                assert "'" + key + "'" not in response.text


def test_rewritten_assets_drop_old_ranges_and_validators_but_binary_requests_retain_them():
    seen = []
    app = FastAPI()
    @app.get('/static/check.js')
    def asset(request: Request):
        seen.append(dict(request.headers))
        return Response("fetch('/api/check')", media_type='application/javascript', headers={'ETag': 'old-bytes'})
    @app.get('/api/coordination/interaction/audio/check')
    def audio(request: Request):
        seen.append(dict(request.headers))
        return Response(wav(), media_type='audio/wav', headers={'ETag': 'unchanged-wave'})
    with TestClient(ReviewIngress(app, PREFIX)) as client:
        headers = {'Range': 'bytes=0-10', 'If-None-Match': 'old-bytes'}
        asset = client.get(PREFIX + '/static/check.js', headers=headers)
        assert PREFIX + '/api/check' in asset.text and 'etag' not in asset.headers
        assert 'range' not in seen[0] and 'if-none-match' not in seen[0]
        audio = client.get(PREFIX + '/api/coordination/interaction/audio/check', headers=headers)
        assert audio.content == wav() and audio.headers['etag'] == 'unchanged-wave'
        assert seen[1]['range'] == headers['Range'] and seen[1]['if-none-match'] == 'old-bytes'
