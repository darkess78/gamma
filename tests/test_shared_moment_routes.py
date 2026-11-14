"""Authenticated dashboard -> typed proxy -> actual Core historical flow."""
import base64
import time
from unittest.mock import patch

from fastapi.testclient import TestClient
import httpx

from gamma.coding.durable_practice import PracticeRunner
from gamma.config import settings
from gamma.coordination.app import build_app
from gamma.coordination.dashboard import build_app as dashboard_app
from gamma.coordination.shared_activity import SharedActivities
from gamma.dashboard.auth import session_cookie_value
from gamma.dashboard.shana_client import ShanaApiClient
from gamma.schemas.response import AssistantResponse


def test_connected_authenticated_moment_watch_receipt_and_withdrawal_routes(tmp_path):
    calls, images, requests = [], [], []
    def activity(core):
        def image(raw, *_): images.append(raw); return {'summary': 'An authored changing frame.', 'confidence': .6}
        image.background = image
        return SharedActivities(core, analyze_image=image)
    backend_app = build_app(tmp_path, bearer_token='controlled-owner', run_background=False,
        shared_activity_factory=activity,
        draft_factory=lambda: lambda *_: calls.append(1) or AssistantResponse(display_text='A historical reflection.'),
        runner_factory=lambda store: PracticeRunner(store, propose=lambda *_: {}, check=lambda *_: {}))
    with TestClient(backend_app) as backend:
        def transport(request):
            requests.append(request.url.path)
            response = backend.request(request.method, str(request.url), headers=request.headers, content=request.content)
            return httpx.Response(response.status_code, content=response.content, headers={'content-type': 'application/json'})
        with (patch.object(settings, 'api_auth_enabled', True), patch.object(settings, 'api_bearer_token', 'controlled-owner'),
              patch.object(settings, 'dashboard_auth_enabled', True), patch.object(settings, 'dashboard_auth_username', 'owner'),
              patch.object(settings, 'dashboard_auth_password', 'controlled-password'), patch.object(settings, 'dashboard_session_secret', 'controlled-session')):
            api = ShanaApiClient(base_url='http://controlled', transport=httpx.MockTransport(transport))
            with TestClient(dashboard_app(api)) as client:
                prefix = '/api/coordination/shared-activity'
                def post(path, data, request_id='request'):
                    return client.post(prefix + path, json={'audience':'owner_private','request_id':request_id, **data})
                assert post('/moment/received', {'moment_id':'moment','text_sha256':'a'*64}).status_code == 401
                client.cookies.set('gamma_dashboard_session', session_cookie_value('owner'))
                html = client.get('/dashboard/activities')
                assert html.status_code == 200 and 'Share this moment' in html.text and 'sharingWatchMinutes' in html.text
                state = post('/control', {'action':'select','values':{'source_id':'window', 'label':'Authored window',
                    'kind':'selected_application','seconds':300,'maximum_reviews':10,'maximum_frames':10}}, 'source').json()
                captured_at = time.time()
                raw = b'\x89PNG\r\n\x1a\nauthored fixture'
                packet = {'metadata':{'source_id':'window','source_epoch':state['source_epoch'],'kind':'selected_application',
                    'captured_at':captured_at,'observation_id':'capture','sequence':0},
                    'image_base64':base64.b64encode(raw).decode(),'media_type':'image/png'}
                assert post('/moment', {**packet, 'audience':'public'}).status_code == 422
                assert post('/moment', {**packet, 'execute':True}).status_code == 422
                result = post('/moment', packet, 'moment')
                assert result.status_code == 200, result.text
                assert result.headers['cache-control'] == 'no-store'
                moment = result.json()
                assert moment['captured_at'] == captured_at and moment['current_state'] == 'unknown' and not moment['action_authority']
                # Missing/wrong hash is rejected; authenticated receipt is acknowledgment only.
                assert post('/moment/received', {'moment_id':'moment','text_sha256':'a'*64}).status_code == 400
                receipt = {'moment_id':'moment','text_sha256':moment['text_sha256']}
                assert post('/moment/received', receipt, 'received').json()['status'] == 'consumer_reported_received'
                assert post('/moment/received', receipt, 'received').json()['duplicate']
                assert len(calls) == len(images) == 1
                assert client.get(prefix + '/moments/moment?audience=owner_private').json()['delivery_status'] == 'consumer_reported_received'
                # An explicit new watch grant, and a second new image. No owner turns fabricated.
                state = post('/control', {'action':'select','values':{'source_id':'window','label':'Authored window',
                    'kind':'selected_application','seconds':300,'maximum_reviews':10,'maximum_frames':10,
                    'expected_epoch':state['source_epoch']}}, 'watch_source').json()
                watch = post('/watch/start', {'source_id':'window','source_epoch':state['source_epoch'],'minutes':5}, 'start').json()
                lease = {'watch_id':watch['id'],'lease':watch['lease']}
                assert post('/watch/heartbeat', lease).status_code == 200
                packet['metadata'].update(source_epoch=state['source_epoch'],observation_id='watch_capture',captured_at=time.time())
                watched = post('/watch/moment', {**packet, **lease}, 'watch_moment')
                assert watched.status_code == 200 and watched.json()['moment']['status'] == 'generated'
                assert watched.json()['watch']['consumed_moments'] == 1
                assert post('/watch/stop', lease).json()['status'] == 'stopped'
                assert client.get(prefix + '/watch/' + watch['id'] + '?audience=owner_private').json()['remaining_moments'] == 9
                assert post('/control', {'action':'stop','values':{}}, 'stop_source').status_code == 200
                cached = client.get(prefix + '/moments/moment?audience=owner_private').json()
                assert cached['status'] == 'cancelled' and not cached['text']
                assert len(calls) == len(images) == 2 and backend_app.state.core.store.read('turn','watch_moment') is None
                assert prefix + '/moment' not in requests  # Only /v1 backend URLs reached it.
