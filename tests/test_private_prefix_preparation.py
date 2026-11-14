"""Optional prefix preparation preserves the actual generation and fence boundaries."""
from contextlib import contextmanager
from types import SimpleNamespace as NS
from urllib import error
import json
import math
import time

import pytest
from gamma.config import settings
from gamma.errors import ExternalServiceError, GammaError
from gamma.llm import local_adapter as module
from gamma.llm.local_adapter import LocalLLMAdapter, local_inference_admission, prepare_private_prefix
from gamma.llm.local_metrics import local_cache_counts, allowlisted_local_timing
from gamma.llm.local_runtime import local_runtime_profile, current_local_runtime_profile, validate_primary_prefix_cache
from gamma.coordination.store import OwnershipLost
from test_private_prefix_cache import SYSTEM,STATIC,MODEL,ENDPOINT,VERSION,SHOW,TAGS


@pytest.fixture
def transport(monkeypatch):
    for key,value in {'local_llm_model':MODEL,'local_llm_endpoint':ENDPOINT,'local_llm_keep_alive':'0',
                      'local_llm_timeout_seconds':20,'local_llm_enable_routing':False,'llm_router_enabled':False}.items():
        monkeypatch.setattr(settings,key,value)
    state=NS(calls=[],active=False,admissions=[],primer_seen=False,version=VERSION,show=SHOW,tags=TAGS,
             primer_error=None,normal_error=None,metadata_error=None)
    @contextmanager
    def slot(endpoint,**kwargs):
        assert not state.active
        state.active=True;state.admissions.append((endpoint,kwargs))
        try:yield
        finally:state.active=False
    class Response:
        def __init__(self,value):self.raw=json.dumps(value).encode()
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def read(self,size=None):return self.raw if size is None else self.raw[:size]
    def open_request(req,**kwargs):
        assert state.active,'Verification and primer must use the existing admission'
        payload=json.loads(req.data) if req.data else None
        state.calls.append((req.full_url,payload,req.data))
        if req.full_url.endswith('/api/version'):return Response(state.version)
        if req.full_url.endswith('/api/show'):
            if state.metadata_error:raise state.metadata_error
            return Response(state.show)
        if req.full_url.endswith('/api/tags'):return Response(state.tags)
        assert req.full_url==ENDPOINT+'/api/generate'
        if payload.get('raw') is True:
            state.primer_seen=True
            if state.primer_error:raise state.primer_error
            return Response({'model':MODEL,'done':True,'response':'IGNORED_PRIMER_PRIVATE_CANARY',
                             'prompt_eval_count':3000,'prompt_eval_cached_count':2996})
        if state.normal_error:raise state.normal_error
        return Response({'model':MODEL,'done':True,'response':'normal checked draft transport result',
                         'prompt_eval_count':5500,'prompt_eval_cached_count':3000,'load_duration':1000000})
    monkeypatch.setattr(module,'backend_slot',slot)
    monkeypatch.setattr(module.request,'urlopen',open_request)
    state.adapter=LocalLLMAdapter(think=False,maximum_output_tokens=1024,maximum_context_tokens=8192,json_schema={'type':'object'})
    return state


def selected(enabled=True):
    return local_runtime_profile(primary_keep_alive='30m',primary_temperature=.2,primary_prefix_cache=enabled)


def normal_calls(state):return [row for row in state.calls if row[1] is not None and 'prompt' in row[1] and not row[1].get('raw')]


