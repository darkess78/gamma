"""Prove private presentation against actual Core with declared synthetic draft/audio.

This finite isolated check makes no model, microphone or human listening claim.
"""
import hashlib
import io
import json
import math
import os
from pathlib import Path
import secrets
import subprocess
import sys
import uuid
import wave

from verify_coordination_slice import server


def fixture_wave():
    content=io.BytesIO()
    with wave.open(content,'wb') as output:
        output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000)
        output.writeframes(b''.join(int(math.sin(i*440*math.pi/8000)*1200).to_bytes(2,'little',signed=True)
                                   for i in range(48000)))
    return content.getvalue()


def main():
    project=Path(__file__).resolve().parents[1]
    if os.name!='posix' or project.name in {'gamma-main','gamma-modular-phase1'}:
        raise RuntimeError('use the isolated Gamma candidate')
    root=project/'data/coordination/p5'/('presence-core-'+uuid.uuid4().hex)
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
    relevant=['src/gamma/coordination/interaction.py','src/gamma/coordination/interaction_routes.py',
        'src/gamma/coordination/core.py','src/gamma/coordination/app.py','src/gamma/coordination/dashboard.py',
        'src/gamma/coordination/presence.html','src/gamma/dashboard/static/private_presence.js',
        'src/gamma/dashboard/static/presence_queue.js','scripts/private_presence_core_probe.cjs',
        'scripts/verify_private_presence_core.py']
    hashes={p:hashlib.sha256((project/p).read_bytes()).hexdigest() for p in relevant}
    draft=lambda *_:AssistantResponse(display_text='A synthetic private reply for this transport test.',delivery_mode='text_only')
    audio=fixture_wave()
    app=build_app(root/'state',bearer_token=token,draft_factory=lambda:draft,
        runner_factory=lambda store:PracticeRunner(store,propose=lambda *_:{},check=lambda *_:{}),run_background=False,
        interaction_factory=lambda core:PrivateInteraction(core,synthesize=lambda *_:(audio,'audio/wav')))
    report={'complete':False,'scope':'actual Core, authenticated HTTP proxies, browser and PCM lifecycle; synthetic draft/synthesis',
            'source_files':hashes,'source_resolution':str(Path(gamma.__file__).resolve()),'activation':False,'model_calls':0}
    try:
        with server(app) as api:
            settings.shana_port=int(api.rsplit(':',1)[1])
            with server(dashboard_app(ShanaApiClient(base_url=api))) as url:
                result=subprocess.run(['node',str(project/'scripts/private_presence_core_probe.cjs')],
                    input=json.dumps({'url':url,'username':'isolated-owner','password':password,'screenshot':str(root/'presence-core.png')}),
                    capture_output=True,text=True,timeout=75)
                (root/'browser.log').write_text(result.stderr,encoding='utf-8')
                assert result.returncode==0,result.stderr
                report['browser']=json.loads(result.stdout)
                assert report['browser']['complete']
        report['server_threads_stopped']=True
        report['source_unchanged']=hashes=={p:hashlib.sha256((project/p).read_bytes()).hexdigest() for p in relevant}
        assert report['source_unchanged']
        report['complete']=True
    finally:
        (root/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(root/'report.json'),'complete':report['complete']}))


if __name__=='__main__':main()
