"""Actual Core/browser finalized chunk proof with declared synthetic providers."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import threading
import uuid

from verify_coordination_slice import server
from verify_private_presence_core import fixture_wave


def main():
    project=Path(__file__).resolve().parents[1]
    if os.name!='posix' or project.name in {'gamma-main','gamma-modular-phase1'}:
        raise RuntimeError('use the isolated Gamma candidate')
    root=project/'data/coordination/p5'/('presence-chunks-'+uuid.uuid4().hex)
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
    draft=lambda *_:AssistantResponse(display_text=first+' '+second,delivery_mode='text_only')
    release,held=threading.Event(),threading.Event()
    lock=threading.Lock();calls=[]
    audio=fixture_wave()
    def synthesize(text,emotion):
        with lock:
            calls.append({'text':text,'emotion':emotion});number=len(calls)
        if number==2:
            held.set()
            if not release.wait(35):raise TimeoutError('bounded synthesis fixture was not released')
        if number<=2:return audio,'audio/wav'
        # Keep succeeding chunks short without changing their speech text.
        import io,wave
        output=io.BytesIO()
        with wave.open(output,'wb') as stream:
            stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(16000);stream.writeframes(b'\0\0'*3200)
        return output.getvalue(),'audio/wav'
    app=build_app(root/'state',bearer_token=token,draft_factory=lambda:draft,
        runner_factory=lambda store:PracticeRunner(store,propose=lambda *_:{},check=lambda *_:{}),run_background=False,
        interaction_factory=lambda core:PrivateInteraction(core,synthesize=synthesize))
    relevant=['src/gamma/coordination/interaction.py','src/gamma/coordination/audio_policy.py',
        'src/gamma/coordination/interaction_routes.py','src/gamma/coordination/core.py',
        'src/gamma/dashboard/static/private_presence.js','src/gamma/dashboard/static/presence_queue.js',
        'scripts/private_presence_chunk_probe.cjs','scripts/verify_private_presence_chunks.py']
    hashes={p:hashlib.sha256((project/p).read_bytes()).hexdigest() for p in relevant}
    report={'complete':False,'source_files':hashes,'model_calls':0,'activation':False,
            'scope':'actual Core, HTTP/auth and browser Audio with finalized synthetic draft/held PCM callback'}
    try:
        with server(app) as api:
            settings.shana_port=int(api.rsplit(':',1)[1])
            dashboard=dashboard_app(ShanaApiClient(base_url=api))
            @dashboard.get('/api/presence-proof/state')
            def proof_state():return {'second_held':held.is_set(),'released':release.is_set(),'synthesis_calls':len(calls)}
            @dashboard.post('/api/presence-proof/release')
            def proof_release():release.set();return {'released':True}
            with server(dashboard) as url:
                result=subprocess.run(['node',str(project/'scripts/private_presence_chunk_probe.cjs')],
                    input=json.dumps({'url':url,'username':'isolated-owner','password':password,'second_sentence':second,
                                      'screenshot':str(root/'presence-chunks.png')}),capture_output=True,text=True,timeout=75)
                (root/'browser.log').write_text(result.stderr,encoding='utf-8')
                assert result.returncode==0,result.stderr
                report['browser']=json.loads(result.stdout);assert report['browser']['complete']
        report['server_threads_stopped']=True
        report['source_unchanged']=hashes=={p:hashlib.sha256((project/p).read_bytes()).hexdigest() for p in relevant}
        assert report['source_unchanged']
        report['synthesis_calls']=calls
        assert len(calls)==4 and [c['text'] for c in calls]==[first,second,first,second]
        report['complete']=True
    finally:
        release.set()
        (root/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(root/'report.json'),'complete':report['complete']}))


if __name__=='__main__':main()