def test_enabled_preparation_uses_one_admission_and_unchanged_normal_generate_payload(transport):
    state=transport
    with selected(False):state.adapter.generate_reply(SYSTEM,'Who am I?')
    baseline=normal_calls(state)[0][2]
    state.calls.clear();state.admissions.clear()
    with selected(),local_inference_admission(foreground=False):reply=state.adapter.generate_reply(SYSTEM,'Who am I?')
    assert len(state.admissions)==1 and state.admissions[0][1]['foreground'] is False
    assert [url.rsplit('/',1)[-1] for url,_,_ in state.calls]==['version','show','tags','generate','generate']
    assert len(normal_calls(state))==1 and normal_calls(state)[0][2]==baseline
    primer=state.calls[-2][1]
    assert primer['raw'] is True and primer['think'] is False and primer['options']['num_predict']==1
    assert primer['options']=={'num_ctx':8192,'num_batch':512,'temperature':.2,'num_predict':1}
    assert 'format' not in primer and 'system' not in primer
    assert reply.text=='normal checked draft transport result'
    assert 'IGNORED_PRIMER_PRIVATE_CANARY' not in repr(reply.metadata)
    timing=reply.metadata['local_timing']
    assert timing['prefix_prime_succeeded']==1 and timing['prime_cached_n']==2996 and timing['prime_processed_n']==4
    assert timing['cached_n']==3000 and timing['processed_n']==2500
    assert timing['verification_ms']>=0 and timing['prime_ms']>=0
    assert current_local_runtime_profile()=={}


@pytest.mark.parametrize('mode',['default','old_layout','other_model','other_retention'])
def test_ineligible_calls_have_only_the_ordinary_request(transport,mode):
    state=transport;system=SYSTEM;kwargs={}
    if mode=='old_layout':system=SYSTEM.replace('# Turn Decision Contract\n','')
    if mode=='other_model':kwargs['model_override']='another-model'
    scope=selected() if mode!='default' else local_runtime_profile()
    if mode=='other_retention':scope=local_runtime_profile(primary_prefix_cache=True,primary_keep_alive='5m')
    with scope:state.adapter.generate_reply(system,'Question',**kwargs)
    assert len(state.calls)==1 and len(normal_calls(state))==1 and not state.primer_seen


@pytest.mark.parametrize('failure',['version','metadata','primer'])
def test_optional_preparation_failure_drafts_once_without_replaying(transport,failure):
    state=transport
    if failure=='version':state.version={'version':'0.34.3'}
    if failure=='metadata':state.metadata_error=error.URLError('private metadata transport details')
    if failure=='primer':state.primer_error=TimeoutError('private primer timeout details')
    with selected():reply=state.adapter.generate_reply(SYSTEM,'Question')
    assert len(normal_calls(state))==1 and reply.text=='normal checked draft transport result'
    assert reply.metadata['local_timing']['prefix_prime_succeeded']==0
    assert 'private ' not in repr(reply.metadata)


@pytest.mark.parametrize('exception',[GammaError('stale turn'),OwnershipLost('old identity'),RuntimeError('checkpoint stopped')])
def test_cancellation_after_ignored_primer_keeps_exact_fence_and_never_drafts(transport,exception):
    state=transport
    def checkpoint():
        if state.primer_seen:raise exception
    with selected(),local_inference_admission(foreground=False,checkpoint=checkpoint):
        with pytest.raises(type(exception)) as caught:state.adapter.generate_reply(SYSTEM,'Question')
    assert caught.value is exception and state.primer_seen and normal_calls(state)==[]
    assert not state.active


def test_typed_control_error_from_optional_provider_is_not_swallowed(transport):
    state=transport;exception=GammaError('cancelled')
    state.metadata_error=exception
    with selected():
        with pytest.raises(GammaError) as caught:state.adapter.generate_reply(SYSTEM,'Question')
    assert caught.value is exception and normal_calls(state)==[]


def test_normal_provider_failure_is_not_retried_after_successful_primer(transport):
    state=transport;state.normal_error=error.URLError('ordinary provider failure')
    with selected():
        with pytest.raises(ExternalServiceError):state.adapter.generate_reply(SYSTEM,'Question')
    assert state.primer_seen and len(normal_calls(state))==1


def test_expired_preparation_deadline_does_not_submit_an_optional_request(transport):
    state=transport
    metrics=prepare_private_prefix('<|im_start|>system\n'+STATIC,endpoint=ENDPOINT,timeout_seconds=5,deadline=time.monotonic()-1)
    assert state.calls==[] and metrics['prefix_prime_succeeded']==0


@pytest.mark.parametrize('value',[None,0,1,'true',[],{},float('nan')])
def test_prefix_option_requires_a_real_boolean(value):
    with pytest.raises(ValueError):validate_primary_prefix_cache(value)


