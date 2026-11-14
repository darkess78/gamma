"""Exact browser download QA on a closed P3 snapshot clone, with no inference."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import uuid

from gamma.coordination.store import Store, private_path
from gamma.coordination.transfer import restore_snapshot
from gamma.coordination.work import WorkRunner
from verify_coordination_foundation import publish, until


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def original_hashes(report_path,snapshot):
    values={'primary/report.json':digest(report_path),
            'primary/coordination.sqlite3':digest(report_path.parent/'coordination.sqlite3')}
    values.update({'snapshot/'+p.relative_to(snapshot).as_posix():digest(p)
                   for p in snapshot.rglob('*') if p.is_file()})
    return values


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--model',default='qwen3.8:27b')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args();project=Path(__file__).resolve().parents[1]
    if os.name!='posix' or project.name in {'gamma-main','gamma-modular-phase1'}:
        raise RuntimeError('isolated_Gamma_candidate_required')
    primary=private_path(args.report)
    if not primary.is_relative_to(project/'data/coordination/p3') or primary.name!='report.json':
        raise ValueError('primary_P3_report_required')
    original=json.loads(primary.read_text())
    if original.get('complete') is not True or not all(x is True for x in original['checks'].values()):
        raise ValueError('completed_primary_journey_required')
    snapshot=private_path(str(primary.parent)+'-snapshot')
    preserved=original_hashes(primary,snapshot)
    clone=private_path(project/'data/coordination/p3'/('download-review-'+uuid.uuid4().hex))
    output=private_path(args.output or project/'data/coordination/p3/review/download-browser.json')
    if not output.is_relative_to(project/'data/coordination/p3/review') or output.exists():
        raise ValueError('new_private_review_output_required')
    restore_snapshot(snapshot,clone)
    store=Store(clone)
    with store.transaction() as db:
        runs={r['id']:r for r in store.list(db,'run',None)}
        activities=store.list(db,'activity',None);turns=store.list(db,'turn',None)
        brain=store.get(db,'brain','state')
    if (not runs or any(r['status'] not in {'completed','blocked','needs_checker','stopped'} for r in runs.values())
        or any(a['status'] in {'waiting','queued','running','paused'} for a in activities)
        or any(t['status'] in {'pending','queued','recoverable'} for t in turns)
        or brain.get('permit') is not None):
        raise ValueError('clone_must_have_no_dispatchable_work_or_dialogue')
    identifier=original['tasks']['tracker_changed']['id']
    runner=WorkRunner(store,propose=lambda *_:(_ for _ in ()).throw(RuntimeError('QA_never_infers')))
    expected=runner.verify(runs[identifier])
    files=runner.artifacts.get(expected['source_ref'])['files']
    token,password=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
    env={**os.environ,'PYTHONPATH':str(project/'src'),'SHANA_PROJECT_ROOT':str(project),
        'GAMMA_ISOLATED_COORDINATION':'1','GAMMA_COORDINATION_STATE_DIR':str(clone),
        'SHANA_API_AUTH_ENABLED':'true','SHANA_API_BEARER_TOKEN':token,
        'SHANA_DASHBOARD_AUTH_ENABLED':'true','SHANA_DASHBOARD_AUTH_USERNAME':'isolated-owner',
        'SHANA_DASHBOARD_AUTH_PASSWORD':password,'SHANA_DASHBOARD_SESSION_SECRET':secrets.token_urlsafe(32),
        'SHANA_DASHBOARD_COOKIE_SECURE':'false','SHANA_LLM_PROVIDER':'local','SHANA_LLM_ROUTER_ENABLED':'false',
        'SHANA_LOCAL_LLM_ENDPOINT':'http://127.0.0.1:9','SHANA_LOCAL_LLM_MODEL':args.model,
        'SHANA_LOCAL_LLM_ENABLE_ROUTING':'false','SHANA_LOCAL_LLM_KEEP_ALIVE':'0',
        'SHANA_LOCAL_LLM_TIMEOUT_SECONDS':'10','GAMMA_PRACTICE_MODEL':args.model,
        'SHANA_DATABASE_URL':'sqlite:///'+str(clone/'conversation.sqlite3')}
    report={'scope':'isolated_artifact_browser_only','primary_report':str(primary.relative_to(project)),
        'primary_report_sha256':digest(primary),'snapshot_manifest_sha256':digest(snapshot/'manifest.json'),
        'model_inference_verified':False,'monitor_chat_verified_here':False,
        'inference_origin':'disabled_loopback_port_9','production_activated':False,'complete':False}
    process=None;log=(clone/'host.log').open('w')
    try:
        process=subprocess.Popen([sys.executable,str(project/'scripts/verify_practical_work.py'),
            '--model',args.model,'--endpoint','http://127.0.0.1:9','--child-state',str(clone)],
            cwd=project,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log)
        ready=until(lambda:json.loads((clone/'ready.json').read_text()) if (clone/'ready.json').exists() else None,
                    lambda value:value is not None,40)
        report['process']=ready
        node=subprocess.run(['node',str(project/'scripts/work_browser_probe.cjs')],input=json.dumps({
            'url':ready['dashboard'],'username':'isolated-owner','password':password,'task_id':identifier,
            'artifact_only':True,'download_path':str(clone/'downloaded-candidate.json'),
            'screenshot':str(clone/'work-private-hidden.png')}),text=True,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120)
        if node.returncode!=0:raise AssertionError(node.stderr[-1500:])
        browser=json.loads(node.stdout);report['browser']=browser
        downloaded=json.loads((clone/'downloaded-candidate.json').read_text())
        if (browser.get('exact_download_source') is not True or browser.get('monitor_chat_verified_here') is not False
            or browser.get('monitor_receipt') is not None or browser.get('errors')
            or browser.get('download_source_sha256')!=expected['source_sha256']
            or downloaded['activity']['id']!=identifier or downloaded['files']!=files
            or downloaded['checkpoint']['source_sha256']!=expected['source_sha256']):
            raise AssertionError('download_does_not_match_actual_current_candidate')
        report['downloaded_payload_sha256']=digest(clone/'downloaded-candidate.json')
    except Exception as exc:
        report['error_type']=type(exc).__name__;report['error']=str(exc)[:1500]
        raise
    finally:
        if process and process.poll() is None:
            process.terminate()
            try:process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill();process.wait(timeout=10);report['forced_cleanup']=True
        log.close();report['temporary_process_absent']=process is not None and not Path('/proc',str(process.pid)).exists()
        with store.transaction() as db:after={r['id']:r for r in store.list(db,'run',None)}
        report['existing_run_records_unchanged']=after==runs
        report['original_primary_and_snapshot_hashes_unchanged']=original_hashes(primary,snapshot)==preserved
        report['inference_attempts_absent']=not (clone/'worker-http-started.json').exists() and not (clone/'dialogue-telemetry.jsonl').exists()
        report['complete']=all(report.get(key) is True for key in (
            'temporary_process_absent','existing_run_records_unchanged',
            'original_primary_and_snapshot_hashes_unchanged','inference_attempts_absent')) and not report.get('error_type') and not report.get('forced_cleanup')
        publish(clone/'report.json',report)
    if not report['complete']:raise AssertionError('supplemental_browser_QA_incomplete')
    output.parent.mkdir(parents=True,exist_ok=True,mode=0o700);publish(output,report)
    print(json.dumps({'report':str(clone/'report.json'),'review_report':str(output),
        'exact_download_source_sha256':report['browser']['download_source_sha256'],'complete':True}))


if __name__=='__main__':main()
