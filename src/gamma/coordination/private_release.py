"""Specific authenticated private-release factories; no service or proxy mutation."""
from contextlib import asynccontextmanager,ExitStack
import hashlib
import hmac
import os
from pathlib import Path
from urllib.parse import urlsplit

from .private_release_manifest import absolute_path, digest, load_manifest, preflight, effective_voice_pin, compatibility_lock_roots


def release_api_bearer(manifest, purpose, base_token):
    """Bind each internal request to this exact release and purpose, without storage."""
    if purpose not in {'test', 'permanent'}:
        raise ValueError('invalid_private_release_purpose')
    if not isinstance(base_token, str) or not base_token:
        raise ValueError('private_release_requires_base_api_credential')
    context = b'gamma-private-release-api-v1\0' + purpose.encode('ascii') + b'\0' + digest(manifest).encode('ascii')
    return hmac.new(base_token.encode('utf-8'), context, hashlib.sha256).hexdigest()


def runtime_manifest(path=None, *, purpose=None):
    purpose = purpose or os.environ.get('GAMMA_PRIVATE_RELEASE_PURPOSE', 'permanent')
    if purpose not in {'test', 'permanent'}:
        raise ValueError('invalid_private_release_purpose')
    manifest = load_manifest(path or os.environ.get('GAMMA_PRIVATE_RELEASE_MANIFEST', ''))
    project = absolute_path(manifest['source']['root'])
    from ..config import settings
    if (Path(__file__).resolve().parents[3] != project or settings.project_root.resolve() != project
        or Path.cwd().resolve() != project):
        raise ValueError('release_source_resolution_mismatch')
    if purpose == 'test':
        import re
        if not re.fullmatch(r'private-release-test-[a-f0-9]{32}-[ab]', project.name):
            raise ValueError('finite_test_requires_disposable_release_checkout')
    elif os.environ.get('GAMMA_PRIVATE_RELEASE_ENABLE') != '1':
        raise ValueError('operator_private_release_opt_in_required')
    result = preflight(manifest, purpose=purpose)
    if not result['ok']:
        raise ValueError('private_release_preflight_blocked:' + ','.join(result['blockers']))
    from ..dashboard.auth import dashboard_auth_ready
    if (not settings.api_auth_enabled or not settings.api_bearer_token
        or not settings.dashboard_auth_enabled or not dashboard_auth_ready()):
        raise ValueError('private_release_requires_api_and_dashboard_authentication')
    if settings.llm_router_enabled or settings.local_llm_enable_routing or settings.llm_provider.lower() not in {'local', 'ollama', 'mock'}:
        raise ValueError('private_release_requires_explicit_local_provider')
    if purpose == 'permanent' and settings.llm_provider.lower() == 'mock':
        raise ValueError('mock_provider_is_not_a_permanent_release')
    if str(settings.local_llm_model) != manifest['models']['primary'] or str(settings.tts_profile or '') != manifest['models']['voice']:
        raise ValueError('release_model_or_voice_reference_changed')
    if os.environ.get('GAMMA_PRACTICE_MODEL')!=manifest['models']['primary']:
        raise ValueError('release_work_model_reference_changed')
    if str(settings.local_llm_vision_model or settings.local_llm_model)!=manifest['models']['vision']:
        raise ValueError('release_vision_model_reference_changed')
    from .inference_profile import validate_inference_runtime
    validate_inference_runtime(manifest.get('inference_profile'), settings)
    if effective_voice_pin(manifest['models']['voice'],project)!=manifest['models']['voice_pin']:
        raise ValueError('effective_release_voice_changed')
    endpoint = urlsplit(settings.local_llm_endpoint)
    if (endpoint.scheme != 'http' or endpoint.hostname not in {'127.0.0.1', '::1'}
        or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment
        or endpoint.path not in {'', '/'}):
        raise ValueError('private_release_requires_local_inference_endpoint')
    from sqlalchemy.engine import make_url
    database = make_url(settings.database_url)
    if (database.drivername != 'sqlite' or not database.database
        or absolute_path(database.database) != absolute_path(manifest['state']['active_root']) / 'conversation.sqlite3'):
        raise ValueError('release_conversation_database_must_match_manifest_state')
    return manifest, purpose