def test_prefix_option_is_scoped_and_restored():
    assert current_local_runtime_profile()=={}
    with local_runtime_profile(primary_prefix_cache=True):
        assert current_local_runtime_profile()=={'primary_prefix_cache':True}
        with local_runtime_profile(primary_prefix_cache=False):assert current_local_runtime_profile()=={'primary_prefix_cache':False}
        assert current_local_runtime_profile()=={'primary_prefix_cache':True}
    assert current_local_runtime_profile()=={}


def test_cache_count_and_optional_timing_allowlists_reject_non_numeric_state():
    assert local_cache_counts({'prompt_eval_count':8,'prompt_eval_cached_count':5})=={'cached_n':5,'processed_n':3}
    for total,cached in [(8,True),(8,9),(float('nan'),2),(8,-1),(8,'5')]:
        assert local_cache_counts({'prompt_eval_count':total,'prompt_eval_cached_count':cached})=={}
    actual=allowlisted_local_timing({'verification_ms':2,'prime_ms':3,'prime_cached_n':20,'cached_n':10,
                                    'prefix_prime_succeeded':1,'private_prompt':'SECRET','processed_n':True,'prime_processed_n':float('inf')})
    assert actual=={'verification_ms':2,'prime_ms':3,'prime_cached_n':20,'cached_n':10,'prefix_prime_succeeded':1}


