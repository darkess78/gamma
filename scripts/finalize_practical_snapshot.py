"""Finish the P3 transfer after closing a previously abrupt test-host exit."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

from verify_coordination_foundation import publish,until


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',type=Path,required=True);parser.add_argument('--model',required=True)
    parser.add_argument('--endpoint',required=True);args=parser.parse_args()
    project=Path(__file__).resolve().parents[1]
    from gamma.coordination.store import Store,private_path
    from gamma.coordination.transfer import export_snapshot,restore_snapshot
    state=private_path(args.state)
    if os.name!='posix' or project.name in {'gamma-main','gamma-modular-phase1'} or not state.is_relative_to(project/'data/coordination/p3'):
        raise ValueError('isolated_P3_state_required')
    raw=(state/'report.json').read_bytes();report=json.loads(raw)
    if (report.get('error_type')!='ValueError' or report.get('error')!='close_owner_before_transfer'
        or report.get('temporary_processes_absent') is not True
        or report['checks'].get('real_browser_candidate_artifact_privacy') is not True):
        raise ValueError('unsupported_transfer_continuation')
    backup=state/'report-at-abrupt-probe-exit.json'
    if backup.exists():raise ValueError('snapshot_continuation_already_attempted')
    backup.write_bytes(raw);backup.chmod(0o600)
    report.pop('error_type');report.pop('error')
    report['snapshot_continuation']={'file':backup.name,'sha256':hashlib.sha256(raw).hexdigest(),
        'fix':'Test host handles SIGTERM by closing app contexts and releasing owner. SIGKILL remains the actual crash fixture.'}
    env={**os.environ,'PYTHONPATH':str(project/'src'),'SHANA_PROJECT_ROOT':str(project),
        'GAMMA_ISOLATED_COORDINATION':'1','GAMMA_COORDINATION_STATE_DIR':str(state),
        'SHANA_API_AUTH_ENABLED':'true','SHANA_API_BEARER_TOKEN':secrets.token_urlsafe(32),
        'SHANA_DASHBOARD_AUTH_ENABLED':'true','SHANA_DASHBOARD_AUTH_USERNAME':'isolated-owner',
        'SHANA_DASHBOARD_AUTH_PASSWORD':secrets.token_urlsafe(32),'SHANA_DASHBOARD_SESSION_SECRET':secrets.token_urlsafe(32),
        'SHANA_DASHBOARD_COOKIE_SECURE':'false','SHANA_LLM_PROVIDER':'local','SHANA_LLM_ROUTER_ENABLED':'false',
        'SHANA_LOCAL_LLM_ENDPOINT':args.endpoint,'SHANA_LOCAL_LLM_MODEL':args.model,
        'SHANA_LOCAL_LLM_ENABLE_ROUTING':'false','SHANA_LOCAL_LLM_KEEP_ALIVE':'0',
        'SHANA_LOCAL_LLM_TIMEOUT_SECONDS':'120','GAMMA_PRACTICE_MODEL':args.model,
        'SHANA_DATABASE_URL':'sqlite:///'+str(state/'conversation.sqlite3')}
    process=None
    try:
        store=Store(state)
        with store.transaction() as db:before=store.list(db,'run',None)
        (state/'ready.json').unlink(missing_ok=True)
        with (state/'host-graceful-close.log').open('w') as log:
            process=subprocess.Popen([sys.executable,str(project/'scripts/verify_practical_work.py'),
                '--model',args.model,'--endpoint',args.endpoint,'--child-state',str(state)],cwd=project,env=env,
                stdin=subprocess.DEVNULL,stdout=log,stderr=log)
            ready=until(lambda:json.loads((state/'ready.json').read_text()) if (state/'ready.json').exists() else None,
                lambda v:v is not None,40)
            assert ready['pid']==process.pid;report['processes'].append(ready)
            process.terminate();process.wait(timeout=30);assert process.returncode==0
        assert store.read('brain','state')['owner']==''
        with store.transaction() as db:assert before==store.list(db,'run',None),'No existing task may replay while closing owner'
        snapshot=state.parent/(state.name+'-snapshot');restored=state.parent/(state.name+'-restored')
        export_snapshot(state,snapshot);restore_snapshot(snapshot,restored)
        copied=Store(restored)
        with store.transaction() as ad,copied.transaction() as bd:
            assert store.list(ad,'run',None)==copied.list(bd,'run',None)
            assert store.list(ad,'activity',None)==copied.list(bd,'activity',None)
        report['snapshot']={'snapshot':str(snapshot),'restored':str(restored)}
        report['checks']['closed_state_transfer_preserves_practical_history']=True
        report['checks']['graceful_owner_close_without_task_replay']=True
        report['complete']=True
    except BaseException as exc:
        report.update(error_type=type(exc).__name__,error=str(exc)[:2000]);raise
    finally:
        if process and process.poll() is None:process.terminate();process.wait(timeout=30)
        report['temporary_processes_absent']=all(not Path('/proc',str(p['pid'])).exists() for p in report['processes'])
        publish(state/'report.json',report)
        print(json.dumps({'report':str(state/'report.json'),'complete':report.get('complete',False)}))


if __name__=='__main__':main()
