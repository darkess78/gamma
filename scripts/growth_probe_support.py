"""Finite isolated P4 host and observer used by the actual growth journey."""
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

GUIDANCE=('For any owner question about a work task blocker, progress, required input, what would unblock it, '
          'or the next thing the owner should provide: first identify the matching task in activities. '
          'Set state_updates.activity_action to report_status and state_updates.activity_run_id to that task entry\'s exact id. '
          'Do this before giving an answer from a task goal or conversation memory. Do not choose wait for this status request. '
          'The checked status capability supplies the current worker evidence and owner question; do not reconstruct an input filename. '
          'If the task is ambiguous, ask which task. Other conversation keeps its normal judgment. '
          'This scoped trial grants no permission and never turns a blocked task into completed work.')


def hashed(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def source_files(project):
    return {p.relative_to(project).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((project/'src/gamma').rglob('*')) if p.is_file()
            and '__pycache__' not in p.parts and p.suffix in {'.py','.html','.js','.json','.md'}}


def source_hash(project):
    return hashed(source_files(project))


def checker_files(project):
    return {p.relative_to(project).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((project/'scripts').rglob('*')) if p.is_file()
            and '__pycache__' not in p.parts and p.suffix in {'.py','.pyw','.cjs','.js'}}


def probe_contains(system,needle):
    # An artifact may be JSON text inside the JSON context. Match its exact
    # value under those escaping layers without recording the private prompt.
    forms={needle}
    for _ in range(3):
        forms.update(json.dumps(s,ensure_ascii=ascii_only)[1:-1]
                     for s in tuple(forms) for ascii_only in (False,True))
    return any(form in system for form in forms)


def child(project,state):
    original=urllib.request.urlopen
    def observed(req,*args,**kwargs):
        if isinstance(req,urllib.request.Request) and req.full_url.endswith('/api/generate') and req.data:
            data=json.loads(req.data);system=data.get('system','')
            probe={}
            probe_path=state/'growth-prompt-probe.json'
            if probe_path.exists():
                raw=probe_path.read_bytes()
                if len(raw)>16000:raise ValueError('oversized private verification probe')
                values=json.loads(raw)
                if (set(values)!={'needles'} or not isinstance(values['needles'],list) or len(values['needles'])>12
                    or any(not isinstance(s,str) or not 1<=len(s)<=1000 for s in values['needles'])):
                    raise ValueError('invalid private verification probe')
                probe={'probe_manifest_sha256':hashlib.sha256(raw).hexdigest(),
                    'matched_probe_indices':[i for i,s in enumerate(values['needles']) if probe_contains(system,s)]}
            with (state/'growth-prompt-observations.jsonl').open('a',encoding='utf-8') as stream:
                stream.write(json.dumps({'at':time.time(),'primary_private_turn':'Private Coordination Context' in system,
                    'guidance_in_actual_http_system':GUIDANCE in system,'system_sha256':hashed(system),**probe})+'\n')
        return original(req,*args,**kwargs)
    urllib.request.urlopen=observed
    from verify_practical_work import child as practical_child
    practical_child(project,state)


class Host:
    def __init__(self,project,state,model,endpoint):
        self.project,self.state,self.model,self.endpoint=project,state,model,endpoint
        self.process=self.client=self.log=None
    def start(self):
        import httpx
        state,project=self.state,self.project
        state.mkdir(parents=True,exist_ok=True,mode=0o700)
        token,password=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
        env={**os.environ,'PYTHONPATH':str(project/'src'),'SHANA_PROJECT_ROOT':str(project),
            'GAMMA_ISOLATED_COORDINATION':'1','GAMMA_COORDINATION_STATE_DIR':str(state),
            'SHANA_API_AUTH_ENABLED':'true','SHANA_API_BEARER_TOKEN':token,
            'SHANA_DASHBOARD_AUTH_ENABLED':'true','SHANA_DASHBOARD_AUTH_USERNAME':'isolated-owner',
            'SHANA_DASHBOARD_AUTH_PASSWORD':password,'SHANA_DASHBOARD_SESSION_SECRET':secrets.token_urlsafe(32),
            'SHANA_DASHBOARD_COOKIE_SECURE':'false','SHANA_LLM_PROVIDER':'local','SHANA_LLM_ROUTER_ENABLED':'false',
            'SHANA_LOCAL_LLM_ENDPOINT':self.endpoint,'SHANA_LOCAL_LLM_MODEL':self.model,
            'SHANA_LOCAL_LLM_ENABLE_ROUTING':'false','SHANA_LOCAL_LLM_KEEP_ALIVE':'0',
            'SHANA_LOCAL_LLM_TIMEOUT_SECONDS':'120','GAMMA_PRACTICE_MODEL':self.model,
            'SHANA_DATABASE_URL':'sqlite:///'+str(state/'conversation.sqlite3')}
        (state/'ready.json').unlink(missing_ok=True)
        self.log=(state/('host-'+uuid.uuid4().hex+'.log')).open('w')
        self.process=subprocess.Popen([sys.executable,str(getattr(self,'child_script',project/'scripts/verify_growth_flow.py')),
            '--model',self.model,'--endpoint',self.endpoint,'--child-state',str(state)],cwd=project,env=env,
            stdin=subprocess.DEVNULL,stdout=self.log,stderr=self.log)
        self.ready=until(lambda:json.loads((state/'ready.json').read_text()) if (state/'ready.json').exists() else None,
            lambda value:value is not None,40)
        assert self.ready['pid']==self.process.pid
        self.client=httpx.Client(base_url=self.ready['dashboard'],timeout=180)
        assert self.client.post('/api/coordination/growth',json={}).status_code==401
        assert self.client.post('/login',data={'username':'isolated-owner','password':password}).status_code in {200,303}
        self.password=password;return self
    def stop(self):
        if self.client:self.client.close();self.client=None
        if self.process and self.process.poll() is None:self.process.terminate();self.process.wait(timeout=150)
        if self.log:self.log.close();self.log=None
    def request(self,method,path,**values):
        response=self.client.request(method,path,**values);response.raise_for_status();return response.json()
    def growth(self,action,values):
        return self.request('POST','/api/coordination/growth',json={'request_id':uuid.uuid4().hex,
            'audience':'owner_private','action':action,'values':values})
    def growth_state(self):return self.request('GET','/api/coordination/growth?audience=owner_private')
    def chat(self,text,session='growth-proof'):
        rid=uuid.uuid4().hex
        response=self.request('POST','/api/monitor/input',json={'request_id':rid,'audience':'owner_private',
            'input_mode':'private_text','text':text,'session_id':session})['result']['assistant_response']
        assert not response['reason_code'].startswith('contract_fallback') and not response['speech_text'],response
        return rid,response
    def prepare(self,spec):
        return self.request('POST','/api/coordination/work',json={'request_id':uuid.uuid4().hex,
            'audience':'owner_private','action':'prepare','spec':spec,'maximum_attempts':2,'budget_seconds':1800,
            'authorize_start':True})['activity']['id']
    def start_work(self,identifier):
        return self.request('POST','/api/coordination/work',json={'request_id':uuid.uuid4().hex,
            'audience':'owner_private','action':'start','task_id':identifier})
    def work(self,identifier):
        return self.request('GET','/api/coordination/work',params={'audience':'owner_private','task_id':identifier})
    def prompt_flags(self):
        path=self.state/'growth-prompt-observations.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def blocked_spec(topic,filename):
    return {'mode':'build','goal':f'Create a {topic} report using the current README contract. '
        f'An earlier plan referred to old-{topic}.csv. That plan may be stale; the current README controls. '
        'Do not invent missing inputs. Ask the owner for the required current source.',
        'files':{'report.py':'# Waiting for the actual required input.\n',
            'README.md':f'The actual required input for this revision is {filename}. The earlier old-{topic}.csv name is obsolete. '
                f'No input data is supplied. Do not create a substitute. Ask for the exact {filename} contents before implementing the report.',
            'test_report.py':'import unittest\nclass RequiredInput(unittest.TestCase):\n    def test_required(self):\n        self.fail("Required source input was not supplied.")\n'},
        'editable':['report.py'],'checks':[{'name':'required_source','module':'test_report','kind':'regression'}]}


def status_oracle(response,expected):
    text=response.get('display_text','')
    updates=response.get('state_updates') or {}
    return (response.get('reason_code')=='work_status' and expected['task_id'] in text
            and updates.get('activity_action')=='report_status' and updates.get('activity_run_id')==expected['task_id']
            and expected['question'] in text and 'not verified' in text.lower())


def experience(host,source_ids,situation,observed):
    return host.growth('experience',{'source_ids':source_ids,'situation':situation,
        'options':['Keep direct reports distinct from inferences','Examine alternative interpretations'],
        'action':'owner_review_of_sourced_exchange',
        'expected_outcome':'A traceable observation with uncertainty, not proof of a motive or unobserved reasoning.',
        'observed_outcome':observed,'uncertainty':'This is one scoped observation, not a universal causal conclusion.'})
