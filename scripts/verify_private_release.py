"""Actual copied-process private-release rehearsal, with controlled providers only."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
import wave

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'src'))


def rows(root,kind):
    with closing(sqlite3.connect((Path(root)/'coordination.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:
        return {key:json.loads(body) for key,body in db.execute('SELECT id,body FROM records WHERE kind=?',(kind,))}


def child(args):
    manifest=json.loads(args.manifest.read_text())
    credentials=json.loads(sys.stdin.readline())
    os.environ.update({'SHANA_PROJECT_ROOT':str(PROJECT),'GAMMA_PRIVATE_RELEASE_MANIFEST':str(args.manifest),
        'GAMMA_PRIVATE_RELEASE_PURPOSE':'permanent' if args.permanent_fixture else 'test','SHANA_API_AUTH_ENABLED':'true',
        'SHANA_API_BEARER_TOKEN':credentials['api_token'],'SHANA_DASHBOARD_AUTH_ENABLED':'true',
        'SHANA_DASHBOARD_AUTH_USERNAME':'private-release-owner','SHANA_DASHBOARD_CREDENTIALS_FILE':'', 'SHANA_DASHBOARD_AUTH_PASSWORD':credentials['password'],
        'SHANA_DASHBOARD_SESSION_SECRET':credentials['session_secret'],'SHANA_DASHBOARD_COOKIE_SECURE':'false',
        'SHANA_DATABASE_URL':'sqlite:///'+manifest['state']['active_root']+'/conversation.sqlite3',
        'SHANA_LLM_PROVIDER':'local' if args.permanent_fixture else 'mock','SHANA_LLM_ROUTER_ENABLED':'false',
        'SHANA_LOCAL_LLM_MODEL':manifest['models']['primary'],'GAMMA_PRACTICE_MODEL':manifest['models']['primary'],
        'SHANA_LOCAL_LLM_VISION_MODEL':manifest['models']['vision'],
        'SHANA_LOCAL_LLM_ENDPOINT':os.environ['GAMMA_PRIVATE_RELEASE_TEST_TRIPWIRE'],'SHANA_LOCAL_LLM_ENABLE_ROUTING':'false',
        'SHANA_TTS_PROFILE':manifest['models']['voice']})
    os.chdir(PROJECT)
    if args.old_default_fixture:
        from gamma.coordination.core import Core
        from gamma.coordination.store import Store
        from gamma.coding.durable_practice import PracticeRunner
        import gamma.coordination.core as core_module
        source=str(Path(core_module.__file__).resolve())
        (args.control/'actual-core-source.json').write_text(json.dumps({'source':source}))
        store=Store(manifest['state']['active_root'])
        owner=Core(store,draft=lambda *_:None,runner=PracticeRunner(store,propose=lambda *_:{},check=lambda *_:{}))
        try:
            print(json.dumps({'phase':'ready','pid':os.getpid(),'api':None,'dashboard':None,'old_default_core':True,'core_source':source}),flush=True)
            assert sys.stdin.readline().strip()=='stop'
        finally:owner.close()
        return
    from gamma.conversation.service import ConversationService
    from gamma.schemas.response import AssistantResponse
    calls=[]
    label='owner-private release' if args.permanent_fixture else 'owner-private release rehearsal'
    def controlled(_self,text,**_kwargs):
        if args.permanent_fixture:
            assert 'One continuing Shana, '+label+'.' in _kwargs['background_context']
        calls.append(text)
        (args.control/'calls.json').write_text(json.dumps(calls))
        if text.startswith('hold-'):
            (args.control/(text+'.started')).write_text('started')
            end=time.monotonic()+30
            while not (args.control/(text+'.release')).exists() and time.monotonic()<end:
                time.sleep(.02)
        return AssistantResponse(display_text='Controlled fixture reply: '+text,delivery_mode='text_only')
    ConversationService.respond=controlled
    from gamma.coordination import app as app_module
    if not args.permanent_fixture:
        actual_core=app_module.Core
        def controlled_core(*values,**options):
            options['draft']=lambda text,_context:controlled(None,text)
            return actual_core(*values,**options)
        app_module.Core=controlled_core
    if args.permanent_fixture:
        os.environ['GAMMA_PRIVATE_RELEASE_ENABLE']='1'
        from gamma.coordination import private_release_manifest
        from gamma.config import settings
        private_release_manifest.observe_target=lambda *_:{'inactive_observed':True,'controlled_observation_fixture':True}
        # Keep all semantic/work providers controlled BEFORE background admission.
        # Actual permanent guards still see configured local refs, not mock mode.
        ConversationService.__init__=lambda _self:None
        from gamma.coordination import turn_adapter,work_learner,interaction_routes,shared_vision
        from gamma.coding import learner
        from gamma.coordination.interaction import PrivateInteraction
        def unavailable(*_args,**_kwargs):raise RuntimeError('controlled_provider_unavailable_no_replay')
        learner.LocalPracticeLearner=lambda **_:unavailable
        work_learner.LocalWorkProposer=lambda **_:unavailable
        turn_adapter.PrivateTurnAdapter=lambda:object()
        interaction_routes.local_interaction_factory=lambda root:lambda core:PrivateInteraction(core)
        shared_vision.local_image_analyzer=lambda:None
        from gamma.coordination.core import Core
        from gamma.coordination.phone import PhoneAccess
        counts={'tick':0,'review':0,'contact':0};lock=threading.Lock()
        def count(name,function):
            def measured(*values,**options):
                with lock:
                    counts[name]+=1
                    temporary=args.control/'counts.tmp';temporary.write_text(json.dumps(counts));temporary.replace(args.control/'background-counts.json')
                return function(*values,**options)
            return measured
        Core.tick=count('tick',Core.tick);Core.review=count('review',Core.review)
        PhoneAccess.consider_due=count('contact',PhoneAccess.consider_due)
    from gamma.coordination.private_release import create_api_app,create_dashboard_app
    from gamma.coordination.hosting import loopback_server
    from gamma.config import settings
    before=rows(manifest['state']['active_root'],'brain')['state']
    settings.local_llm_enable_routing=True
    try:
        try:create_api_app()
        except ValueError as exc:assert 'explicit_local_provider' in str(exc)
        else:raise AssertionError('manifest_primary_routing_not_fenced')
    finally:settings.local_llm_enable_routing=False
    assert rows(manifest['state']['active_root'],'brain')['state']==before
    with loopback_server(create_api_app(),shutdown_timeout=10) as api:
        if args.permanent_fixture:
            print(json.dumps({'phase':'ready','pid':os.getpid(),'api':api,'dashboard':None,'permanent_background_fixture':True}),flush=True)
            import select
            while not select.select([sys.stdin],[],[],.2)[0]:pass
            assert sys.stdin.readline().strip()=='stop'
            print(json.dumps({'phase':'closing','background_counts':counts}),flush=True)
            return
        os.environ['GAMMA_PRIVATE_RELEASE_API_ORIGIN']=api
        with loopback_server(create_dashboard_app(),shutdown_timeout=10) as dashboard:
            print(json.dumps({'phase':'ready','pid':os.getpid(),'api':api,'dashboard':dashboard,
                              'routing_rejected_before_owner_claim':True}),flush=True)
            end=time.monotonic()+120
            import select
            while time.monotonic()<end:
                if select.select([sys.stdin],[],[],.2)[0]:
                    if sys.stdin.readline().strip()=='stop':break
    print(json.dumps({'phase':'closed','pid':os.getpid()}),flush=True)


def main(args):
    if os.name!='posix' or PROJECT.name in {'gamma-main','gamma-modular-phase1'}:
        raise ValueError('Gamma_isolated_checkout_required')
    import httpx
    from gamma.coordination.core import Core
    from gamma.coordination.store import Store,OwnershipLost
    from gamma.coordination.transfer import export_snapshot,restore_snapshot,project_legacy
    from gamma.coordination.private_release import compatible_reversal,release_api_bearer
    from gamma.coordination.private_release_manifest import prepare_manifest,source_inventory,preflight
    from gamma.coding.durable_practice import PracticeRunner
    from gamma.schemas.response import AssistantResponse
    started=time.monotonic();identifier=uuid.uuid4().hex
    out=args.output or PROJECT/'data/coordination/remaining-20261027'/('private-release-'+identifier)
    out.mkdir(mode=0o700,parents=True,exist_ok=False)
    scratch=Path(tempfile.mkdtemp(prefix='private-release-proof-',dir='/tmp'))
    a=Path('/tmp')/('private-release-test-'+identifier+'-a')
    b=Path('/tmp')/('private-release-test-'+identifier+'-b')
    a.mkdir(mode=0o700);b.mkdir(mode=0o700)
    report={'kind':'actual_copied_process_private_release','complete':False,'controlled_providers':True,
            'model_calls':0,'production_activation':False,'legacy_writers_globally_fenced':False,
            'atomic_legacy_import_proved':False,'source_before':source_inventory(PROJECT),'steps':[],
            'temporary_source_roots':[str(a),str(b)],'retained_scratch':str(scratch)}
    hosts=[];clients=[]
    provider_requests=[]
    class Tripwire(BaseHTTPRequestHandler):
        def do_GET(self):
            provider_requests.append(self.path);self.send_response(503);self.end_headers()
        do_POST=do_GET
        def log_message(self,*_args):pass
    tripwire=ThreadingHTTPServer(('127.0.0.1',0),Tripwire)
    tripwire_thread=threading.Thread(target=tripwire.serve_forever,daemon=True);tripwire_thread.start()
    tripwire_url='http://127.0.0.1:'+str(tripwire.server_port)
    def save():
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    def clone(root):
        for name in report['source_before']:
            target=root/name;target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            shutil.copyfile(PROJECT/name,target)
        profile=root/'config/voices.toml'
        profile.write_text(profile.read_text()+'''\n[profiles.release_fixture]
provider = "stub"
tts_model = "controlled-fixture"
tts_voice = "controlled-fixture"
rvc_enabled = false
qwen_tts_endpoint = "http://127.0.0.1:9"
qwen_tts_reference_audio = "./assets/release_fixture.wav"
qwen_tts_reference_text = "Fictional reference."
qwen_tts_extra_json = {}
''')
        reference=root/'assets/release_fixture.wav';reference.parent.mkdir(mode=0o700)
        with wave.open(str(reference),'wb') as audio:
            audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(16000);audio.writeframes(b'\0\0'*160)
    def prepare(root,state,baseline,reader,common,**_ignored):
        target=scratch/('prepared-'+uuid.uuid4().hex+'.json')
        result=subprocess.run([sys.executable,str(root/'scripts/private_release.py'),'prepare',
            '--source',str(root),'--state',str(state),'--baseline',str(baseline),'--reader',str(reader),
            '--lock-root',str(common),'--original-lock-root',str(PROJECT/'data/coordination/identities'),
            '--model','controlled-release-fixture','--voice','release_fixture','--output',str(target)],
            cwd=root,env={**os.environ,'SHANA_PROJECT_ROOT':str(root),'PYTHONPATH':str(root/'src')},
            capture_output=True,text=True,timeout=20)
        assert result.returncode==0,result.stdout+result.stderr
        return json.loads(target.read_text())
    def start(root,manifest,label,*,cli=False,permanent=False,old_default=False):
        control=scratch/label;control.mkdir(mode=0o700)
        credentials={key:secrets.token_urlsafe(32) for key in ('api_token','password','session_secret')}
        manifest_path=control/'manifest.json';manifest_path.write_text(json.dumps(manifest));manifest_path.chmod(0o600)
        argv=[sys.executable,str(root/'scripts/private_release.py'),'serve-test','--manifest',str(manifest_path),'--duration','5','--mock','--endpoint',tripwire_url] if cli else [
            sys.executable,str(root/'scripts/verify_private_release.py'),'--child','--manifest',str(manifest_path),'--control',str(control)]
        if permanent:argv.append('--permanent-fixture')
        if old_default:argv.append('--old-default-fixture')
        errors=(control/'stderr.log').open('w');log=(control/'stdout.log').open('w')
        process=subprocess.Popen(argv,cwd=root,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=errors,text=True,
                                 env={**os.environ,'PYTHONPATH':str(root/'src'),'TMPDIR':'/tmp',
                                      'GAMMA_PRIVATE_RELEASE_TEST_TRIPWIRE':tripwire_url})
        record={'process':process,'control':control,'credentials':credentials,'errors':errors,'log':log,'messages':queue.Queue()}
        hosts.append(record)
        def read():
            for line in process.stdout:
                log.write(line);log.flush()
                try:record['messages'].put(json.loads(line))
                except json.JSONDecodeError:pass
            record['messages'].put(None)
        record['reader']=threading.Thread(target=read,daemon=True);record['reader'].start()
        process.stdin.write(json.dumps(credentials)+'\n');process.stdin.flush()
        ready=record['messages'].get(timeout=25)
        if not ready or ready.get('phase')!='ready':
            process.wait(timeout=10)
            raise RuntimeError('child_not_ready:'+label+':'+(str(ready) if ready else (control/'stderr.log').read_text()[-1800:]))
        record['ready']=ready
        if old_default:return record
        credential=release_api_bearer(manifest,'permanent' if permanent else 'test',credentials['api_token'])
        client=httpx.Client(base_url=ready['api'],headers={'Authorization':'Bearer '+credential},timeout=35)
        clients.append(client);record['client']=client
        return record
    def stop(host):
        process=host['process']
        if process.poll() is None:
            process.stdin.write('stop\n');process.stdin.flush();process.wait(timeout=15)
        assert process.returncode==0
    def post(host,path,**payload):
        payload={'audience':'owner_private','request_id':uuid.uuid4().hex,**payload}
        response=host['client'].post('/v1/coordination/'+path,json=payload)
        assert response.status_code==200,(path,response.status_code,response.text)
        return response.json()
    def await_file(path):
        end=time.monotonic()+15
        while not path.exists() and time.monotonic()<end:time.sleep(.02)
        assert path.exists(),str(path)
    try:
        archive=out/'executed-source.zip'
        with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as zipped:
            for name in report['source_before']:zipped.write(PROJECT/name,name)
        report['source_archive_sha256']=hashlib.sha256(archive.read_bytes()).hexdigest()
        clone(a);clone(b)
        report['child_source_overrides']={}
        with zipfile.ZipFile(archive,'a',compression=zipfile.ZIP_DEFLATED) as zipped:
            for label,root in (('a',a),('b',b)):
                override={name:sha for name,sha in source_inventory(root).items() if report['source_before'].get(name)!=sha}
                report['child_source_overrides'][label]=override
                for name in override:zipped.write(root/name,'overrides/'+label+'/'+name)
            zipped.write(a/'assets/release_fixture.wav','fixtures/release_fixture.wav')
        report['source_archive_sha256']=hashlib.sha256(archive.read_bytes()).hexdigest()
        state=a/'data/coordination/active'
        store=Store(state)
        seed=Core(store,draft=lambda *_:AssistantResponse(display_text='Fixture baseline.',delivery_mode='text_only'),
                  runner=PracticeRunner(store,propose=lambda *_:{},check=lambda *_:{}))
        seed.personal_control('unfinished','memory',{'op':'remember','kind':'commitment','subject_id':'owner',
            'topic':'fixture','text':'Finish the fictional release checklist.'})
        run=seed.control('fixture-practice','practice',exercise_id='retry_budget',maximum_attempts=3,budget_seconds=600)['activity']['worker_run']
        seed.control('pause-fixture','pause',run_id=run);seed.runner.run(run)
        checkpoint=seed.runner.artifacts.get(store.read('run',run)['checkpoint_ref'])
        checkpoint['tasks'][0]['attempt_count']=2
        checkpoint['tasks'][0]['attempts']=[{'kind':'authored_fixture_prior_attempt','passed':False}]*2
        reference=seed.runner.artifacts.put(checkpoint)
        store.update_run(run,reference,status='paused')
        identity=seed.personal_state()['identity_id'];seed.close()
        baseline=scratch/'baseline';export_snapshot(state,baseline)
        restore_snapshot(baseline,b/'data/coordination/active')
        common=scratch/'identity-locks'
        manifest=prepare(a,state,baseline,b,common)
        before_permanent=preflight(manifest)
        assert not before_permanent['ok'] and 'owner_activation_decision_missing' in before_permanent['blockers']
        report['permanent_preflight']=before_permanent
        second_manifest=prepare(b,b/'data/coordination/active',baseline,a,common)
        old_default=start(a,manifest,'old-default-owner',old_default=True)
        assert old_default['ready']['core_source']==str(a/'src/gamma/coordination/core.py')
        original_b=rows(b/'data/coordination/active','brain')['state']
        try:start(b,second_manifest,'blocked-by-old-default')
        except RuntimeError:pass
        else:raise AssertionError('release_ignored_old_default_owner')
        assert rows(b/'data/coordination/active','brain')['state']==original_b
        from gamma.coordination.store import process_lock
        with process_lock(common/(identity+'.lock')):pass
        stop(old_default)
        first=start(a,manifest,'first')
        assert httpx.get(first['ready']['api']+'/v1/coordination/private-release',
            headers={'Authorization':'Bearer '+first['credentials']['api_token']},timeout=10).status_code==401
        try:start(b,second_manifest,'old-default-blocked-by-release',old_default=True)
        except RuntimeError:pass
        else:raise AssertionError('old_default_ignored_release_bridge')
        with httpx.Client(base_url=first['ready']['dashboard'],timeout=10) as dashboard:
            denied=dashboard.get('/api/coordination/status?audience=owner_private');assert denied.status_code==401
            login=dashboard.post('/login',data={'username':'private-release-owner','password':first['credentials']['password']},follow_redirects=False)
            assert login.status_code==303
            assert dashboard.get('/dashboard/home').status_code==200
            assert dashboard.get('/api/coordination/status?audience=owner_private').status_code==200
        before_b=rows(b/'data/coordination/active','brain')['state']
        try:start(b,second_manifest,'concurrent')
        except RuntimeError:pass
        else:raise AssertionError('same_identity_coowned')
        assert rows(b/'data/coordination/active','brain')['state']==before_b
        report['steps'].append({'sole_owner_across_distinct_checkouts':True,'dashboard_actual_factory_auth':True,
            'manifest_primary_local_routing_rejected_before_claim':first['ready']['routing_rejected_before_owner_claim'],
            'old_default_Core_from_distinct_checkout_blocks_release':True,'release_blocks_old_default_Core_in_reader_checkout':True,
            'partial_compatibility_lease_failure_did_not_claim_owner_or_leak_common_lease':True})
        payload={'request_id':'post-handover','text':'Fictional experience after proposed handover.','session_id':'release-proof'}
        post(first,'input',**payload);calls=json.loads((first['control']/'calls.json').read_text())
        post(first,'input',**payload)
        assert json.loads((first['control']/'calls.json').read_text())==calls
        assert rows(state,'turn')['post-handover']['status']=='completed'
        device={'device_id':'release-fixture-phone','device_secret':secrets.token_urlsafe(32)}
        post(first,'phone/register',**device,label='Controlled HTTP fixture')
        connected=post(first,'phone/connect',**device)
        phone={**device,'epoch':connected['device']['epoch']}
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(post,first,'input',request_id='held-stale',text='hold-stale',session_id='release-proof')
            await_file(first['control']/'hold-stale.started')
            post(first,'control',action='sleep')
            (first['control']/'hold-stale.release').touch()
            withheld=pending.result(timeout=15)
            assert 'Controlled fixture reply: hold-stale' not in json.dumps(withheld)
            post(first,'control',action='wake')
            uncertain=pool.submit(post,first,'phone/input',**phone,request_id='crash-uncertain',text='hold-crash',session_id='release-proof')
            await_file(first['control']/'hold-crash.started')
            old=rows(state,'brain')['state'];old_token=(old['generation'],old['owner'])
            first['process'].kill();first['process'].wait(timeout=10)
            try:uncertain.result(timeout=10)
            except Exception:pass
            else:raise AssertionError('crashed_HTTP_unexpectedly_confirmed')
        reopened=start(a,manifest,'reopened')
        new=rows(state,'brain')['state'];assert new['generation']==old['generation']+1
        inspected=Store(state)
        try:
            with inspected.transaction(old_token) as db:inspected.put(db,'turn','stale-write',{'status':'completed'})
        except OwnershipLost:pass
        else:raise AssertionError('old_generation_wrote')
        reconnected=post(reopened,'phone/connect',**device)
        lookup=post(reopened,'phone/result',**device,epoch=reconnected['device']['epoch'],input_request_id='crash-uncertain')
        assert lookup['automatic_replay'] is False and lookup['status']=='recoverable'
        assert not (reopened['control']/'calls.json').exists()
        assert rows(state,'turn')['crash-uncertain']['status']=='recoverable'
        report['steps'].append({'post_handover_exchange_retained':True,'duplicate_input_not_replayed':True,
            'late_response_withheld_after_sleep':True,'actual_pending_HTTP_crash':True,
            'crash_generation_incremented_once':True,'old_token_write_rejected':True,'uncertain_transport_status_lookup_did_not_replay':True})
        stop(reopened)
        target=b/'data/coordination/reversed'
        reversal=compatible_reversal(manifest,target)
        assert rows(target,'turn')['post-handover']['status']=='completed'
        commitments=rows(target,'personal_record');assert any(x.get('kind')=='commitment' and x.get('status')=='active' for x in commitments.values())
        run_after=rows(target,'run')[run];assert run_after['maximum_attempts']==3 and run_after['status']=='paused'
        checkpoint_after=json.loads((target/'artifacts'/(run_after['checkpoint_ref']+'.json')).read_text())
        assert checkpoint_after['tasks'][0]['attempt_count']==2
        latest_bundle=state.parent/('reversal-'+manifest['release_id'])
        final_manifest=prepare(b,target,latest_bundle,a,common)
        returned=start(b,final_manifest,'compatible-reader')
        (returned['control']/'hold-crash.release').touch()
        post(returned,'input',request_id='after-reversal',text='Fictional experience after compatible reversal.',session_id='release-proof')
        stop(returned)
        assert rows(target,'turn')['after-reversal']['status']=='completed'
        legacy=scratch/'legacy-reader';legacy.mkdir()
        try:project_legacy(target,legacy)
        except ValueError as exc:assert 'current_reader' in str(exc)
        else:raise AssertionError('P1_projection_accepted_current_work')
        finite=start(b,final_manifest,'finite-cli',cli=True)
        assert finite['client'].get('/v1/coordination/private-release').status_code==200
        finite['process'].wait(timeout=15);assert finite['process'].returncode==0
        report['steps'].append({**reversal,'unfinished_commitment_retained':True,
            'checkpoint_artifacts_and_consumed_limits_retained':True,'current_reader_resumed_actual_HTTP':True,
            'legacy_P1_rejected_without_projection':True,'actual_finite_CLI_auto_closed':True})
        approved_fixture=json.loads(json.dumps(final_manifest));approved_fixture['status']='owner_approved'
        permanent=start(b,approved_fixture,'permanent-background',permanent=True)
        await_file(permanent['control']/'background-counts.json')
        until=time.monotonic()+5
        while time.monotonic()<until:
            counts=json.loads((permanent['control']/'background-counts.json').read_text())
            if all(value>=1 for value in counts.values()):break
            time.sleep(.05)
        assert all(value>=1 for value in counts.values())
        scope=permanent['client'].get('/v1/coordination/private-release').json()
        assert scope['purpose']=='permanent'
        post(permanent,'input',request_id='permanent-controlled',text='Permanent background fixture.',session_id='release-proof')
        stop(permanent)
        report['steps'].append({'actual_permanent_API_factory_purpose':True,'default_background_loops_executed':counts,
            'drain_and_provider_boundaries_controlled_before_lifespan':True,'real_services_or_normal_ports_changed':False})
        assert not provider_requests,provider_requests
        report['provider_tripwire_requests']=provider_requests
        report['complete']=True
    finally:
        for host in hosts:
            process=host['process']
            if process.poll() is None:
                try:process.stdin.write('stop\n');process.stdin.flush();process.wait(timeout=10)
                except Exception:process.kill();process.wait(timeout=10)
            host['reader'].join(timeout=2)
            process.stdin.close();process.stdout.close()
            host['errors'].close();host['log'].close()
        for client in clients:client.close()
        tripwire.shutdown();tripwire.server_close();tripwire_thread.join(timeout=2)
        report['owned_pids']=[x['process'].pid for x in hosts]
        report['owned_hosts_absent']=bool(hosts) and all(x['process'].poll() is not None for x in hosts)
        report['source_stable']=source_inventory(PROJECT)==report['source_before']
        report['elapsed_seconds']=round(time.monotonic()-started,3)
        report['limits']=['Controlled providers, not actual model/voice or human acceptance.',
            'Only cooperating release readers share the lock; no noncooperating legacy controller was stopped.',
            'Authored fixture prior attempts test retained limits, not measured real skill.',
            'Current-reader copied-state reversal, not legacy-code production rollback.']
        report['limits'].append('Core may resume uncommitted recoverable computation. Transport status lookup does not request replay or release old copied output.')
        save()
    assert report['source_stable'] and report['owned_hosts_absent']
    print(json.dumps({'report':str(out/'report.json'),'complete':report['complete'],'elapsed_seconds':report['elapsed_seconds']}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--child',action='store_true');parser.add_argument('--manifest',type=Path)
    parser.add_argument('--control',type=Path);parser.add_argument('--output',type=Path)
    parser.add_argument('--permanent-fixture',action='store_true')
    parser.add_argument('--old-default-fixture',action='store_true')
    args=parser.parse_args()
    if args.child:child(args)
    else:main(args)
