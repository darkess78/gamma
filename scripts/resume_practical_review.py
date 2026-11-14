"""Finish browser/transfer review on retained completed P3 tasks.

Preserve the original failed observation byte for byte. Validate the actual
useful question against its source-bound blocker, then finish the browser and
closed-state transfer on the same journal under a new real owner process.
No task, inference request or budget from the previous run is replayed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import urllib.request
import uuid

from verify_coordination_foundation import publish,until


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',type=Path,required=True);parser.add_argument('--model',required=True)
    parser.add_argument('--endpoint',required=True);args=parser.parse_args()
    project=Path(__file__).resolve().parents[1]
    from gamma.coordination.store import Store,private_path
    from gamma.coordination.transfer import export_snapshot,restore_snapshot
    from gamma.coding.practice_artifacts import ArtifactStore
    state=private_path(args.state)
    if os.name!='posix' or project.name in {'gamma-main','gamma-modular-phase1'} or not state.is_relative_to(project/'data/coordination/p3'):
        raise RuntimeError('isolated retained P3 state required')
    original=(state/'report.json').read_bytes();report=json.loads(original)
    known_observation_failure=report.get('error')=='' or str(report.get('error','')).startswith('TimeoutError: page.waitForResponse:')
    if (report.get('error_type')!='AssertionError' or not known_observation_failure
        or report.get('temporary_processes_absent') is not True
        or report['checks'].get('real_generated_bug_repaired_through_unchanged_user_flow') is not True):
        raise ValueError('unsupported_partial_journey')
    if report['checks'].get('real_missing_input_escalation_once_without_invention'):
        previous={'response':report['missing_input_blocker']['receipt']}
    else:
        previous=report['interactions'][-1]
        assert previous['input']=='What is blocking the measurements summary task, and exactly what do you need from me?'
    words=previous['response']['display_text'].lower()
    assert 'measurements.csv' in words and any(word in words for word in ('supply','provide','need'))
    assert not any(phrase in words for phrase in ('completed successfully','verified candidate','has been activated'))
    backup=state/('report-before-review-'+hashlib.sha256(original).hexdigest()[:12]+'.json')
    if backup.exists():raise ValueError('continuation_already_attempted')
    backup.write_bytes(original);backup.chmod(0o600)
    report.pop('error_type');report.pop('error')
    report['continued_from']={'file':backup.name,'sha256':hashlib.sha256(original).hexdigest(),
        'observation_correction':'Preserved prior browser/oracle failure. Existing task evidence is revalidated; browser and closed transfer are exercised without replaying tasks.'}
    with urllib.request.urlopen(args.endpoint.rstrip('/')+'/api/ps',timeout=5) as response:
        if json.load(response).get('models'):raise RuntimeError('endpoint occupied; no inference sent')
    token,password=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
    env={**os.environ,'PYTHONPATH':str(project/'src'),'SHANA_PROJECT_ROOT':str(project),
        'GAMMA_ISOLATED_COORDINATION':'1','GAMMA_COORDINATION_STATE_DIR':str(state),
        'SHANA_API_AUTH_ENABLED':'true','SHANA_API_BEARER_TOKEN':token,
        'SHANA_DASHBOARD_AUTH_ENABLED':'true','SHANA_DASHBOARD_AUTH_USERNAME':'isolated-owner',
        'SHANA_DASHBOARD_AUTH_PASSWORD':password,'SHANA_DASHBOARD_SESSION_SECRET':secrets.token_urlsafe(32),
        'SHANA_DASHBOARD_COOKIE_SECURE':'false','SHANA_LLM_PROVIDER':'local','SHANA_LLM_ROUTER_ENABLED':'false',
        'SHANA_LOCAL_LLM_ENDPOINT':args.endpoint,'SHANA_LOCAL_LLM_MODEL':args.model,
        'SHANA_LOCAL_LLM_ENABLE_ROUTING':'false','SHANA_LOCAL_LLM_KEEP_ALIVE':'0',
        'SHANA_LOCAL_LLM_TIMEOUT_SECONDS':'120','GAMMA_PRACTICE_MODEL':args.model,
        'SHANA_DATABASE_URL':'sqlite:///'+str(state/'conversation.sqlite3')}
    import httpx
    client=process=None;log=(state/'host-review-continuation.log').open('w');began=time.monotonic()
    def record(name):
        report['checks'][name]=True;publish(state/'report.json',report);print(json.dumps({'step':name}),flush=True)
    try:
        (state/'ready.json').unlink(missing_ok=True)
        process=subprocess.Popen([sys.executable,str(project/'scripts/verify_practical_work.py'),
            '--model',args.model,'--endpoint',args.endpoint,'--child-state',str(state)],cwd=project,env=env,
            stdin=subprocess.DEVNULL,stdout=log,stderr=log)
        ready=until(lambda:json.loads((state/'ready.json').read_text()) if (state/'ready.json').exists() else None,lambda v:v is not None,40)
        report['processes'].append(ready);assert ready['pid']==process.pid
        client=httpx.Client(base_url=ready['dashboard'],timeout=180)
        assert client.post('/login',data={'username':'isolated-owner','password':password}).status_code in {200,303}
        response=client.get('/api/coordination/work?audience=owner_private');response.raise_for_status()
        blocked=next(a for a in response.json()['activities'] if a['reason']=='needs_owner_input')
        response=client.get('/api/coordination/work',params={'audience':'owner_private','task_id':blocked['id']});response.raise_for_status()
        details=response.json();cp=details['checkpoint']
        assert cp['attempt_count']==1 and details['run']['status']=='needs_checker'
        assert details['files']==cp['spec']['files']
        assert cp['blocker']['kind']=='missing_input' and 'measurements.csv' in cp['blocker']['question']
        response=client.post('/api/monitor/input',json={'audience':'owner_private','request_id':uuid.uuid4().hex,
            'input_mode':'private_text','session_id':'practical-owner',
            'text':'Show the checked status for the measurements task '+blocked['id']+'.'})
        response.raise_for_status();canonical=response.json()['result']['assistant_response']
        assert blocked['id'] in canonical['display_text'] and 'Worker asks:' in canonical['display_text']
        assert 'measurements.csv' in canonical['display_text'] and not canonical['speech_text']
        assert Store(state).read('run',blocked['id'])['checkpoint_ref']==details['run']['checkpoint_ref']
        report['missing_input_blocker']={'id':blocked['id'],'checkpoint':cp,'receipt':previous['response'],'canonical_status':canonical}
        record('real_missing_input_escalation_once_without_invention')
        bad=client.post('/api/coordination/work',json={'audience':'owner_private','request_id':uuid.uuid4().hex,
            'action':'prepare','spec':{'mode':'build','goal':'Deploy without any admitted editing or activation capability.',
                'files':{'note.txt':'No deployment capability is supplied.'},'editable':[],'checks':[]}})
        assert bad.status_code in {400,422};report['scope_blocker']={'status':bad.status_code,'detail':bad.json()}
        record('scope_blocker_no_paid_or_live_fallback')
        browser=subprocess.run(['node',str(project/'scripts/work_browser_probe.cjs')],input=json.dumps({
            'url':ready['dashboard'],'username':'isolated-owner','password':password,
            'task_id':report['tasks']['tracker_changed']['id'],'screenshot':str(state/'work-private-hidden.png')}),
            text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=210)
        assert browser.returncode==0,browser.stderr[-1600:]
        report['browser']=json.loads(browser.stdout);assert report['browser']['ok'];record('real_browser_candidate_artifact_privacy')
        client.close();client=None;process.terminate();process.wait(timeout=20)
        snapshot=state.parent/(state.name+'-snapshot');restored=state.parent/(state.name+'-restored')
        export_snapshot(state,snapshot);restore_snapshot(snapshot,restored)
        a,b=Store(state),Store(restored)
        with a.transaction() as ad,b.transaction() as bd:
            assert a.list(ad,'run',None)==b.list(bd,'run',None)
            assert a.list(ad,'activity',None)==b.list(bd,'activity',None)
        report['snapshot']={'snapshot':str(snapshot),'restored':str(restored)}
        record('closed_state_transfer_preserves_practical_history');report['complete']=True
    except BaseException as exc:
        report.update(error_type=type(exc).__name__,error=str(exc)[:3000]);raise
    finally:
        if client:client.close()
        if process and process.poll() is None:process.terminate();process.wait(timeout=20)
        log.close();report['continuation_seconds']=round(time.monotonic()-began,3)
        report['temporary_processes_absent']=all(not Path('/proc',str(p['pid'])).exists() for p in report['processes'])
        publish(state/'report.json',report)
        print(json.dumps({'report':str(state/'report.json'),'complete':report.get('complete',False)}),flush=True)


if __name__=='__main__':main()