def create_api_app(manifest_path=None, *, purpose=None):
    """Actual opt-in factory, usable by uvicorn after explicit operator approval."""
    manifest, purpose = runtime_manifest(manifest_path, purpose=purpose)
    from ..config import settings
    from .app import configured_app
    app = configured_app(absolute_path(manifest['source']['root']),
                         absolute_path(manifest['state']['active_root']),
                          bearer_token=release_api_bearer(manifest, purpose, settings.api_bearer_token),
                         identity_lock_root=absolute_path(manifest['identity_lock_root']),
                         expected_identity_id=manifest['state']['identity_id'],
                         additional_identity_lock_roots=compatibility_lock_roots(manifest),
                         run_background=purpose != 'test',
                         runtime_label='owner-private release rehearsal' if purpose == 'test' else 'owner-private release',
                         inference_profile=manifest.get('inference_profile'))
    app.title = 'Shana private release rehearsal' if purpose == 'test' else 'Shana private release'
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def checked_lifespan(application):
        # Revalidate immediately at startup; factory construction may precede bind.
        current, _ = runtime_manifest(manifest_path, purpose=purpose)
        if digest(current) != digest(manifest):
            raise ValueError('release_manifest_changed_before_startup')
        async with original_lifespan(application):
            application.state.private_release = {'release_id': manifest['release_id'],
                'manifest_sha256': digest(manifest), 'identity_id': manifest['state']['identity_id'],
                'scope': 'private_release_owner', 'purpose': purpose,
                'noncooperating_legacy_writers_fenced': False}
            if purpose == 'permanent' and manifest.get('inference_profile'):
                import asyncio
                from .inference_profile import warm_release_inference
                from ..persona.coordination import build_private_interaction_prefix
                core = application.state.core
                with core.store.transaction(core.token) as db:
                    identity = core.personal.identity(db)
                private_prefix = build_private_interaction_prefix(identity, compact_json=True)
                application.state.private_release['inference_warmup'] = await asyncio.to_thread(
                    warm_release_inference, manifest['inference_profile'], settings,
                    getattr(application.state.interaction, 'transcribe', None),
                    private_prefix=private_prefix)
            yield

    app.router.lifespan_context = checked_lifespan

    @app.get('/v1/coordination/private-release')
    def private_release_status():
        return app.state.private_release

    return app


def create_dashboard_app(manifest_path=None, *, purpose=None):
    """Same authenticated dashboard, bound to the exact matching release owner."""
    manifest, purpose = runtime_manifest(manifest_path, purpose=purpose)
    from ..config import settings
    from ..dashboard.shana_client import ShanaApiClient
    from .dashboard import build_app
    origin = os.environ.get('GAMMA_PRIVATE_RELEASE_API_ORIGIN', '')
    url = urlsplit(origin)
    try:
        port = url.port
    except ValueError as exc:
        raise ValueError('invalid_release_api_origin') from exc
    if (url.scheme != 'http' or url.hostname not in {'127.0.0.1', '::1'}
        or not port or url.username or url.password or url.path not in {'', '/'}
        or url.query or url.fragment or port == 11434):
        raise ValueError('release_dashboard_requires_literal_loopback_api')
    if purpose == 'permanent' and port != manifest['roles']['api']:
        raise ValueError('release_api_role_requires_port_8000')
    if purpose == 'test' and (not 1024 <= port <= 65535 or port in {8000, 8001, 8080}):
        raise ValueError('release_test_must_not_use_production_ports')
    client = ShanaApiClient(base_url=origin,
        bearer_token=release_api_bearer(manifest, purpose, settings.api_bearer_token))
    try:
        observed = client.request_json('GET', '/v1/coordination/private-release')
        if any(observed.get(key) != expected for key, expected in {
            'scope': 'private_release_owner', 'release_id': manifest['release_id'],
            'manifest_sha256': digest(manifest), 'identity_id': manifest['state']['identity_id'],
            'purpose': purpose}.items()):
            raise ValueError('dashboard_release_owner_mismatch')
        app = build_app(client)
        app.title = 'Shana private release dashboard'
        return app
    except BaseException:
        client.close()
        raise


def compatible_reversal(manifest, destination):
    """Prepare latest current state for the pinned reader; never activates it."""
    from .transfer import export_snapshot, restore_snapshot
    from .store import process_lock
    active = absolute_path(manifest['state']['active_root'])
    target = absolute_path(str(destination))
    if not target.is_relative_to(absolute_path(manifest['reader']['root']) / 'data/coordination'):
        raise ValueError('reversal_destination_must_belong_to_declared_reader')
    if target.exists():
        raise ValueError('reversal_requires_fresh_destination')
    # Keep cooperating readers out between the closed-state check and both copies.
    with ExitStack() as leases:
        roots=sorted({manifest['identity_lock_root'],*compatibility_lock_roots(manifest)})
        for path in roots:
            leases.enter_context(process_lock(absolute_path(path)/(manifest['state']['identity_id']+'.lock')))
        result = preflight(manifest, purpose='test')
        if not result['ok']:
            raise ValueError('reversal_preflight_blocked')
        bundle = active.parent / ('reversal-' + manifest['release_id'])
        exported = export_snapshot(active, bundle)
        restored = restore_snapshot(bundle, target)
    return {'prepared': True, 'activated': False, 'reader': 'coordination_current',
            'identity_id': restored['identity_id'], 'records': exported['record_count'],
            'latest_closed_state_used': True, 'baseline_snapshot_replayed': False,
            'scope':'point_in_time_preparation','activation_atomic':False,'requires_drain_until_target_claim':True}
