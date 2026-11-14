"""Manifest-bound internal credentials fence requests before canonical mutations."""
from copy import deepcopy
from pathlib import Path
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from gamma.config import settings
from gamma.coordination import app as app_module
from gamma.coordination.private_release import release_api_bearer
from gamma.coding.durable_practice import PracticeRunner
from gamma.dashboard.shana_client import ShanaApiClient
from gamma.schemas.response import AssistantResponse
from test_private_release import packet


def test_release_credential_binds_the_entire_manifest_purpose_and_secret(packet):
    base = 'authored-fixture-secret'
    expected = release_api_bearer(packet, 'test', base)
    assert release_api_bearer(deepcopy(packet), 'test', base) == expected
    assert release_api_bearer(packet, 'permanent', base) != expected
    assert release_api_bearer(packet, 'test', base + '-other') != expected
    for key, value in (('release_id', uuid.uuid4().hex), ('status', 'owner_approved')):
        changed = deepcopy(packet)
        changed[key] = value
        assert release_api_bearer(changed, 'test', base) != expected
    for section, key, value in (
        ('state', 'identity_id', uuid.uuid4().hex),
        ('source', 'root', '/tmp/another-exact-release'),
        ('models', 'primary', 'another-primary'),
    ):
        changed = deepcopy(packet)
        changed[section][key] = value
        assert release_api_bearer(changed, 'test', base) != expected
    assert base not in str(packet) and expected not in str(packet)


@pytest.mark.parametrize('base', ['', None, False, 7])
def test_release_credential_requires_an_explicit_base_secret(packet, base):
    with pytest.raises(ValueError, match='base_api_credential'):
        release_api_bearer(packet, 'test', base)


def test_release_credential_rejects_unknown_purpose(packet):
    with pytest.raises(ValueError, match='purpose'):
        release_api_bearer(packet, 'other', 'authored-secret')


def test_configured_app_explicit_bearer_is_local_and_default_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'api_bearer_token', 'authored-normal-secret')
    monkeypatch.setattr(settings, 'llm_provider', 'mock')
    captured = []
    monkeypatch.setattr(app_module, 'build_app', lambda *args, **kwargs: captured.append(kwargs) or object())
    project = Path(__file__).resolve().parents[1]
    app_module.configured_app(project, tmp_path / 'default')
    app_module.configured_app(project, tmp_path / 'release', bearer_token='authored-release-secret')
    app_module.configured_app(project, tmp_path / 'default-again')
    assert [row['bearer_token'] for row in captured] == [
        'authored-normal-secret', 'authored-release-secret', 'authored-normal-secret']
    assert settings.api_bearer_token == 'authored-normal-secret'


@pytest.mark.parametrize('bearer', ['', False, 1])
def test_configured_app_rejects_empty_or_nontext_explicit_bearer(tmp_path, bearer):
    with pytest.raises(ValueError, match='explicit_api_bearer'):
        app_module.configured_app(Path(__file__).resolve().parents[1], tmp_path, bearer_token=bearer)


def test_client_explicit_bearer_is_local_and_default_auth_behavior_unchanged(monkeypatch):
    monkeypatch.setattr(settings, 'api_auth_enabled', True)
    monkeypatch.setattr(settings, 'api_bearer_token', 'authored-normal-secret')
    captured = []
    def observe(request):
        captured.append(request.headers.get('Authorization'))
        return httpx.Response(200, json={'ok': True})
    transport = httpx.MockTransport(observe)
    for options in ({}, {'bearer_token': 'authored-release-secret'}, {}):
        client = ShanaApiClient(base_url='http://127.0.0.1:9', transport=transport, **options)
        try:
            assert client.get('/fixture') == {'ok': True}
        finally:
            client.close()
    assert captured == ['Bearer authored-normal-secret', 'Bearer authored-release-secret', 'Bearer authored-normal-secret']
    monkeypatch.setattr(settings, 'api_auth_enabled', False)
    client = ShanaApiClient(base_url='http://127.0.0.1:9', transport=transport)
    try:
        client.get('/fixture')
    finally:
        client.close()
    assert captured[-1] is None
    assert settings.api_bearer_token == 'authored-normal-secret'


@pytest.mark.parametrize('bearer', ['', False, 1])
def test_client_rejects_empty_or_nontext_explicit_bearer(bearer):
    with pytest.raises(ValueError, match='explicit_api_bearer'):
        ShanaApiClient(base_url='http://127.0.0.1:9', bearer_token=bearer)


def test_real_http_auth_rejects_other_release_and_ordinary_replacement_before_mutation(packet, tmp_path):
    base = 'authored-fixture-secret'
    changed = deepcopy(packet)
    changed['release_id'] = uuid.uuid4().hex
    changed['state']['identity_id'] = uuid.uuid4().hex
    first = release_api_bearer(packet, 'test', base)
    second = release_api_bearer(changed, 'test', base)
    permanent = release_api_bearer(packet, 'permanent', base)
    for label, accepted, rejected in (
        ('release-one', first, (base, second, permanent)),
        ('replacement-ordinary', base, (first,)),
        ('replacement-release', second, (first, base)),
    ):
        calls = []
        def draft(*_):
            calls.append('called')
            return AssistantResponse(display_text='Authored controlled reply.', delivery_mode='text_only')
        app = app_module.build_app(
            tmp_path / label, bearer_token=accepted, draft_factory=lambda: draft,
            runner_factory=lambda store: PracticeRunner(store, propose=lambda *_: {}, check=lambda *_: {}),
            run_background=False, identity_lock_root=tmp_path / 'private-locks')
        with TestClient(app) as client:
            for index, rejected_token in enumerate(rejected):
                request_id = 'rejected-' + str(index)
                response = client.post('/v1/coordination/input',
                    headers={'Authorization': 'Bearer ' + rejected_token},
                    json={'audience': 'owner_private', 'request_id': request_id,
                          'session_id': 'authored-auth-fixture', 'text': 'Harmless authored mutation trap.'})
                assert response.status_code == 401
                assert app.state.core.store.read('turn', request_id) is None
                assert calls == []
            response = client.post('/v1/coordination/input',
                headers={'Authorization': 'Bearer ' + accepted},
                json={'audience': 'owner_private', 'request_id': 'accepted',
                      'session_id': 'authored-auth-fixture', 'text': 'Harmless authored accepted exchange.'})
            assert response.status_code == 200
            assert app.state.core.store.read('turn', 'accepted')['status'] == 'completed'
            assert calls == ['called']
