"""Real process/HTTP adversarial tests for shared model admission."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
from urllib import request

import pytest

from gamma.resources.shared_ledger import AdmissionBusy, DuplicateRequest, Ledger
from gamma.resources.process_identity import process_identity
from gamma.resources import shared_execution as execution


def test_atomic_multiple_budgets_and_reserved_capacity(tmp_path):
    ledger = Ledger(tmp_path/'leases', {'endpoint': (3,1), 'ram_mb': (100,40)})
    bg = ledger.acquire({'endpoint':2, 'ram_mb':60}, priority='background')
    with pytest.raises(AdmissionBusy):
        ledger.acquire({'endpoint':1, 'ram_mb':1}, priority='background')
    interactive = ledger.acquire({'endpoint':1, 'ram_mb':40})
    with pytest.raises(AdmissionBusy):
        ledger.acquire({'endpoint':1})
    ledger.release(bg)
    assert len(ledger.snapshot()['allocations']) == 2
    ledger.release(interactive)
    assert not ledger.snapshot()['allocations']


def test_failed_multiresource_acquisition_does_not_consume_partial_budget(tmp_path):
    ledger=Ledger(tmp_path/'ledger', {'a':(1,0),'b':(1,0)})
    token=ledger.acquire({'b':1})
    with pytest.raises(AdmissionBusy): ledger.acquire({'a':1,'b':1})
    other=ledger.acquire({'a':1})
    assert len(ledger.snapshot()['allocations']) == 2
    ledger.release(other); ledger.release(token)


def test_deadline_does_not_expire_active_or_uncertain_and_dedup_is_durable(tmp_path):
    ledger=Ledger(tmp_path/'ledger', {'endpoint':(1,0)})
    token=ledger.acquire({'endpoint':1}, request_id='logical-request')
    ledger.uncertain(token)
    time.sleep(.03)
    recreated=Ledger(tmp_path/'ledger', {'endpoint':(1,0)})
    with pytest.raises(AdmissionBusy): recreated.acquire({'endpoint':1},timeout=.04)
    assert recreated.snapshot()['unresolved'][0]['token'] == token
    assert not recreated.snapshot()['waiting']
    recreated.release(token)
    with pytest.raises(DuplicateRequest): recreated.acquire({'endpoint':1},request_id='logical-request')


def test_policy_conflicts_fail_closed(tmp_path):
    Ledger(tmp_path/'ledger', {'a':(1,1)})
    with pytest.raises(ValueError): Ledger(tmp_path/'ledger', {'a':(2,1)})
    with pytest.raises(ValueError): Ledger(tmp_path/'ledger', {'a':(True,0)})


RACE = '''
import sys,time,json
from pathlib import Path
from gamma.resources.shared_ledger import Ledger,AdmissionBusy
p=Path(sys.argv[1]); ledger=Ledger(p, {'endpoint':(1,0)})
try:
 token=ledger.acquire({'endpoint':1},timeout=3)
 with open(str(p)+'.events','a') as f: f.write(json.dumps(['start',time.monotonic(),token])+'\\n')
 time.sleep(.09)
 with open(str(p)+'.events','a') as f: f.write(json.dumps(['end',time.monotonic(),token])+'\\n')
 ledger.release(token)
except AdmissionBusy:
 sys.exit(2)
'''


def test_eight_separate_processes_never_overlap(tmp_path):
    db=tmp_path/'race'
    children=[subprocess.Popen([sys.executable,'-c',RACE,str(db)],stdout=subprocess.PIPE,stderr=subprocess.PIPE) for _ in range(8)]
    for child in children:
        out,err=child.communicate(timeout=15)
        assert child.returncode == 0,err.decode()
    events=[json.loads(line) for line in Path(str(db)+'.events').read_text().splitlines()]
    events.sort(key=lambda item:item[1])
    active=set(); peak=0
    for kind,_,token in events:
        if kind=='start': active.add(token)
        else: active.remove(token)
        peak=max(peak,len(active))
    assert peak == 1 and len(events)==16 and not active


def test_interactive_waiter_precedes_earlier_background(tmp_path):
    ledger=Ledger(tmp_path/'ledger',{'slot':(1,0)})
    held=ledger.acquire({'slot':1}); order=[]; errors=[]
    def wait(priority):
        try:
            token=ledger.acquire({'slot':1},priority=priority,timeout=2)
            order.append(priority); time.sleep(.03); ledger.release(token)
        except BaseException as exc: errors.append(exc)
    background=threading.Thread(target=wait,args=('background',)); background.start()
    deadline=time.monotonic()+2
    while len(ledger.snapshot()['waiting']) != 1 and time.monotonic()<deadline: time.sleep(.01)
    interactive=threading.Thread(target=wait,args=('interactive',)); interactive.start()
    while len(ledger.snapshot()['waiting']) != 2 and time.monotonic()<deadline: time.sleep(.01)
    assert len(ledger.snapshot()['waiting']) == 2
    ledger.release(held)
    background.join(3); interactive.join(3)
    assert not errors and order == ['interactive','background']


@contextmanager
def slow_provider(delay=.3):
    entered=threading.Event(); completed=threading.Event(); calls=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(self.path); entered.set()
            time.sleep(delay)
            try:
                self.send_response(200); self.end_headers(); self.wfile.write(b'{"response":"fixture"}')
            except (BrokenPipeError,ConnectionResetError): pass
            finally: completed.set()
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    try: yield f'http://127.0.0.1:{server.server_port}',entered,completed,calls
    finally: server.shutdown();server.server_close();thread.join(2)


def test_http_timeout_retains_slot_while_provider_runs_and_never_retries():
    with slow_provider() as (url,entered,completed,calls):
        with pytest.raises(execution.AdmissionUncertain):
            with execution.admitted_urlopen(request.urlopen,request.Request(url+'/api/generate',data=b'{}'),timeout=.03) as response:
                response.read()
        assert entered.is_set() and not completed.is_set()
        ledger,demands,_=execution.authority(url)
        with pytest.raises(AdmissionBusy): ledger.acquire(demands)
        assert len(execution.status()['unresolved'])==1 and len(calls)==1
        assert completed.wait(2)
        # A server response lost to the caller does not give this client evidence.
        assert not execution.reconcile()['released']
        assert execution.status()['allocations']


def test_thread_client_timeout_does_not_release_running_native_compute():
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    entered=threading.Event(); finish=threading.Event()
    def compute():
        with execution.execution(local=True,workload='stt'):
            entered.set();finish.wait(3)
    with ThreadPoolExecutor(1) as pool:
        future=pool.submit(compute); assert entered.wait(2)
        with pytest.raises(TimeoutError): future.result(timeout=.02)
        assert execution.status()['allocations']
        finish.set(); future.result(timeout=2)
    assert not execution.status()['allocations']


DEAD_OWNER='''
import os,sys
from gamma.resources.shared_execution import execution
with execution('http://127.0.0.1:19999' if sys.argv[1]=='remote' else None,local=sys.argv[1]=='local') as token:
 print(token,flush=True)
 os._exit(0)
'''


@pytest.mark.parametrize('kind',['local','remote'])
def test_worker_death_reconciliation_requires_matching_execution_evidence(kind):
    child=subprocess.run([sys.executable,'-c',DEAD_OWNER,kind],capture_output=True,text=True,timeout=10)
    assert child.returncode==0,child.stderr
    token=child.stdout.strip()
    assert execution.status()['unresolved'][0]['token']==token
    result=execution.reconcile()
    if kind=='local': assert result['released']==[token] and not result['allocations']
    else: assert not result['released'] and result['allocations']


def test_unknown_process_identity_never_releases(monkeypatch,tmp_path):
    ledger=Ledger(tmp_path/'ledger',{'a':(1,0)})
    token=ledger.acquire({'a':1},execution_kind='local')
    monkeypatch.setattr('gamma.resources.shared_ledger.process_identity',lambda pid:'unknown')
    assert not ledger.reconcile_local_dead(token)
    assert ledger.snapshot()['allocations']


def test_background_does_not_dispatch_provider_when_reserved(monkeypatch):
    from gamma.llm.local_adapter import LocalLLMAdapter
    from gamma.llm.base import LLMCallContext
    called=[]
    monkeypatch.setattr('gamma.llm.local_adapter.request.urlopen',lambda *a,**k:called.append(True))
    with pytest.raises(Exception):
        LocalLLMAdapter().generate_reply('system','fixture',call_context=LLMCallContext(interaction_mode='improvement'))
    assert called==[] and not execution.status()['allocations']


def test_same_authority_across_runtime_roots(monkeypatch,tmp_path):
    from gamma.config import settings
    first=execution.shared_path()
    monkeypatch.setattr(settings,'data_dir',tmp_path/'other-data')
    monkeypatch.setattr(settings,'project_root',tmp_path/'other-project')
    assert execution.shared_path()==first
    assert execution.endpoint_key('http://localhost:11434/api/generate')==execution.endpoint_key('http://127.0.0.1:11434/api/chat')


def test_brain_background_context_reaches_provider():
    from gamma.brain.queue import BrainQueue
    queue=BrainQueue()
    try:
        assert queue.submit(execution.priority_for,priority='background').result(timeout=3)=='background'
        assert queue.submit(execution.priority_for).result(timeout=3)=='interactive'
    finally: queue.close()


def test_piper_subprocess_capacity_released_only_after_exit():
    result=execution.admitted_subprocess_run([sys.executable,'-c','print("fixture")'],workload='tts',capture_output=True,text=True,check=True)
    assert result.stdout.strip()=='fixture'
    assert not execution.status()['allocations']
    with pytest.raises(subprocess.TimeoutExpired):
        execution.admitted_subprocess_run([sys.executable,'-c','import time;time.sleep(10)'],workload='tts',timeout=.05)
    assert not execution.status()['allocations']


def test_process_identity_live_dead_and_invalid():
    assert process_identity(os.getpid()) not in {'unknown','dead'}
    assert process_identity(True)=='unknown'
    process=subprocess.Popen([sys.executable,'-c','pass']);process.wait(timeout=3)
    assert process_identity(process.pid)=='dead'


def test_real_model_worker_death_keeps_provider_slot(tmp_path,monkeypatch):
    from gamma.brain.model_runtime import ModelRuntime
    from gamma.voice.worker_client import PersistentWorkerClient
    from gamma.resources.admission import Admission
    from gamma.config import settings
    root=tmp_path/'runtime-root';(root/'config').mkdir(parents=True)
    (root/'pyproject.toml').write_text('');(root/'config/app.example.toml').write_text('')
    with slow_provider(.5) as (url,entered,completed,calls):
        monkeypatch.setattr(settings,'llm_provider','local')
        monkeypatch.setattr(settings,'local_llm_endpoint',url)
        env=dict(os.environ, SHANA_PROJECT_ROOT=str(root), SHANA_LOCAL_LLM_ENDPOINT=url,SHANA_LLM_PROVIDER='local',SHANA_LLM_ROUTER_ENABLED='false')
        client=PersistentWorkerClient([sys.executable,'-m','gamma.brain.model_worker'],cwd=root,env=env,timeout=5)
        runtime=ModelRuntime(client,Admission(tmp_path/'legacy.db'))
        try:
            client.start();client.timeout=.08
            with pytest.raises(Exception) as result: runtime.generate_reply('fixture','fictional text')
            assert execution.is_uncertain(result.value)
            assert entered.is_set() and not completed.is_set()
            assert not client.status()['running']
            state=execution.reconcile()
            assert state['unresolved'] and not state['released']
            assert len(calls)==1
        finally: runtime.close()


def test_positive_qwen_terminal_receipt_reconciles_without_replay():
    state={'completed':False,'job':None,'posts':0}
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            state['posts']+=1; state['job']=self.headers.get('X-Shana-Job-ID')
            time.sleep(.15); state['completed']=True
            try: self.send_response(200);self.end_headers();self.wfile.write(b'audio')
            except (BrokenPipeError,ConnectionResetError): pass
        def do_GET(self):
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
            self.wfile.write(json.dumps({'schema_version':1,'generation':'a'*32,'job_id':state['job'],
                'state':'completed' if state['completed'] else 'running'}).encode())
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        url=f'http://127.0.0.1:{server.server_port}/tts'
        with pytest.raises(execution.AdmissionUncertain):
            with execution.admitted_urlopen(request.urlopen,request.Request(url,data=b'{}'),timeout=.02,workload='tts') as response: response.read()
        assert state['job'] and execution.status()['unresolved']
        assert not execution.reconcile()['released']
        deadline=time.monotonic()+2
        while not state['completed'] and time.monotonic()<deadline: time.sleep(.01)
        result=execution.reconcile()
        assert len(result['released'])==1 and not result['allocations'] and state['posts']==1
        assert not execution.reconcile()['released']
    finally: server.shutdown();server.server_close();thread.join(2)


def test_unreachable_qwen_receipt_stays_unresolved():
    with slow_provider(.1) as (url,_,_,_):
        with pytest.raises(execution.AdmissionUncertain):
            with execution.admitted_urlopen(request.urlopen,request.Request(url+'/tts',data=b'{}'),timeout=.01,workload='tts'): pass
    assert not execution.reconcile()['released']
    assert execution.status()['unresolved']


@pytest.mark.parametrize('target',['stt','emotion','audio-events','openai','safety'])
def test_concrete_provider_paths_cannot_bypass_held_budget(target):
    ledger,demands,_=execution.authority();token=ledger.acquire(demands)
    try:
        if target=='stt':
            from gamma.voice.stt import FasterWhisperSTTBackend
            call=lambda:FasterWhisperSTTBackend.__new__(FasterWhisperSTTBackend).transcribe_audio('unused')
        elif target=='emotion':
            from gamma.voice.audio_understanding import HuggingFaceSpeakerEmotionBackend as Backend
            call=lambda:Backend.__new__(Backend).analyze(None,transcript='',prosody=None)
        elif target=='audio-events':
            from gamma.voice.audio_understanding import HuggingFaceAudioEventBackend as Backend
            call=lambda:Backend.__new__(Backend).detect(None,transcript='')
        elif target=='openai':
            from gamma.llm.openai_adapter import OpenAIAdapter
            call=lambda:OpenAIAdapter.__new__(OpenAIAdapter).generate_reply('fixture','fixture')
        else:
            from gamma.safety.review_provider import generate_review
            call=lambda:generate_review('fixture','fixture','model',0,'http://127.0.0.1:19999',.1)
        with pytest.raises(Exception) as result: call()
        assert execution.is_busy(result.value)
        assert len(execution.status()['allocations'])==1
    finally: ledger.release(token)


def test_admission_rejection_does_not_strand_tts_runtime(tmp_path):
    from unittest.mock import Mock
    from gamma.resources.admission import Admission
    from gamma.voice.tts_runtime import TTSRuntime
    client=Mock(); client.synthesize.side_effect=AdmissionBusy('reserved')
    legacy=Admission(tmp_path/'legacy'); runtime=TTSRuntime(client,legacy)
    with pytest.raises(AdmissionBusy):runtime.synthesize('fixture')
    assert legacy.status('tts') is None and legacy.status('voice-inference') is None


def test_operator_http_status_reconcile_and_auth(monkeypatch):
    from fastapi.testclient import TestClient
    from gamma.dashboard import main
    monkeypatch.setattr(main,'is_authenticated',lambda *a,**kw:True)
    client=TestClient(main.app)
    token=None
    try:
        ledger,demands,_=execution.authority();token=ledger.acquire(demands)
        response=client.get('/api/resources/admission')
        assert response.status_code==200 and response.json()['allocations'][0]['token']==token
        assert client.post('/api/resources/admission/reconcile').json()['released']==[]
        html=client.get('/dashboard/workers')
        assert html.status_code==200 and 'admission-status' in html.text
        monkeypatch.setattr(main,'is_authenticated',lambda *a,**kw:False)
        assert client.get('/api/resources/admission').status_code==401
        assert client.post('/api/resources/admission/reconcile').status_code==401
    finally:
        if token:ledger.release(token)
        client.close()


def test_distinct_runtime_process_roots_share_provider_capacity(tmp_path):
    active=0;peak=0;calls=0;lock=threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            nonlocal active,peak,calls
            with lock: active+=1;peak=max(peak,active);calls+=1
            time.sleep(.08)
            self.send_response(200);self.end_headers();self.wfile.write(b'{"response":"fixture"}')
            with lock: active-=1
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    code='''
import json
from gamma.config import settings
from gamma.llm.local_adapter import LocalLLMAdapter
reply=LocalLLMAdapter().generate_reply('system','fictional fixture')
print(json.dumps({'root':str(settings.project_root),'data':str(settings.data_dir),'text':reply.text}))
'''
    children=[]
    try:
        for i in range(4):
            root=tmp_path/f'root-{i}';(root/'config').mkdir(parents=True)
            (root/'config/app.example.toml').write_text('');(root/'pyproject.toml').write_text('')
            env=dict(os.environ,SHANA_PROJECT_ROOT=str(root),SHANA_LOCAL_LLM_ENDPOINT=f'http://127.0.0.1:{server.server_port}')
            children.append(subprocess.Popen([sys.executable,'-c',code],cwd=root,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE))
        replies=[]
        for child in children:
            out,err=child.communicate(timeout=10);assert child.returncode==0,err
            replies.append(json.loads(out))
        assert len({reply['root'] for reply in replies})==4
        assert len({reply['data'] for reply in replies})==4
        assert peak==1 and calls==4 and active==0
        assert not execution.status()['allocations']
    finally:
        for child in children:
            if child.poll() is None:child.kill();child.wait(timeout=3)
        server.shutdown();server.server_close();thread.join(2)


def test_replayed_worker_identity_does_not_execute_provider_again():
    with slow_provider(.01) as (url,_,_,calls):
        for attempt in range(2):
            with execution.work_request('same-durable-worker-job'):
                if attempt==0:
                    with execution.admitted_urlopen(request.urlopen,request.Request(url,data=b'{}'),timeout=1) as response:response.read()
                else:
                    with pytest.raises(DuplicateRequest):
                        with execution.admitted_urlopen(request.urlopen,request.Request(url,data=b'{}'),timeout=1):pass
        assert len(calls)==1


def test_reused_pid_cannot_keep_dead_local_allocation(tmp_path,monkeypatch):
    ledger=Ledger(tmp_path/'pid-reuse',{'a':(1,0)})
    token=ledger.acquire({'a':1},execution_kind='local')
    monkeypatch.setattr('gamma.resources.shared_ledger.process_identity',lambda pid:'different-boot:later-start')
    assert ledger.reconcile_local_dead(token)
    assert not ledger.snapshot()['allocations']


def test_changed_workload_budget_policy_cannot_bypass_shared_configuration(tmp_path,monkeypatch):
    execution.authority()
    policy=tmp_path/'different.toml'
    policy.write_text('wait_seconds=2.0\n[budgets.model-work]\ncapacity=1\nreserved=1\n[workloads.stt.demands]\nmodel-work=1\n')
    monkeypatch.setenv('GAMMA_ADMISSION_POLICY',str(policy))
    with pytest.raises(ValueError,match='configuration conflict'): execution.authority()


def test_database_lock_cannot_admit_after_wait_deadline(tmp_path):
    ledger=Ledger(tmp_path/'deadline',{'a':(1,0)})
    connection=sqlite3.connect(ledger.path,isolation_level=None)
    connection.execute('BEGIN IMMEDIATE')
    start=time.monotonic()
    try:
        with pytest.raises(AdmissionBusy):ledger.acquire({'a':1},timeout=.02)
        assert time.monotonic()-start < .25
    finally:connection.rollback();connection.close()
    assert not ledger.snapshot()['allocations']


def test_completed_duplicate_remains_rejected_after_process_restart(tmp_path):
    ledger=Ledger(tmp_path/'dedup',{'a':(1,0)})
    token=ledger.acquire({'a':1},request_id='durable-logical-operation');ledger.release(token)
    code='''
import sys
from gamma.resources.shared_ledger import Ledger,DuplicateRequest
try:Ledger(sys.argv[1],{'a':(1,0)}).acquire({'a':1},request_id='durable-logical-operation')
except DuplicateRequest: print('duplicate suppressed')
else:sys.exit(7)
'''
    result=subprocess.run([sys.executable,'-c',code,str(ledger.path)],capture_output=True,text=True,timeout=5)
    assert result.returncode==0 and result.stdout.strip()=='duplicate suppressed'


def test_reviewer_worker_preserves_background_priority():
    from gamma.brain.model_runtime import ModelRuntime
    from gamma.resources.admission import Admission
    from unittest.mock import Mock
    import tempfile
    with tempfile.TemporaryDirectory() as root:
        client=Mock();client.request.return_value={'text':'fixture'}
        runtime=ModelRuntime(client,Admission(Path(root)/'legacy'))
        with execution.work_priority('background'):
            runtime.review_text('system','fixture',model='fixture-model',temperature=0)
        assert client.request.call_args.kwargs['admission_priority']=='background'


def test_background_waiters_cannot_fill_interactive_queue_positions(tmp_path):
    ledger=Ledger(tmp_path/'queue-budget',{'a':(1,0),'b':(1,0)})
    now=time.time()
    with sqlite3.connect(ledger.path) as conn:
        conn.executemany('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)',[
            (f'wait-{i}',f'wait-{i}','waiting','background',os.getpid(),process_identity(os.getpid()),
             'remote',now,now+10,json.dumps({'a':1}),'fixture') for i in range(96)])
    with pytest.raises(AdmissionBusy,match='queue positions'):
        ledger.acquire({'b':1},priority='background')
    token=ledger.acquire({'b':1})
    assert len(ledger.snapshot()['waiting'])==96
    ledger.release(token)


def test_dead_subprocess_parent_does_not_prove_descendants_exited(monkeypatch):
    import socket
    record={'process_group':12345,'host':socket.gethostname()}
    monkeypatch.setattr(execution.os,'killpg',lambda pid,signal:None)
    assert not execution._process_group_exited(record)
    def missing(pid,signal):raise ProcessLookupError()
    monkeypatch.setattr(execution.os,'killpg',missing)
    assert execution._process_group_exited(record)
    assert not execution._process_group_exited({'provider_process':{'pid':12345}})
