"""Validated release lifespan primes canonical identity without an owner turn."""
from contextlib import asynccontextmanager, contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS
import uuid

from fastapi.testclient import TestClient
import pytest

from gamma.config import settings
from gamma.coordination import app as app_module
from gamma.coordination import private_release
from gamma.coordination.store import OwnershipLost
from gamma.persona.coordination import build_private_interaction_prefix
from test_private_inference_profile import PROFILE, configured
from test_private_release import core as seed_core


def registry(core):
    with core.store.transaction(core.token) as db:
        return (tuple(db.execute('SELECT kind, body FROM records ORDER BY kind, body')),
                core._brain(db)['sequence'])


def prepared(tmp_path, monkeypatch, *, purpose='permanent', profile=True,
             prefix_status='ready', lose_owner=False):
    for key, value in vars(configured()).items():
        monkeypatch.setattr(settings, key, value)
    monkeypatch.setattr(settings, 'llm_provider', 'mock')
    monkeypatch.setattr(settings, 'api_bearer_token', 'authored-startup-fixture-key')
    monkeypatch.setenv('GAMMA_PRACTICE_MODEL', 'fixture')
    monkeypatch.setattr('urllib.request.urlopen', lambda *_a, **_k: pytest.fail('Unexpected model HTTP'))
    monkeypatch.setattr('gamma.persona.loader.load_persona_config', lambda **_: {'name': 'DECOY_BASELINE'})
    project, reader = tmp_path / 'project', tmp_path / 'reader'
    project.mkdir(); reader.mkdir()
    root = tmp_path / 'state'
    owner = seed_core(root)
    with owner.store.transaction(owner.token) as db:
        canonical = owner.personal.identity(db)
        canonical['fields']['description'] = 'STARTUP_CANONICAL_EDIT with the current authored trait.'
        canonical['revision'] += 1
        owner.store.put(db, 'personal_identity', 'self', canonical)
        owner.store.put(db, 'person', 'self', {'id': 'self', 'name': 'DECOY_PERSON_RECORD'})
        identity_id = owner.personal.meta(db)['identity_id']
    owner.close()
    manifest = {'release_id': uuid.uuid4().hex, 'source': {'root': str(project)},
        'reader': {'root': str(reader)}, 'identity_lock_root': str(tmp_path / 'locks'),
        'original_identity_lock_roots': [],
        'state': {'active_root': str(root), 'identity_id': identity_id}}
    if profile:
        manifest['inference_profile'] = dict(PROFILE)
    # Manifest authentication/source gates have independent real preflight tests.
    # This fixture starts the actual configured Core and checked lifespan after
    # that validation boundary, with providers and periodic jobs disabled.
    monkeypatch.setattr(private_release, 'runtime_manifest', lambda *_a, **_k: (deepcopy(manifest), purpose))
    original_factory = app_module.configured_app
    state = NS(app=None, before=None, transaction_depth=0, calls=[], prefixes=[], startup_flags=[])
    original_builder = build_private_interaction_prefix

    def forbidden(*_a, **_k):
        pytest.fail('Startup must not create a conversation, output or consumer')

    def factory(*args, **kwargs):
        state.startup_flags.append(kwargs['run_background'])
        kwargs['run_background'] = False
        app = original_factory(*args, **kwargs)
        state.app = app
        original_lifespan = app.router.lifespan_context
        @asynccontextmanager
        async def observed(application):
            async with original_lifespan(application):
                core = application.state.core
                original_transaction = core.store.transaction
                @contextmanager
                def tracked(*a, **k):
                    with original_transaction(*a, **k) as db:
                        state.transaction_depth += 1
                        try:
                            yield db
                        finally:
                            state.transaction_depth -= 1
                core.store.transaction = tracked
                state.before = registry(core)
                core.input = core.draft = forbidden
                application.state.interaction.observe = forbidden
                application.state.interaction.connect = forbidden
                application.state.interaction.synthesize = forbidden
                if lose_owner:
                    core.store.claim(core.token[0])
                yield
        app.router.lifespan_context = observed
        return app
    monkeypatch.setattr(app_module, 'configured_app', factory)

    def builder(identity, **kwargs):
        state.prefixes.append(deepcopy(identity))
        return original_builder(identity, **kwargs)
    monkeypatch.setattr('gamma.persona.coordination.build_private_interaction_prefix', builder)

    def warm(profile, settings, transcribe=None, *, private_prefix=None, maximum_seconds=30):
        assert state.transaction_depth == 0, 'Identity transaction must end before warmup HTTP'
        assert maximum_seconds == 30
        state.calls.append(private_prefix)
        assert registry(state.app.state.core) == state.before
        if private_prefix is None:
            cache = {'status': 'not_requested'}
        else:
            cache = {'status': prefix_status,
                'prefix_sha256': hashlib.sha256(private_prefix.encode()).hexdigest(),
                'prefix_characters': len(private_prefix)}
        return {'status': 'ready', 'models': {}, 'prefix_cache': cache}
    monkeypatch.setattr('gamma.coordination.inference_profile.warm_release_inference', warm)
    app = private_release.create_api_app('validated-fixture', purpose=purpose)
    bearer = private_release.release_api_bearer(manifest, purpose, settings.api_bearer_token)
    return app, state, canonical, {'Authorization': 'Bearer ' + bearer}


@pytest.mark.parametrize('prefix_status', ['ready', 'unavailable'])
def test_real_release_lifespan_passes_current_canonical_prefix_without_state_changes(
        tmp_path, monkeypatch, prefix_status):
    app, state, canonical, headers = prepared(tmp_path, monkeypatch, prefix_status=prefix_status)
    with TestClient(app) as client:
        assert state.calls == [build_private_interaction_prefix(canonical, compact_json=True)]
        assert state.prefixes == [canonical]
        assert 'STARTUP_CANONICAL_EDIT' in state.calls[0]
        assert 'DECOY_PERSON_RECORD' not in state.calls[0] and 'DECOY_BASELINE' not in state.calls[0]
        assert '# Sourced observations' not in state.calls[0]
        assert '# Private Coordination Context' not in state.calls[0]
        assert '# Recent Conversation Turns' not in state.calls[0]
        assert registry(app.state.core) == state.before
        status = client.get('/v1/coordination/private-release', headers=headers)
        assert status.status_code == 200
        assert status.json()['inference_warmup']['prefix_cache']['status'] == prefix_status
        assert 'STARTUP_CANONICAL_EDIT' not in json.dumps(status.json())
        assert 'DECOY_' not in json.dumps(status.json())
        assert registry(app.state.core) == state.before
        assert state.transaction_depth == 0
    assert state.startup_flags == [True]


@pytest.mark.parametrize('purpose,profile', [('test', True), ('permanent', False)])
def test_nonselected_lifespans_do_not_build_or_prime_a_private_prefix(
        tmp_path, monkeypatch, purpose, profile):
    app, state, _canonical, headers = prepared(tmp_path, monkeypatch, purpose=purpose, profile=profile)
    with TestClient(app) as client:
        assert state.calls == state.prefixes == []
        assert registry(app.state.core) == state.before
        assert 'inference_warmup' not in client.get('/v1/coordination/private-release', headers=headers).json()


def test_lost_owner_propagates_before_prefix_priming(tmp_path, monkeypatch):
    app, state, _canonical, _headers = prepared(tmp_path, monkeypatch, lose_owner=True)
    with pytest.raises(OwnershipLost):
        with TestClient(app):
            pass
    assert state.calls == state.prefixes == []
