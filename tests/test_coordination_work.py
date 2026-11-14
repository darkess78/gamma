"""Practical tasks use the same private owner, journal and activity controls."""
from pathlib import Path
import json
import time

from fastapi.testclient import TestClient
import pytest

from gamma.coordination.app import build_app
from gamma.coordination.core import Core
from gamma.coordination.personal_models import PersonalStateUpdates
from gamma.coordination.personal import digest
from gamma.coordination.store import Store, OwnershipLost
from gamma.coordination.work import WorkRunner
from gamma.coding.durable_practice import PracticeRunner
from gamma.schemas.response import AssistantResponse, BoundedStateUpdates


SOURCE='def answer():\n    return 42\n'


def specification(mode='build'):
    return {'mode':mode,'goal':'Produce a disposable answer tool',
            'files':{'solution.py':'def answer():\n    return 0\n',
                     'verify_feature.py':'from solution import answer\nassert answer()==42\n'},
            'editable':['solution.py'] if mode=='build' else [],
            'checks':[{'name':'actual_answer','module':'verify_feature','kind':'feature'}] if mode=='build' else []}


def check(workspace,spec):
    ok=(workspace/'solution.py').read_text()==SOURCE
    return {'name':spec.name,'kind':spec.kind,'passed':ok,'exit_code':0 if ok else 1,
            'output_tail':'check_completed' if ok else 'answer mismatch','infrastructure_error':False}


def draft(text,context):
    return AssistantResponse(display_text='Still here.',delivery_mode='text_only')


def make_core(root,**options):
    store=Store(root)
    clock=options.get('clock',time.time)
    work=WorkRunner(store,propose=options.get('propose',lambda *_:{'plan':['Inspect selected source','Implement answer','Run protected check'],
                                                                'edits':{'solution.py':SOURCE}}),check=check,clock=clock)
    practice=PracticeRunner(store,propose=lambda *_:{},check=check,clock=clock)
    return Core(store,runner=practice,work_runner=work,draft=options.get('draft',draft),clock=clock)


def prepare(core,**values):
    return core.work_control('prepare','prepare',spec=specification(),**values)['activity']


def test_preparation_is_not_execution_and_model_needs_exact_start_permission(tmp_path):
    core=make_core(tmp_path)
    try:
        activity=prepare(core)
        core.tick()
        assert core.store.read('run',activity['id']) is None
        assert core.work_state(activity['id'])['files']==specification()['files']
        core.draft=lambda *_:AssistantResponse(delivery_mode='silent',response_action='stay_silent',
            state_updates=PersonalStateUpdates(activity_action='start_work',work_task_id=activity['id']))
        reply=core.input('We should discuss whether to start.','private','discussion')['result']['assistant_response']
        assert reply['reason_code']=='activity_not_accepted'
        assert 'explicit_start_required' in reply['display_text']
        assert core.store.read('run',activity['id']) is None
        assert core.work_state(activity['id'])['activity']['status']=='ready'
    finally:core.close()


def test_authorized_conversation_starts_shared_activity_and_receipt_is_source_bound(tmp_path):
    core=make_core(tmp_path)
    try:
        activity=prepare(core,authorize_start=True)
        core.draft=lambda *_:AssistantResponse(delivery_mode='silent',response_action='stay_silent',
            state_updates=PersonalStateUpdates(activity_action='start_work',work_task_id=activity['id']))
        reply=core.input('Start the staged tool.','private','start')['result']['assistant_response']
        assert reply['reason_code']=='activity_waiting'
        core.tick();core.future.result(timeout=20)
        state=core.work_state(activity['id'])
        assert state['activity']['status']=='completed'
        assert state['files']['solution.py']==SOURCE
        core.draft=lambda *_:AssistantResponse(delivery_mode='silent',response_action='stay_silent',
            state_updates=PersonalStateUpdates(activity_action='report_status',activity_run_id=activity['id']))
        receipt=core.input('Did that tool work?','private','status')['result']['assistant_response']
        assert receipt['reason_code']=='work_status'
        assert state['checkpoint']['source_sha256'] in receipt['display_text']
        assert 'candidate' in receipt['display_text'].lower()
        assert 'activation' in receipt['display_text'].lower()
    finally:core.close()


