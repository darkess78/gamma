"""Matched finite P3 foreground/background cold/warm observations, not an SLA."""
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

from practical_work_fixtures import packet
from verify_coordination_foundation import publish,until


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',required=True);parser.add_argument('--endpoint',required=True)
    parser.add_argument('--repetitions',type=int,default=2)
    args=parser.parse_args();project=Path(__file__).resolve().parents[1]
    if os.name!='posix' or project.name in {'gamma-main','gamma-modular-phase1'} or not 1<=args.repetitions<=3:
        raise RuntimeError('finite isolated Gamma benchmark required')
    with urllib.request.urlopen(args.endpoint.rstrip('/')+'/api/ps',timeout=5) as response:
        if json.load(response).get('models'):raise RuntimeError('endpoint occupied; no inference sent')
    import httpx
    root=project/'data/coordination/p3'/('responsiveness-'+uuid.uuid4().hex);root.mkdir(mode=0o700,parents=True)
    report={'model':args.model,'same_task':'inventory CLI source packet','samples':[],
        'dialogue_prompt':'I am taking a short break. Give me one practical reason to keep a file inventory.',
        'streaming':False,'first_text_equals_full_text':True,'voice_start_ms':None,
        'text_timing_scope':'buffered HTTP reply arrival; browser paint time is not measured',
        'voice_limit':'private text path does not synthesize speech','activation':False,
        'limits':['small finite sample, no percentile or usability claim','nonpreemptive active generation can delay chat',
                  'warm denotes resident before task; the worker unload policy may make the later foreground request cold']}
    for repeat in range(args.repetitions):
        for thermal in ('cold','warm'):
            for background in (False,True):
                state=root/f'{repeat}-{thermal}-{int(background)}';state.mkdir(mode=0o700)
                token,password=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
                env={**os.environ,'PYTHONPATH':str(project/'src'),'SHANA_PROJECT_ROOT':str(project),
                    'GAMMA_ISOLATED_COORDINATION':'1','GAMMA_COORDINATION_STATE_DIR':str(state),
                    'SHANA_API_AUTH_ENABLED':'true','SHANA_API_BEARER_TOKEN':token,
                    'SHANA_DASHBOARD_AUTH_ENABLED':'true','SHANA_DASHBOARD_AUTH_USERNAME':'isolated-owner',
                    'SHANA_DASHBOARD_AUTH_PASSWORD':password,'SHANA_DASHBOARD_SESSION_SECRET':secrets.token_urlsafe(32),
                    'SHANA_DASHBOARD_COOKIE_SECURE':'false','SHANA_LLM_PROVIDER':'local','SHANA_LLM_ROUTER_ENABLED':'false',
                    'SHANA_LOCAL_LLM_ENDPOINT':args.endpoint,'SHANA_LOCAL_LLM_MODEL':args.model,
                    'SHANA_LOCAL_LLM_ENABLE_ROUTING':'false','SHANA_LOCAL_LLM_KEEP_ALIVE':'60s' if thermal=='warm' else '0',
                    'SHANA_LOCAL_LLM_TIMEOUT_SECONDS':'120','GAMMA_PRACTICE_MODEL':args.model,
                    'SHANA_DATABASE_URL':'sqlite:///'+str(state/'conversation.sqlite3')}
                sample={'repeat':repeat,'initial_thermal_condition':thermal,'background':background}
                log=(state/'host.log').open('w');process=None;client=None
                try:
                    process=subprocess.Popen([sys.executable,str(project/'scripts/verify_practical_work.py'),
                        '--model',args.model,'--endpoint',args.endpoint,'--child-state',str(state)],cwd=project,
                        env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log)
                    ready=until(lambda:json.loads((state/'ready.json').read_text()) if (state/'ready.json').exists() else None,
                        lambda v:v is not None,40)
                    client=httpx.Client(base_url=ready['dashboard'],timeout=180)
                    assert client.post('/login',data={'username':'isolated-owner','password':password}).status_code in {200,303}
                    def chat(text,session):
                        began=time.monotonic()
                        response=client.post('/api/monitor/input',json={'audience':'owner_private','request_id':uuid.uuid4().hex,
                            'input_mode':'private_text','text':text,'session_id':session})
                        response.raise_for_status()
                        return response.json()['result']['assistant_response'],(time.monotonic()-began)*1000
                    def details(identifier):
                        response=client.get('/api/coordination/work',params={'audience':'owner_private','task_id':identifier})
                        response.raise_for_status();return response.json()
                    def work(action,**values):
                        response=client.post('/api/coordination/work',json={'audience':'owner_private','request_id':uuid.uuid4().hex,
                            'action':action,**values});response.raise_for_status();return response.json()
                    # Same task is staged in every case. Staging does no inference.
                    task=work('prepare',spec=packet('inventory'),maximum_attempts=2,budget_seconds=600)['activity']['id']
                    if thermal=='warm':
                        from gamma.llm.backend_admission import backend_slot
                        prime={'model':args.model,'prompt':'Say ready.','stream':False,'think':False,'keep_alive':60,
                            'options':{'num_ctx':8192,'num_predict':8,'temperature':0}}
                        req=urllib.request.Request(args.endpoint.rstrip('/')+'/api/generate',data=json.dumps(prime).encode(),
                            headers={'Content-Type':'application/json'})
                        with backend_slot(args.endpoint,foreground=False,timeout=120):
                            with urllib.request.urlopen(req,timeout=60) as response:primed=json.load(response)
                        assert primed.get('done') is True
                        sample['warm_fixture']={'same_model_inference':True,'keep_alive_seconds':60,
                            'load_duration':primed.get('load_duration'),'prompt_eval_count':primed.get('prompt_eval_count')}
                    telemetry=state/'dialogue-telemetry.jsonl'
                    preceding=len(telemetry.read_text().splitlines()) if telemetry.exists() else 0
                    if background:
                        (state/'hold-candidate').touch()
                        work('start',task_id=task)
                        until(lambda:(state/'worker-http-started.json').exists(),bool,90)
                    reply,elapsed=chat(report['dialogue_prompt'],'measured')
                    assert reply['display_text'] and not reply['reason_code'].startswith('contract_fallback'),reply
                    sample.update(first_text_ms=round(elapsed,3),full_text_ms=round(elapsed,3),reply=reply,
                        dialogue_telemetry=[json.loads(line) for line in telemetry.read_text().splitlines()[preceding:]])
                    if background:
                        until(lambda:(state/'candidate-ready.json').exists(),bool,90)
                        status_before=details(task);requested=time.monotonic()
                        result=client.post('/api/coordination/control',json={'audience':'owner_private','request_id':uuid.uuid4().hex,
                            'action':'stop','run_id':task});result.raise_for_status()
                        sample['cancel_request_ms']=round((time.monotonic()-requested)*1000,3)
                        (state/'hold-candidate').unlink()
                        final=until(lambda:details(task),lambda v:v['run']['status'] in {'completed','blocked','needs_checker','stopped'},150)
                        acknowledged=(time.monotonic()-requested)*1000
                        sample['cancel_acknowledged_observed_ms']=round(acknowledged,3)
                        sample['cancel_observation_poll_ms']=100
                        sample['cancel_condition']='after_reply_at_held_durable_candidate_boundary'
                        sample['progress_before_cancel']={'stage':status_before['checkpoint']['stage'],
                            'attempt_count':status_before['checkpoint']['attempt_count'],'status':status_before['run']['status']}
                        sample['final_status']=final['run']['status'];sample['worker_attempts']=final['checkpoint'].get('attempts',[])
                    else:sample.update(cancel_request_ms=None,cancel_acknowledged_observed_ms=None)
                finally:
                    if client:client.close()
                    (state/'hold-candidate').unlink(missing_ok=True)
                    if process and process.poll() is None:process.terminate();process.wait(timeout=150)
                    log.close()
                    sample['temporary_process_absent']=process is not None and not Path('/proc',str(process.pid)).exists()
                    report['samples'].append(sample);publish(root/'report.json',report)
                print(json.dumps({'sample':len(report['samples']),'thermal':thermal,'background':background,
                    'full_text_ms':sample.get('full_text_ms')}),flush=True)
                # The isolated warm request is finite. Release its own residency
                # under cooperative admission before the next cold condition.
                from gamma.llm.backend_admission import backend_slot
                payload=json.dumps({'model':args.model,'keep_alive':0}).encode()
                req=urllib.request.Request(args.endpoint.rstrip('/')+'/api/generate',data=payload,
                    headers={'Content-Type':'application/json'})
                with backend_slot(args.endpoint,foreground=False,timeout=120):
                    with urllib.request.urlopen(req,timeout=30) as response:response.read()
    report['complete']=len(report['samples'])==args.repetitions*4
    publish(root/'report.json',report);print(json.dumps({'report':str(root/'report.json'),'complete':report['complete']}),flush=True)


if __name__=='__main__':main()
