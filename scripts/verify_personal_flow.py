"""Opt-in P2 real local-model/API/browser/restart journey on fictional state only."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import urllib.request
import uuid

from verify_coordination_foundation import until,publish,child


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',required=True)
    parser.add_argument('--endpoint',required=True)
    parser.add_argument('--child-state',type=Path)
    args=parser.parse_args()
    project=Path(__file__).resolve().parents[1]
    if os.name!='posix' or project.name in {'gamma-main','gamma-modular-phase1'}:
        raise RuntimeError('isolated Gamma checkout required')
    if args.child_state:
        child(project,args.child_state);return
    with urllib.request.urlopen(args.endpoint.rstrip('/')+'/api/ps',timeout=5) as response:
        if json.load(response).get('models'):raise RuntimeError('selected endpoint occupied; no inference sent')
    import httpx
    state=project/'data/coordination/p2'/('connected-'+uuid.uuid4().hex)
    state.mkdir(mode=0o700)
    token,password=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
    env={**os.environ,'PYTHONPATH':str(project/'src'),'SHANA_PROJECT_ROOT':str(project),
        'GAMMA_ISOLATED_COORDINATION':'1','GAMMA_COORDINATION_STATE_DIR':str(state),
        'SHANA_API_AUTH_ENABLED':'true','SHANA_API_BEARER_TOKEN':token,
        'SHANA_DASHBOARD_AUTH_ENABLED':'true','SHANA_DASHBOARD_AUTH_USERNAME':'isolated-owner',
        'SHANA_DASHBOARD_AUTH_PASSWORD':password,'SHANA_DASHBOARD_SESSION_SECRET':secrets.token_urlsafe(32),
        'SHANA_DASHBOARD_COOKIE_SECURE':'false','SHANA_LLM_PROVIDER':'local','SHANA_LLM_ROUTER_ENABLED':'false',
        'SHANA_LOCAL_LLM_ENDPOINT':args.endpoint,'SHANA_LOCAL_LLM_MODEL':args.model,'SHANA_LOCAL_LLM_ENABLE_ROUTING':'false',
        'SHANA_LOCAL_LLM_KEEP_ALIVE':'0','SHANA_LOCAL_LLM_TIMEOUT_SECONDS':'90','GAMMA_PRACTICE_MODEL':args.model,
        'SHANA_DATABASE_URL':'sqlite:///'+str(state/'conversation.sqlite3')}
    report={'scope':'fictional_isolated_owner_private','model':args.model,'checks':{},'interactions':[],
            'production_activation':False,'voice_verified':False}
    process=client=log=None
    def record(name):
        report['checks'][name]=True;publish(state/'report.json',report);print(json.dumps({'step':name}),flush=True)
    def start():
        nonlocal process,client,log
        (state/'ready.json').unlink(missing_ok=True)
        log=(state/('host-'+uuid.uuid4().hex+'.log')).open('w')
        process=subprocess.Popen([sys.executable,__file__,'--model',args.model,'--endpoint',args.endpoint,
            '--child-state',str(state)],cwd=project,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log)
        ready=until(lambda:json.loads((state/'ready.json').read_text()) if (state/'ready.json').exists() else None,
                    lambda v:v is not None,30)
        client=httpx.Client(base_url=ready['dashboard'],timeout=180)
        assert client.post('/api/coordination/personal',json={}).status_code==401
        assert client.post('/login',data={'username':'isolated-owner','password':password}).status_code in {200,303}
        assert client.get('/api/coordination/personal?audience=public').status_code==422
        return ready
    def stop(crash=False):
        nonlocal process,client,log
        if client:client.close();client=None
        if process and process.poll() is None:
            process.kill() if crash else process.terminate()
            process.wait(timeout=20)
        if log:log.close();log=None
    def personal():
        response=client.get('/api/coordination/personal?audience=owner_private');response.raise_for_status();return response.json()
    def control(action,**values):
        response=client.post('/api/coordination/control',json={'request_id':uuid.uuid4().hex,'audience':'owner_private',
            'action':action,**values});response.raise_for_status();return response.json()
    def chat(text,session='fixture-a',request=None):
        rid=request or uuid.uuid4().hex;began=time.monotonic()
        response=client.post('/api/monitor/input',json={'request_id':rid,'audience':'owner_private','input_mode':'private_text',
            'text':text,'session_id':session});response.raise_for_status()
        result=response.json()['result']['assistant_response']
        assert not result['reason_code'].startswith('contract_fallback'),result
        assert not result.get('speech_text') and not result.get('audio_path')
        report['interactions'].append({'id':rid,'session':session,'input':text,'response':result,
            'elapsed_seconds':round(time.monotonic()-began,2)})
        publish(state/'report.json',report)
        return result
    try:
        ready=start();record('authenticated_private_routes')
        first=chat('For the workshop next week, I prefer jasmine tea. I decided to repair my bicycle before buying another. I still need to call Mira about the lantern.')
        records=personal()['records']
        assert len([r for r in records if r['status']=='active'])>=2, 'Useful automatic personal changes missing'
        record('automatic_useful_records_without_save_command')
        remembered=chat('What drink do I prefer for the workshop, what bicycle decision did I make, and what remains unfinished?',session='fixture-b')
        assert all(word in remembered['display_text'].lower() for word in ('jasmine','bicycle','mira')),remembered
        record('cross_session_decision_preference_unfinished_recall')
        chat('Correction: for that workshop I now prefer mint tea, not jasmine. Please correct the earlier preference.',session='fixture-b')
        corrected=[r for r in personal()['records'] if r['status']=='active' and 'mint' in r['text'].lower()]
        assert corrected and corrected[0]['revision']>=2, 'Correction did not retain a stable record revision'
        record('conversational_correction_retains_revision')
        identity=personal()['identity_id'];stop(crash=True);ready=start()
        assert personal()['identity_id']==identity
        remembered=chat('What is my current workshop tea preference? Was there an earlier report?',session='fixture-c')
        assert 'mint' in remembered['display_text'].lower() and 'jasmine' in remembered['display_text'].lower()
        record('process_crash_restart_current_and_prior_report')
        browser=subprocess.run(['node',str(project/'scripts/personal_browser_probe.cjs')],input=json.dumps({
            'url':ready['dashboard'],'username':'isolated-owner','password':password,'screenshot':str(state/'personal-private-hidden.png')}),
            text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=100)
        assert browser.returncode==0,browser.stderr[-1000:]
        report['browser']=json.loads(browser.stdout);assert report['browser']['ok'];record('real_browser_owner_edit_correction_forget_account_privacy')
        familiar=chat('What do you know about Fictional Mira? Does familiarity mean you agree with her risky shortcuts or grant her authority?',session='relationship-fixture')
        words=familiar['display_text'].lower()
        assert len([p for p in personal()['people'] if p['name']=='Fictional Mira'])==1
        assert 'familiar' in words and any(w in words for w in ('shortcut','judgment','judgement','disagree'))
        assert any(w in words for w in ('authority','permission','access')) and any(w in words for w in ('not','no','doesn\'t','don\'t'))
        assert all(account['grants_authority'] is False for account in personal()['accounts'])
        record('relationship_familiarity_opinion_authority_separate')
        control('sleep')
        queued=chat('When you wake, the fictional lantern meeting moved to Thursday.',session='sleep-fixture',request='sleep-once')
        assert queued['reason_code']=='asleep'
        chat('When you wake, the fictional lantern meeting moved to Thursday.',session='sleep-fixture',request='sleep-once')
        assert len(personal()['queued_messages'])==1
        stop(crash=True);ready=start();assert len(personal()['queued_messages'])==1
        control('wake')
        from gamma.coordination.store import Store
        until(lambda:Store(state).read('turn','sleep-once'),lambda t:t['status']=='completed',150)
        after=chat('Which day is the fictional lantern meeting now?',session='fixture-d')
        assert 'thursday' in after['display_text'].lower();record('sleep_duplicate_restart_wake_communication_once')
        response=client.post('/api/coordination/personal',json={'request_id':'partial-retention','audience':'owner_private',
            'action':'policy','values':{'text_days':0}});response.raise_for_status()
        partial=chat('What bicycle decision do you have recorded? Can you still quote the exact original conversation?',session='partial-fixture')
        assert 'bicycle' in partial['display_text'].lower()
        assert any(word in partial['display_text'].lower() for word in ('exact','quote','original','verbatim'))
        record('partial_recollection_source_retention')
        record('complete_connected_p2_journey')
    except BaseException as exc:
        report['failure']={'type':type(exc).__name__,'message':str(exc)[:1500]};raise
    finally:
        stop();publish(state/'report.json',report);print(json.dumps({'report':str(state/'report.json')}),flush=True)


if __name__=='__main__':main()