def test_real_configured_core_service_keeps_ignored_primer_out_of_history(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from gamma.coordination.app import configured_app
    from gamma.coordination.inference_profile import release_inference_options
    from gamma.llm import backend_admission
    from pathlib import Path
    from gamma.coordination.personal_models import PrivateTurnDraft
    from gamma.schemas.response import BoundedStateUpdates
    from gamma.safety import llm_reviewer
    root=Path(__file__).resolve().parents[1]
    for key,value in {'project_root':root,'data_dir':tmp_path/'data','database_url':'sqlite:///'+str(tmp_path/'conversation.sqlite3'),
                      'llm_provider':'local','local_llm_model':MODEL,'local_llm_endpoint':ENDPOINT,
                      'local_llm_keep_alive':'0','local_llm_timeout_seconds':20,'local_llm_enable_routing':False,
                      'llm_router_enabled':False,'speech_filter_llm_enabled':True,'speech_filter_llm_model':'gemma4:e4b'}.items():
        monkeypatch.setattr(settings,key,value)
    monkeypatch.setenv('GAMMA_PRACTICE_MODEL',MODEL)
    monkeypatch.setattr(backend_admission,'_coordination_root',lambda:tmp_path/'admission')
    calls=[]
    class Response:
        def __init__(self,value):self.raw=json.dumps(value).encode()
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def read(self,size=None):return self.raw if size is None else self.raw[:size]
    def provider(req,**kwargs):
        payload=json.loads(req.data) if req.data else None
        calls.append((req.full_url,payload))
        if req.full_url.endswith('/api/version'):return Response(VERSION)
        if req.full_url.endswith('/api/show'):return Response(SHOW)
        if req.full_url.endswith('/api/tags'):return Response(TAGS)
        assert req.full_url.endswith('/api/generate')
        if payload['model']=='gemma4:e4b':
            return Response({'response':json.dumps({'action':'allow','reason':'ordinary private greeting','confidence':1})})
        if payload.get('raw') is True:
            assert '# Private Coordination Context' not in payload['prompt']
            assert '# Sourced observations' not in payload['prompt']
            return Response({'model':MODEL,'done':True,'response':'IGNORED_PRIMER_PRIVATE_CANARY',
                             'prompt_eval_count':3000,'prompt_eval_cached_count':2996})
        assert payload['model']==MODEL and payload['think'] is False
        assert '# Private Coordination Context' in payload['system']
        draft=PrivateTurnDraft(action='reply',requested_delivery='text_only',final_text='Neety.',
                               reason_code='technical_fixture',state_updates={'activity_action':'wait'})
        return Response({'response':draft.model_dump_json(),'prompt_eval_count':5500,'prompt_eval_cached_count':3000})
    monkeypatch.setattr(module.request,'urlopen',provider)
    profile={'name':'gamma_warm_local_v1','review_model':'gemma4:e4b'}
    app=configured_app(root,tmp_path/'core',bearer_token='controlled-test-token',run_background=False,
                       identity_lock_root=tmp_path/'locks',inference_profile=profile)
    with TestClient(app,headers={'Authorization':'Bearer controlled-test-token'}) as api:
        owner=api.post('/v1/coordination/personal',json={'audience':'owner_private','request_id':'profile-name',
                       'action':'owner_profile','values':{'name':'Neety','pronunciation':'nee-tee'}})
        assert owner.status_code==200,owner.text
        wake=api.post('/v1/coordination/control',json={'audience':'owner_private','request_id':'wake-test','action':'wake'})
        assert wake.status_code==200,wake.text
        response=api.post('/v1/coordination/input',json={'audience':'owner_private','request_id':'actual-private-test',
                                                       'session_id':'technical','text':'Who am I?'})
        assert response.status_code==200,response.text
        assistant=response.json()['result']['assistant_response']
        assert assistant['display_text']=='Neety.'
        assert assistant['timing_ms']['draft_prefix_prime_succeeded']==1
        assert assistant['timing_ms']['draft_prime_cached_n']==2996
        assert assistant['timing_ms']['draft_cached_n']==3000
        assert 'IGNORED_PRIMER_PRIVATE_CANARY' not in response.text
        with app.state.core.store.transaction(app.state.core.token) as db:
            records=db.execute('SELECT body FROM records').fetchall()
        assert all('IGNORED_PRIMER_PRIVATE_CANARY' not in row[0] for row in records)
    primers=[p for _url,p in calls if p and p.get('raw') is True]
    drafts=[p for _url,p in calls if p and p.get('model')==MODEL and 'system' in p]
    reviews=[p for _url,p in calls if p and p.get('model')=='gemma4:e4b']
    assert len(primers)==len(drafts)==len(reviews)==1
    assert reviews[0]['options']['num_gpu']==32 and reviews[0]['options']['num_thread']==8


def test_startup_optional_prefix_uses_shared_deadline_and_never_reports_its_output(monkeypatch):
    from gamma.coordination.inference_profile import warm_release_inference
    from gamma.llm import backend_admission
    active=[False];calls=[]
    @contextmanager
    def slot(endpoint,**kwargs):
        assert not active[0];active[0]=True
        try:yield
        finally:active[0]=False
    class Response:
        def __init__(self,value):self.raw=json.dumps(value).encode()
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def read(self,size=None):return self.raw if size is None else self.raw[:size]
    def provider(req,**kwargs):
        assert active[0]
        payload=json.loads(req.data) if req.data else None;calls.append((req.full_url,payload,kwargs['timeout']))
        if req.full_url.endswith('/api/version'):return Response(VERSION)
        if req.full_url.endswith('/api/show'):return Response(SHOW)
        if req.full_url.endswith('/api/tags'):return Response(TAGS)
        return Response({'model':payload['model'],'done':True,'response':'STARTUP_IGNORED_CANARY',
                         'prompt_eval_count':3000,'prompt_eval_cached_count':2000})
    monkeypatch.setattr(backend_admission,'backend_slot',slot)
    monkeypatch.setattr(module.request,'urlopen',provider)
    configured=NS(local_llm_endpoint=ENDPOINT,local_llm_model=MODEL,speech_filter_llm_enabled=True,
                  speech_filter_llm_model='gemma4:e4b',local_llm_tagging_model='',local_llm_light_model='')
    report=warm_release_inference({'name':'gamma_warm_local_v1','review_model':'gemma4:e4b'},configured,
                                  maximum_seconds=3,private_prefix=STATIC)
    assert report['status']=='ready' and report['prefix_cache']['status']=='ready'
    assert report['prefix_cache']['prefix_prime_succeeded']==1
    assert report['prefix_cache']['prime_cached_n']==2000
    assert STATIC not in json.dumps(report) and 'STARTUP_IGNORED_CANARY' not in json.dumps(report)
    primers=[p for _url,p,_timeout in calls if p and p.get('raw') is True]
    assert len(primers)==1 and primers[0]['prompt']=='<|im_start|>system\n'+STATIC
    assert primers[0]['options']['num_predict']==1
    assert all(0<timeout<=3 for _url,_p,timeout in calls)
