"""Actual browser capture path with synthetic stream and held provider callbacks."""
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import threading
import uuid
import wave

from verify_coordination_slice import server
from verify_private_presence_core import fixture_wave


def main():
    project=Path(__file__).resolve().parents[1]
    if os.name!='posix' or project.name in {'gamma-main','gamma-modular-phase1'}:
        raise RuntimeError('use the isolated Gamma candidate')
    root=project/'data/coordination/p5'/('presence-capture-'+uuid.uuid4().hex)
    root.mkdir(parents=True,mode=0o700)
    token,password=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
    os.environ.update({'SHANA_PROJECT_ROOT':str(project),'GAMMA_ISOLATED_COORDINATION':'1',
        'SHANA_API_AUTH_ENABLED':'true','SHANA_API_BEARER_TOKEN':token,
        'SHANA_DASHBOARD_AUTH_ENABLED':'true','SHANA_DASHBOARD_AUTH_USERNAME':'isolated-owner',
        'SHANA_DASHBOARD_AUTH_PASSWORD':password,'SHANA_DASHBOARD_SESSION_SECRET':secrets.token_urlsafe(32),
        'SHANA_DASHBOARD_COOKIE_SECURE':'false','SHANA_LLM_PROVIDER':'mock',
        'SHANA_LLM_ROUTER_ENABLED':'false','SHANA_DATABASE_URL':'sqlite:///'+str(root/'unused.sqlite3')})
    from gamma.config import settings
    from gamma.coordination.app import build_app
    from gamma.coordination.dashboard import build_app as dashboard_app
    from gamma.coordination.interaction import PrivateInteraction
    from gamma.coding.durable_practice import PracticeRunner
    from gamma.dashboard.shana_client import ShanaApiClient
    from gamma.schemas.response import AssistantResponse
    import gamma
    assert Path(gamma.__file__).resolve().is_relative_to(project/'src/gamma')
    first='First complete sentence carries the useful point.'
    second='Second complete sentence adds a little detail.'
    release,held=threading.Event(),threading.Event()
    privacy_release,privacy_held=threading.Event(),threading.Event()
    lock=threading.Lock();calls=[];transcriptions=[]
    def transcribe(raw):
        with lock:
            transcriptions.append({'sha256':hashlib.sha256(raw).hexdigest()});number=len(transcriptions)
        return 'Please consider a short break.' if number==1 else 'Shana, stop and answer this new question.'
    def synthesize(text,emotion):
        with lock:
            calls.append({'text':text,'emotion':emotion});number=len(calls)
        if number==2:
            held.set()
            if not release.wait(35):raise TimeoutError('bounded capture fixture was not released')
        if number==5:
            privacy_held.set()
            if not privacy_release.wait(20):raise TimeoutError('bounded privacy fixture was not released')
        if number<=2:return fixture_wave(),'audio/wav'
        content=io.BytesIO()
        with wave.open(content,'wb') as output:
            output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000);output.writeframes(b'\0\0'*3200)
        return content.getvalue(),'audio/wav'
    draft=lambda *_:AssistantResponse(display_text=first+' '+second,delivery_mode='text_only')
    app=build_app(root/'state',bearer_token=token,draft_factory=lambda:draft,
        runner_factory=lambda store:PracticeRunner(store,propose=lambda *_:{},check=lambda *_:{}),run_background=False,
        interaction_factory=lambda core:PrivateInteraction(core,transcribe=transcribe,synthesize=synthesize))
    relevant=['src/gamma/coordination/interaction.py','src/gamma/coordination/interaction_routes.py',
        'src/gamma/coordination/core.py','src/gamma/dashboard/static/private_presence.js',
        'src/gamma/dashboard/static/presence_queue.js','scripts/private_presence_capture_probe.cjs',
        'scripts/verify_private_presence_capture.py']
    hashes={name:hashlib.sha256((project/name).read_bytes()).hexdigest() for name in relevant}
    report={'complete':False,'scope':'actual browser capture/VAD/encoder/HTTP/Core; synthetic microphone/transcribe/draft/PCM',
        'source_files':hashes,'model_calls':0,'activation':False}
    try:
        with server(app) as api:
            settings.shana_port=int(api.rsplit(':',1)[1])
            dashboard=dashboard_app(ShanaApiClient(base_url=api))
            @dashboard.get('/api/capture-proof/state')
            def proof_state():
                core=app.state.core
                with core.store.transaction(core.token) as db:
                    completed=[turn['id'] for turn in core.store.list(db,'turn',None) if turn.get('status')=='completed']
                    consumers=sum(1 for value in core.store.list(db,'interaction_consumer',None) if value.get('connected'))
                    receipts=[{'id':value['id'],'status':(value.get('receipt') or {}).get('status')}
                              for value in core.store.list(db,'interaction_request',None)]
                    utterances=[value['utterance_id'] for value in core.store.list(db,'interaction_output',None)]
                return {'second_held':held.is_set(),'privacy_held':privacy_held.is_set(),'transcriptions':list(transcriptions),
                        'completed_turns':completed,'synthesis_calls':len(calls),'connected_consumers':consumers,
                        'request_receipts':receipts,'output_utterances':utterances}
            @dashboard.post('/api/capture-proof/release')
            def proof_release():release.set();return {'released':True}
            @dashboard.post('/api/capture-proof/release-privacy')
            def proof_privacy_release():privacy_release.set();return {'released':True}
            with server(dashboard) as url:
                result=subprocess.run(['node',str(project/'scripts/private_presence_capture_probe.cjs')],
                    input=json.dumps({'url':url,'username':'isolated-owner','password':password}),capture_output=True,text=True,timeout=75)
                (root/'browser.log').write_text(result.stderr,encoding='utf-8')
                assert result.returncode==0,result.stderr
                report['browser']=json.loads(result.stdout);assert report['browser']['complete']
        report['server_threads_stopped']=True
        report['source_unchanged']=hashes=={name:hashlib.sha256((project/name).read_bytes()).hexdigest() for name in relevant}
        assert report['source_unchanged']
        report['transcriptions']=transcriptions;report['synthesis_calls']=calls
        assert len(transcriptions)==3 and len(calls)==5
        report['complete']=True
    finally:
        release.set();privacy_release.set()
        (root/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(root/'report.json'),'complete':report['complete']}))


if __name__=='__main__':main()