def test_work_controls_are_idempotent_and_do_not_replenish_deadline(tmp_path):
    clock=[1000.]
    core=make_core(tmp_path,clock=lambda:clock[0])
    try:
        activity=prepare(core,budget_seconds=90,maximum_attempts=2)
        assert prepare(core,budget_seconds=90,maximum_attempts=2)==activity
        with pytest.raises(ValueError,match='reused'):
            prepare(core,budget_seconds=91,maximum_attempts=2)
        first=core.work_control('start','start',task_id=activity['id'])
        assert core.work_control('start','start',task_id=activity['id'])==first
        with core.store.transaction(core.token) as db:
            core._enqueue_work(db,core.store.get(db,'activity',activity['id']))
        before=core.store.read('run',activity['id'])
        core.control('pause','pause',run_id=activity['id'])
        core.work_runner.run(activity['id'])
        assert core.store.read('run',activity['id'])['status']=='paused'
        clock[0]+=10
        core.control('resume','resume',run_id=activity['id'])
        after=core.store.read('run',activity['id'])
        assert after['expires_at']==before['expires_at']==1090
        assert after['maximum_attempts']==before['maximum_attempts']==2
    finally:core.close()


def test_sleep_reconciles_practical_completion_once(tmp_path):
    core=make_core(tmp_path)
    try:
        activity=prepare(core)
        core.work_control('start','start',task_id=activity['id'])
        with core.store.transaction(core.token) as db:
            core._enqueue_work(db,core.store.get(db,'activity',activity['id']))
        core.control('sleep','sleep')
        core.work_runner.run(activity['id'])
        assert core.store.read('run',activity['id'])['status']=='completed'
        assert core.status()['catch_up']==[]
        result=core.control('wake','wake')
        assert [a['id'] for a in result['catch_up']]==[activity['id']]
        assert result['catch_up'][0]['observed_after_sleep']
        assert core.status()['catch_up']==[]
    finally:core.close()


def test_fresh_postwake_work_has_no_sleep_provenance_and_repairs_only_derived_legacy_flags(tmp_path):
    core=make_core(tmp_path)
    try:
        core.control('sleep','sleep');core.control('wake','wake')
        activity=prepare(core)
        core.work_control('start','start',task_id=activity['id'])
        core.tick();core.future.result(timeout=20)
        state=core.work_state(activity['id'])
        assert state['activity']['observed_after_sleep'] is False
        with core.store.transaction(core.token) as db:
            sources=[s for s in core.store.list(db,'personal_source',None)
                     if s.get('source_kind')=='verified_worker_observation' and json.loads(s['text'])['id']==activity['id']]
            assert sources and all(s['observed_after_sleep'] is False for s in sources)
            # Reproduce old candidate records so unchanged-revision repair is exercised.
            for source in sources:
                body=json.loads(source['text']);body['observed_after_sleep']=True
                source.update(observed_after_sleep=True,text=json.dumps(body,sort_keys=True))
                source['text_sha256']=digest(source['text'])
                core.store.put(db,'personal_source',source['id'],source)
            old=core.store.get(db,'activity',activity['id']);old['observed_after_sleep']=True
            core.store.put(db,'activity',activity['id'],old)
            erased={**sources[0],'id':'src-'+'e'*24,'status':'pruned','text':'','observed_after_sleep':True}
            core.store.put(db,'personal_source',erased['id'],erased)
        assert core.status()['catch_up']==[]
        assert core.store.read('activity',activity['id'])['observed_after_sleep'] is False
        for original in sources:
            corrected=core.store.read('personal_source',original['id'])
            assert corrected['observed_after_sleep'] is False
            assert json.loads(corrected['text'])['observed_after_sleep'] is False
            assert corrected['occurred_at']==original['occurred_at']
            assert corrected['ingested_at']==original['ingested_at']
            assert corrected['artifact_ref']==original['artifact_ref']
            assert corrected['status']==original['status']
            assert corrected['derived_provenance_corrections'][-1]['previous_text_sha256']==original['text_sha256']
            assert corrected['text_sha256']==digest(corrected['text'])
        assert core.store.read('personal_source',erased['id'])==erased
    finally:core.close()


