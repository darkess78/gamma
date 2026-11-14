"""Private-release guards, immutable pins and atomic identity admission."""
from copy import deepcopy
import json
from pathlib import Path
import uuid

import pytest

from gamma.coordination.core import Core
from gamma.coordination.store import Store
from gamma.coordination import private_release_manifest as contract
from gamma.coordination.transfer import export_snapshot
from gamma.coding.durable_practice import PracticeRunner
from gamma.schemas.response import AssistantResponse


def core(root, **kwargs):
    store = root if isinstance(root, Store) else Store(root)
    return Core(store, draft=lambda *_:AssistantResponse(display_text='Controlled reply.',delivery_mode='text_only'),
        runner=PracticeRunner(store,propose=lambda *_:{},check=lambda *_:{}), **kwargs)


@pytest.fixture
def packet(tmp_path,monkeypatch):
    monkeypatch.setattr(contract,'effective_voice_pin',lambda *_:{'configuration_sha256':'0'*64,'reference_audio_sha256':None,'provider':'stub'})
    identifier=uuid.uuid4().hex
    a=tmp_path/('private-release-test-'+identifier+'-a')
    b=tmp_path/('private-release-test-'+identifier+'-b')
    for folder in (a,b):
        for name in contract.CRITICAL:
            path=folder/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('# source pin fixture\n')
    state=a/'data/coordination/state'
    owner=core(state);owner.close()
    baseline=tmp_path/'baseline';export_snapshot(state,baseline)
    return contract.prepare_manifest(a,state,baseline,b,tmp_path/'shared-locks',model='fixture',voice='fixture',original_lock_roots=[tmp_path/'original-locks'])


def test_exact_roles_effect_scope_and_coverage(packet):
    contract.validate_manifest(packet)
    for target in ('phone','public_twitch'):
        changed=deepcopy(packet);changed['effects'].pop(target)
        with pytest.raises(ValueError):contract.validate_manifest(changed)
    for target in contract.INACTIVE:
        changed=deepcopy(packet);changed['effects'][target]='core'
        with pytest.raises(ValueError):contract.validate_manifest(changed)
    changed=deepcopy(packet);changed['roles']['api']=True
    with pytest.raises(ValueError):contract.validate_manifest(changed)
    changed=deepcopy(packet);changed['coverage']['voice_selector']='gamma-dashboard.service'
    with pytest.raises(ValueError,match='wrong_drain'):contract.validate_manifest(changed)
    changed=deepcopy(packet);changed['drain_targets'].pop('gamma-coding-idle.service')
    with pytest.raises(ValueError):contract.validate_manifest(changed)


def test_common_lease_cannot_silently_follow_a_checkout(packet):
    packet['identity_lock_root']=packet['source']['root']+'/locks'
    with pytest.raises(ValueError,match='outside_release_checkouts'):contract.validate_manifest(packet)
    packet['identity_lock_root']='relative/locks'
    with pytest.raises(ValueError,match='absolute'):contract.validate_manifest(packet)


def test_existing_common_lock_root_rejects_public_permissions_and_symlinks(packet,tmp_path):
    import os
    lock=Path(packet['identity_lock_root']);lock.mkdir(mode=0o700)
    contract.validate_manifest(packet)
    if os.name=='posix':
        lock.chmod(0o777)
        with pytest.raises(ValueError,match='owned_and_private'):contract.validate_manifest(packet)
        lock.chmod(0o700)
        alias=tmp_path/'link';alias.symlink_to(lock,target_is_directory=True)
        packet['identity_lock_root']=str(alias)
        with pytest.raises(ValueError,match='symlink'):contract.validate_manifest(packet)


def test_proposed_or_active_legacy_cannot_pass_permanent_preflight(packet,monkeypatch):
    monkeypatch.setattr(contract,'observe_target',lambda *_:{'inactive_observed':True})
    assert contract.preflight(packet)['blockers']==['owner_activation_decision_missing']
    packet['status']='owner_approved'
    assert contract.preflight(packet)['ok']
    monkeypatch.setattr(contract,'observe_target',lambda *_:{'inactive_observed':False})
    result=contract.preflight(packet)
    assert result['blockers']==['enumerated_legacy_drain_not_observed']
    assert not result['noncooperating_legacy_writers_fenced']


