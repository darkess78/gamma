"""P6 routes use the actual authenticated isolated app factory."""
import time

from fastapi.testclient import TestClient

from gamma.coding.durable_practice import PracticeRunner
from gamma.coordination.app import build_app
from gamma.schemas.response import AssistantResponse


def test_actual_factory_enforces_auth_private_scope_types_and_absent_game(tmp_path):
    app = build_app(tmp_path, bearer_token='isolated-test-token', run_background=False,
                    draft_factory=lambda: lambda *_: AssistantResponse(display_text='Here.'),
                    runner_factory=lambda store: PracticeRunner(store, propose=lambda *_: {}, check=lambda *_: {}))
    prefix = '/v1/coordination/shared-activity'
    with TestClient(app) as client:
        assert client.get(prefix + '/status', params={'audience': 'owner_private'}).status_code == 401
        client.headers['Authorization'] = 'Bearer isolated-test-token'
        assert client.get(prefix + '/status', params={'audience': 'public'}).status_code == 422
        assert client.get(prefix + '/status', params={'audience': 'owner_private'}).headers['cache-control'] == 'no-store'
        selected = client.post(prefix + '/control', json={'audience': 'owner_private', 'request_id': 'select',
                               'action': 'select', 'values': {'source_id': 'runelite', 'label': 'RuneLite', 'kind': 'osrs_screen'}})
        assert selected.status_code == 200
        epoch = selected.json()['source_epoch']
        payload = {'audience': 'owner_private', 'request_id': 'frame', 'observation': {
            'observation_id': 'frame', 'source_id': 'runelite', 'source_epoch': epoch, 'sequence': 0,
            'captured_at': time.time(), 'kind': 'osrs_screen', 'summary': 'Unknown view.',
            'confidence': .1, 'content_sha256': 'a' * 64}}
        malformed = {**payload, 'observation': {**payload['observation'], 'captured_at': True}}
        assert client.post(prefix + '/observe', json=malformed).status_code == 422
        assert client.post(prefix + '/observe', json={**payload, 'execute': 'code'}).status_code == 422
        assert client.post(prefix + '/observe', json=payload).status_code == 200
        assert client.post(prefix + '/review', json={'audience': 'owner_private', 'request_id': 'review'}).status_code == 200
        game = client.post(prefix + '/minecraft/action', json={'audience': 'owner_private', 'request_id': 'move',
                            'name': 'come_here', 'arguments': {}})
        assert game.status_code == 409 and 'not connected' in game.json()['detail']