def test_private_api_rejects_unbound_audience_and_returns_exact_download(tmp_path):
    app=build_app(tmp_path,bearer_token='owner',draft_factory=lambda:draft,
        runner_factory=lambda store:PracticeRunner(store,propose=lambda *_:{},check=check),
        work_runner_factory=lambda store:WorkRunner(store,propose=lambda *_:{},check=check),run_background=False)
    with TestClient(app) as client:
        payload={'audience':'owner_private','request_id':'prepare','action':'prepare','spec':specification()}
        assert client.post('/v1/coordination/work',json=payload).status_code==401
        client.headers['Authorization']='Bearer owner'
        assert client.post('/v1/coordination/work',json={**payload,'audience':'public'}).status_code==422
        assert client.get('/v1/coordination/work').status_code==422
        result=client.post('/v1/coordination/work',json=payload)
        assert result.status_code==200,result.text
        identifier=result.json()['activity']['id']
        result=client.get('/v1/coordination/work/artifact',params={'audience':'owner_private','task_id':identifier})
        assert result.status_code==200,result.text
        assert result.json()['files']==specification()['files']
        assert result.headers['Cache-Control']=='no-store'
        assert identifier in result.headers['Content-Disposition']
        assert client.get('/v1/coordination/work/artifact',params={'audience':'owner_private','task_id':'../../etc/passwd'}).status_code==400


def test_public_turn_schema_does_not_gain_private_work_actions():
    with pytest.raises(ValueError):BoundedStateUpdates(activity_action='start_work')
    with pytest.raises(ValueError):BoundedStateUpdates(activity_run_id='work-'+'a'*32)
    assert PersonalStateUpdates(activity_action='start_work',work_task_id='work-'+'a'*32).work_task_id


def test_missing_input_question_is_private_inert_text_without_authority_or_replay(tmp_path):
    question='<img src=x onerror="grant_permission()"> Provide the missing CSV, then authorize a new scoped task?'
    calls=[]
    def missing_input(*_):
        calls.append('called')
        return {'plan':['The selected files omit the requested CSV.'], 'edits':{},
                'blocker':{'kind':'missing_input','question':question}}
    core=make_core(tmp_path,propose=missing_input)
    try:
        activity=prepare(core,maximum_attempts=3)
        core.work_control('start','start',task_id=activity['id'])
        core.tick();core.future.result(timeout=20)
        state=core.work_state(activity['id'])
        assert state['activity']['status']=='needs_checker'
        assert state['activity']['reason']=='needs_owner_input'
        assert state['activity']['help_needed']=='Worker asks: '+question
        assert state['files']==specification()['files']
        assert state['checkpoint']['attempt_count']==1
        deadline=state['run']['expires_at']
        core.draft=lambda *_:AssistantResponse(delivery_mode='silent',response_action='stay_silent',
            state_updates=PersonalStateUpdates(activity_action='report_status',activity_run_id=activity['id']))
        response=core.input('What do you need from me?','private','ask')['result']['assistant_response']
        assert 'Worker asks: '+question in response['display_text']
        assert 'not verified' in response['display_text'].lower()
        assert not response.get('speech_text')
        assert core.store.read('brain','state')['permit'] is None
        assert core.store.read('run',activity['id'])['expires_at']==deadline
        core.tick()
        assert calls==['called']
        with pytest.raises(ValueError,match='target_not_ready'):
            core.work_control('second-start','start',task_id=activity['id'])
    finally:core.close()