@pytest.mark.parametrize('which',['source','reader'])
def test_changed_source_pin_fails_before_owner_claim(packet,which):
    state=Store(packet['state']['active_root']);before=state.read('brain','state')
    path=Path(packet[which]['root'])/'src/gamma/coordination/core.py';path.write_text('# changed\n')
    assert which+'_source_changed' in contract.preflight(packet,purpose='test')['blockers']
    assert state.read('brain','state')==before


def test_baseline_hash_is_immutable_but_live_journal_progress_is_allowed(packet):
    owner=core(Path(packet['state']['active_root']))
    owner.input('A later accepted exchange.','release-fixture','later')
    owner.close()
    assert contract.preflight(packet,purpose='test')['ok']
    manifest=Path(packet['state']['baseline_bundle'])/'manifest.json'
    manifest.write_text(manifest.read_text()+' ')
    assert 'baseline_manifest_changed' in contract.preflight(packet,purpose='test')['blockers']


@pytest.mark.parametrize('identity',[None,'b'*32])
def test_manifest_expected_identity_never_initialises_or_claims_wrong_state(tmp_path,identity):
    store=Store(tmp_path/'state')
    if identity:
        with store.transaction() as db:store.put(db,'personal_meta','state',{'schema_version':1,'identity_id':identity})
    before=store.read('brain','state');meta=store.read('personal_meta','state')
    with pytest.raises(ValueError,match='identity_missing_or_changed'):
        core(store,expected_identity_id='a'*32,identity_lock_root=tmp_path/'locks')
    assert store.read('brain','state')==before
    assert store.read('personal_meta','state')==meta


@pytest.mark.parametrize('identity',['A'*32,'a'*31,True,'../bad'])
def test_expected_identity_is_strict_before_mutation(tmp_path,identity):
    store=Store(tmp_path/'state');before=store.read('brain','state')
    with pytest.raises(ValueError,match='invalid_expected'):
        core(store,expected_identity_id=identity)
    assert store.read('brain','state')==before and store.read('personal_meta','state') is None
    with pytest.raises(ValueError,match='invalid_expected'):store.claim(0,expected_identity_id=identity)
    assert store.read('brain','state')==before


def test_identity_swap_at_claim_boundary_is_atomic(tmp_path,monkeypatch):
    owner=core(tmp_path/'state');identity=owner.personal_state()['identity_id'];owner.close()
    store=Store(tmp_path/'state');before=store.read('brain','state');original=store.claim
    def swapped(generation,**kwargs):
        with store.transaction() as db:
            meta=store.get(db,'personal_meta','state');meta['identity_id']='f'*32
            store.put(db,'personal_meta','state',meta)
        return original(generation,**kwargs)
    monkeypatch.setattr(store,'claim',swapped)
    with pytest.raises(ValueError,match='identity_changed_before_claim'):
        core(store,expected_identity_id=identity,identity_lock_root=tmp_path/'common')
    assert store.read('brain','state')==before


def test_isolated_default_still_creates_and_recovers_identity(tmp_path):
    owner=core(tmp_path/'state');identity=owner.personal_state()['identity_id'];owner.close()
    second=core(tmp_path/'state')
    assert second.personal_state()['identity_id']==identity and second.token[0]==2
    second.close()


def test_additional_lease_partial_failure_cleans_up_without_claim(tmp_path):
    from gamma.coordination.store import process_lock
    owner=core(tmp_path/'state');identity=owner.personal_state()['identity_id'];owner.close()
    store=Store(tmp_path/'state');before=store.read('brain','state')
    available=tmp_path/'a-available';blocked=tmp_path/'z-blocked'
    with process_lock(blocked/(identity+'.lock')):
        with pytest.raises(OSError):
            core(store,identity_lock_root=available,expected_identity_id=identity,additional_identity_lock_roots=[blocked])
        assert store.read('brain','state')==before
        with process_lock(available/(identity+'.lock')):pass
    fresh=Store(tmp_path/'fresh')
    with pytest.raises(ValueError,match='absolute'):
        core(fresh,additional_identity_lock_roots=['relative'])
    assert fresh.read('personal_meta','state') is None


def test_explicit_shared_identity_lease_blocks_distinct_state_copies(packet,tmp_path):
    from gamma.coordination.transfer import restore_snapshot
    other=Path(packet['reader']['root'])/'data/coordination/state'
    restore_snapshot(packet['state']['baseline_bundle'],other)
    owner=core(Path(packet['state']['active_root']),identity_lock_root=tmp_path/'common',
               expected_identity_id=packet['state']['identity_id'])
    try:
        before=Store(other).read('brain','state')
        with pytest.raises(OSError):
            core(other,identity_lock_root=tmp_path/'common',expected_identity_id=packet['state']['identity_id'])
        assert Store(other).read('brain','state')==before
    finally:owner.close()


def test_current_records_do_not_gain_legacy_p1_compatibility(packet,tmp_path):
    from gamma.coordination.transfer import project_legacy
    store=Store(packet['state']['active_root'])
    with store.transaction() as db:store.put(db,'phone_device','fiction',{'revoked':True})
    before=store.read('brain','state')
    target=tmp_path/'legacy';target.mkdir()
    with pytest.raises(ValueError,match='connected_channels_require_current_reader'):
        project_legacy(packet['state']['active_root'],target)
    assert store.read('brain','state')==before and not (target/'data').exists()


def test_reversal_keeps_common_identity_lease_through_export_and_restore(packet,monkeypatch):
    from gamma.coordination import transfer
    from gamma.coordination.private_release import compatible_reversal
    state=Path(packet['state']['active_root']);before=Store(state).read('brain','state')
    original=transfer.export_snapshot;probed=[]
    def checked_export(*args):
        with pytest.raises(OSError):
            core(state,identity_lock_root=Path(packet['identity_lock_root']),expected_identity_id=packet['state']['identity_id'])
        assert Store(state).read('brain','state')==before
        probed.append(True)
        return original(*args)
    monkeypatch.setattr(transfer,'export_snapshot',checked_export)
    target=Path(packet['reader']['root'])/'data/coordination/reversed'
    assert compatible_reversal(packet,target)['latest_closed_state_used']
    assert probed==[True]
    owner=core(target,identity_lock_root=Path(packet['identity_lock_root']),expected_identity_id=packet['state']['identity_id'])
    owner.close()


@pytest.mark.parametrize('component,module',[
    ('gamma.integrations.twitch','gamma.integrations.twitch.worker'),
    ('gamma.integrations.twitch','gamma.integrations.twitch.eventsub'),
    ('gamma.integrations.discord','gamma.integrations.discord.worker'),
    ('gamma.coordination.discord_client','gamma.coordination.discord_client'),
    ('gamma.coding','gamma.coding.worker'),('gamma.coding','gamma.coding.autonomous'),
    ('gamma.coding','gamma.improvement.worker')])
def test_known_adapter_actual_python_module_argv_cannot_be_reported_inactive(component,module):
    assert contract.known_module_argv(['/project/.venv/bin/python','-m',module,'--duration','30'],component)
    assert not contract.known_module_argv(['echo',module],component)
    assert not contract.known_module_argv(['python','-m',module+'.unrelated'],component)


def test_module_census_matches_current_dashboard_launch_constants():
    import ast
    path=Path(__file__).resolve().parents[1]/'src/gamma/dashboard/service.py'
    tree=ast.parse(path.read_text())
    names={'TWITCH_WORKER_MODULE':'gamma.integrations.twitch','TWITCH_EVENTSUB_MODULE':'gamma.integrations.twitch',
           'DISCORD_TEXT_MODULE':'gamma.integrations.discord','IMPROVEMENT_WORKER_MODULE':'gamma.coding'}
    found={node.targets[0].id:node.value.value for node in ast.walk(tree)
           if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name)
           and node.targets[0].id in names and isinstance(node.value,ast.Constant)}
    assert set(found)==set(names)
    for name,module in found.items():
        assert contract.known_module_argv(['python','-m',module],names[name])


@pytest.mark.parametrize('target',['gamma.main:app','gamma.dashboard.main:app'])
def test_old_asgi_owner_entrypoints_are_not_confused_with_new_release(target):
    # Exact strings also appear in the current packaged supervisor source.
    source=Path(__file__).resolve().parents[1]/'src/gamma/supervisor/manager.py'
    assert '"'+target+'"' in source.read_text()
    for argv in (['python','-m','uvicorn',target],['/venv/bin/uvicorn',target],['python','/venv/bin/uvicorn',target]):
        assert contract.known_module_argv(argv,'gamma.legacy_apps')
    assert not contract.known_module_argv(['python','-m','uvicorn','gamma.coordination.private_release:create_api_app','--factory'],'gamma.legacy_apps')
    assert not contract.known_module_argv(['echo',target],'gamma.legacy_apps')
    for script in ('start_shana.py','start_dashboard.py','start_all.sh','startup.sh'):
        assert contract.known_module_argv(['python','/project/scripts/'+script],'gamma.legacy_apps')


def test_actual_proc_sees_controlled_old_asgi_signature_without_running_old_app(tmp_path):
    import os,subprocess,sys
    if os.name!='posix':pytest.skip('Actual proc census is Gamma POSIX only')
    (tmp_path/'uvicorn.py').write_text('import time\nprint("ready",flush=True)\ntime.sleep(20)\n')
    process=subprocess.Popen([sys.executable,'-m','uvicorn','gamma.main:app'],cwd=tmp_path,
        env={**os.environ,'PYTHONPATH':str(tmp_path)},stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert process.stdout.readline().strip()=='ready'
        result=contract.observe_target({'kind':'module_census','name':'gamma.legacy_apps'})
        assert not result['inactive_observed'] and process.pid in result['matching_pids']
    finally:
        process.terminate();process.wait(timeout=5);process.stdout.close();process.stderr.close()


@pytest.mark.parametrize('component,module',[
    ('gamma.integrations.twitch','gamma.integrations.twitch.worker'),
    ('gamma.integrations.twitch','gamma.integrations.twitch.eventsub'),
    ('gamma.integrations.discord','gamma.integrations.discord.worker'),
    ('gamma.coding','gamma.coding.autonomous'),('gamma.coding','gamma.improvement.worker')])
def test_actual_proc_census_observes_controlled_child_with_known_module_argv(tmp_path,component,module):
    import os,subprocess,sys
    if os.name!='posix':pytest.skip('Actual proc census is Gamma POSIX only')
    # Deliberately empty fixture packages: no real adapter, credentials or network.
    parts=module.split('.');directory=tmp_path
    for name in parts[:-1]:
        directory/=name;directory.mkdir();(directory/'__init__.py').write_text('')
    (directory/(parts[-1]+'.py')).write_text('import time\nprint("ready",flush=True)\ntime.sleep(20)\n')
    process=subprocess.Popen([sys.executable,'-m',module],cwd=tmp_path,
        env={**os.environ,'PYTHONPATH':str(tmp_path)},stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert process.stdout.readline().strip()=='ready'
        result=contract.observe_target({'kind':'module_census','name':component})
        assert result['inactive_observed'] is False and process.pid in result['matching_pids']
    finally:
        process.terminate();process.wait(timeout=5);process.stdout.close();process.stderr.close()


def test_explicit_voice_resolution_preserves_default_and_pins_options_reference_without_content(tmp_path,monkeypatch):
    from gamma.config import settings
    from gamma.voice import voice_profiles as voices
    reference=tmp_path/'reference.wav';reference.write_bytes(b'controlled reference bytes')
    monkeypatch.setattr(settings,'project_root',tmp_path)
    monkeypatch.setattr(settings,'tts_profile','selected')
    values={'provider':'stub','qwen_tts_endpoint':'http://127.0.0.1:9','qwen_tts_reference_audio':str(reference),
            'qwen_tts_reference_text':'PRIVATE_REFERENCE_TEXT','qwen_tts_extra_json':{'seed':7}}
    monkeypatch.setattr(voices,'get_voice_profile',lambda name:voices.VoiceProfile(name,name,'stub',values=values)
                        if name in {'selected','explicit'} else None)
    assert voices.resolve_tts_config().profile_id=='selected'
    assert voices.resolve_tts_config('explicit').profile_id=='explicit'
    assert settings.tts_profile=='selected'
    first=contract.effective_voice_pin('explicit',tmp_path)
    assert 'PRIVATE_REFERENCE_TEXT' not in json.dumps(first) and str(reference) not in json.dumps(first)
    reference.write_bytes(b'changed reference bytes')
    second=contract.effective_voice_pin('explicit',tmp_path)
    assert first['reference_audio_sha256']!=second['reference_audio_sha256']
    values['qwen_tts_extra_json']={'seed':8}
    assert contract.effective_voice_pin('explicit',tmp_path)['configuration_sha256']!=second['configuration_sha256']
    with pytest.raises(ValueError,match='profile_missing'):contract.effective_voice_pin('missing',tmp_path)
    for endpoint in ('http://user:password@127.0.0.1:9','https://example.com/voice','http://127.0.0.1:9/?token=x'):
        values['qwen_tts_endpoint']=endpoint
        with pytest.raises(ValueError,match='clean_loopback'):contract.effective_voice_pin('explicit',tmp_path)
    values['qwen_tts_endpoint']='http://127.0.0.1:9';values['qwen_tts_extra_json']={'api_key':'DO_NOT_SERIALIZE'}
    with pytest.raises(ValueError,match='not_reviewed'):contract.effective_voice_pin('explicit',tmp_path)


def test_short_text_primary_stays_pinned_when_routing_disabled(monkeypatch):
    from gamma.config import settings
    from gamma.llm.local_adapter import LocalLLMAdapter
    monkeypatch.setattr(settings,'local_llm_model','manifest-primary')
    monkeypatch.setattr(settings,'local_llm_enable_routing',False)
    monkeypatch.setattr(settings,'llm_router_enabled',False)
    adapter=LocalLLMAdapter()
    monkeypatch.setattr(adapter,'_routed_model_name',lambda **_:pytest.fail('routing called despite release fence'))
    assert adapter._model_name_for_request(has_images=False,user_text='hi')=='manifest-primary'


def test_configured_mock_practice_work_voice_and_vision_never_contact_provider(tmp_path,monkeypatch):
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    import threading
    from fastapi.testclient import TestClient
    from gamma.config import settings
    from gamma.coordination.app import configured_app
    from gamma.coordination import interaction_routes,shared_vision
    from test_coordination_work import specification
    from test_private_interaction import observation,wav
    calls=[]
    class Trap(BaseHTTPRequestHandler):
        def do_POST(self):calls.append(self.path);self.send_response(503);self.end_headers()
        do_GET=do_POST
        def log_message(self,*_):pass
    trap=ThreadingHTTPServer(('127.0.0.1',0),Trap)
    thread=threading.Thread(target=trap.serve_forever,daemon=True);thread.start()
    monkeypatch.setattr(settings,'llm_provider','mock')
    monkeypatch.setattr(settings,'local_llm_endpoint','http://127.0.0.1:'+str(trap.server_port))
    monkeypatch.setattr(settings,'api_bearer_token','mock-owner')
    monkeypatch.setattr(settings,'local_llm_supports_vision',True)
    monkeypatch.setenv('GAMMA_PRACTICE_MODEL','mock-fixture')
    monkeypatch.setattr(interaction_routes,'local_interaction_factory',lambda *_:pytest.fail('mock constructed real voice factory'))
    monkeypatch.setattr(shared_vision,'local_image_analyzer',lambda:pytest.fail('mock constructed real vision factory'))
    try:
        app=configured_app(Path(__file__).resolve().parents[1],tmp_path/'state',run_background=False)
        with TestClient(app) as client:
            client.headers['Authorization']='Bearer mock-owner'
            practice=client.post('/v1/coordination/control',json={'audience':'owner_private','request_id':'practice',
                'action':'practice','exercise_id':'retry_budget'}).json()['activity']['worker_run']
            app.state.core.runner.run(practice)
            assert app.state.core.store.read('run',practice)['status']=='blocked'
            assert client.get('/v1/coordination/status',params={'audience':'owner_private'}).status_code==200
            prepared=client.post('/v1/coordination/work',json={'audience':'owner_private','request_id':'prepare',
                'action':'prepare','spec':specification(),'authorize_start':True})
            assert prepared.status_code==200,prepared.text
            task=prepared.json()['activity']['id']
            started=client.post('/v1/coordination/work',json={'audience':'owner_private','request_id':'start',
                'action':'start','task_id':task})
            assert started.status_code==200,started.text
            import time
            end=time.monotonic()+10
            while time.monotonic()<end:
                app.state.core.tick()
                result=app.state.core.store.read('run',task)
                if result and result['status']=='blocked':break
                time.sleep(.02)
            assert result and result['status']=='blocked'
            interaction=app.state.interaction
            interaction.connect('desktop');interaction.control('voice_enabled',True);interaction.control('speech_enabled',True)
            assert interaction.status()['stt_available'] is False and interaction.status()['tts_available'] is False
            with pytest.raises(ValueError,match='transcription provider unavailable'):
                interaction.submit_audio(observation('voice-fixture'),wav())
            interaction.observe(observation('tts-fixture'))
            with app.state.core.store.transaction(app.state.core.token) as db:
                outputs=app.state.core.store.list(db,'interaction_output',None)
                assert not any(row.get('audio_available') for row in outputs)
            assert app.state.shared_activity.analyze_image is None
            selected=app.state.shared_activity.control('mock-select','select',{'source_id':'mock-window','label':'Controlled window','kind':'osrs_screen'})
            import base64
            image=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aYYsAAAAASUVORK5CYII=')
            observed=app.state.shared_activity.observe_image({'observation_id':'mock-image','source_id':'mock-window',
                'source_epoch':selected['source_epoch'],'sequence':0,'captured_at':time.time(),'kind':'osrs_screen'},image,'image/png')
            assert observed['status']=='unavailable' and observed['reason']=='vision_analyzer_not_configured'
            assert calls==[]
    finally:
        trap.shutdown();trap.server_close();thread.join(timeout=2)


def test_cli_default_inert_and_production_port_refusal(tmp_path):
    import subprocess,sys
    script=Path(__file__).resolve().parents[1]/'scripts/private_release.py'
    options={'creationflags':subprocess.CREATE_NO_WINDOW} if hasattr(subprocess,'CREATE_NO_WINDOW') else {}
    result=subprocess.run([sys.executable,str(script)],capture_output=True,text=True,timeout=10,**options)
    assert result.returncode==0 and json.loads(result.stdout)['activation_performed'] is False
    blocked=subprocess.run([sys.executable,str(script),'serve-test','--manifest',str(tmp_path/'absent.json'),
        '--api-port','8000','--mock'],capture_output=True,text=True,timeout=10,**options)
    assert blocked.returncode==1 and 'cannot_bind_production_ports' in blocked.stdout


def test_private_discord_manifest_requires_exact_owner_binding(packet):
    binding = dict(owner_user_id='1', dm_channel_id='2', voice_guild_id='3', voice_channel_id='4', bot_user_id='5')
    packet['effects']['live_discord'] = 'owner_private'
    with pytest.raises(ValueError):contract.validate_manifest(packet)
    packet['discord'] = binding
    contract.validate_manifest(packet)
    for change in [None, dict(binding, extra='6'), dict(binding, bot_user_id='1'), dict(binding, owner_user_id=True), dict(binding, voice_guild_id=None)]:
        changed = deepcopy(packet);changed['discord'] = change
        with pytest.raises(ValueError):contract.validate_manifest(changed)
    changed = deepcopy(packet);changed['effects']['live_discord'] = 'inactive'
    with pytest.raises(ValueError):contract.validate_manifest(changed)
    for key in ('public_twitch', 'real_minecraft'):
        changed = deepcopy(packet);changed['effects'][key] = 'core'
        with pytest.raises(ValueError):contract.validate_manifest(changed)
