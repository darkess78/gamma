"""Growth effects use the existing private owner, accepted sources and activity path."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
import pytest

from gamma.coordination.app import build_app
from gamma.coordination.personal_models import PersonalStateUpdates
from gamma.coordination.store import Store,OwnershipLost
from gamma.coordination.opportunities import validate_offer
from gamma.schemas.response import AssistantResponse,BoundedStateUpdates
from gamma.coding.durable_practice import PracticeRunner
from test_coordination_work import make_core,specification,check,draft


def frozen(core,now):
    return core.growth.artifacts.put({'kind':'growth_frozen_cases','format':1,'component':'dialogue_guidance',
        'model':'fictional:model','source_sha256':'1'*64,'checker_sha256':'2'*64,'frozen_at':now,
        'source_ids':[core.store.read('turn','source')['personal_receipt']['source_id']],
        'scope_target':'dialogue','scope_marker':'task','development_families':['observed-development'],
        'development_input_sha256':['3'*64],'evaluation_families':['new-family'],
        'cases':[{'id':'new-case','family':'new-family','input_sha256':'4'*64,'expected_sha256':'5'*64}]})


def start_trial(core,now=1000.):
    core.input('The task worker needs a missing input.','private','source')
    sid=core.store.read('turn','source')['personal_receipt']['source_id']
    experience=core.growth_control('experience','experience',{'source_ids':[sid],'situation':'A task lacks its file.',
        'options':['invent data','ask for file'],'action':'ask','expected_outcome':'Receive the real file',
        'observed_outcome':'The checked worker asks for the CSV.','uncertainty':'One observed task only.'})['experience']
    hypothesis=core.growth_control('hyp','hypothesis',{'statement':'Task help should read the exact checked receipt.',
        'kind':'judgment','scope_target':'dialogue','scope_marker':'task','experience_ids':[experience['id']],
        'alternatives':['The old answer might suffice in other contexts.'],'expected_outcome':'Accurate current worker questions.'})['hypothesis']
    trial=core.growth_control('trial','trial',{'hypothesis_id':hypothesis['id'],'guidance':'SCOPED_TRIAL_TEXT Use the exact checked task status.',
        'scope_target':'dialogue','scope_marker':'task','expires_seconds':30,'review_condition':'Inspect separate new cases.',
        'review_rationale':'A source-bound experiment, not a general personality change.','frozen_ref':frozen(core,now)})['trial']
    return trial,sid


def test_trial_context_is_scoped_and_withdrawal_strips_guided_working_notes(tmp_path):
    clock=[1000.];core=make_core(tmp_path,clock=lambda:clock[0])
    try:
        trial,_=start_trial(core)
        observed=[]
        def respond(text,context):
            observed.append(json.loads(context))
            return AssistantResponse(display_text='Here is the checked answer.',delivery_mode='text_only',
                state_updates=PersonalStateUpdates(active_topic='SCOPED_TRIAL_TEXT copied into a working note'))
        core.draft=respond
        core.input('Which task needs help?','private','guided')
        assert observed[-1]['growth']['guidance'][0]['trial_id']==trial['id']
        with core.store.transaction(core.token) as db:
            scoped=json.loads(core._context(db,'other','Which task needs help?'))
            assert 'active_topic' in scoped['working_notes']
            for query in ('How is the weather?',''):
                unrelated=json.loads(core._context(db,'other',query))
                assert unrelated['growth']['guidance']==[]
                assert 'active_topic' not in unrelated['working_notes']
        core.growth_control('withdraw','withdraw',{'trial_id':trial['id'],'version':trial['version'],'reason':'The trial is over.'})
        with core.store.transaction(core.token) as db:
            context=json.loads(core._context(db,'other','Which task needs help?'))
            assert context['growth']['guidance']==[]
            assert 'active_topic' not in context['working_notes']
            assert core.store.get(db,'brain','state')['working_notes']['active_topic'].startswith('SCOPED_TRIAL_TEXT')
        assert core.store.read('turn','guided')['status']=='completed'
    finally:core.close()


def test_expiry_during_inference_rejects_late_trial_response_without_effects(tmp_path):
    clock=[1000.];core=make_core(tmp_path,clock=lambda:clock[0]);entered=threading.Event();release=threading.Event()
    try:
        start_trial(core)
        def respond(*_):
            entered.set();assert release.wait(10)
            return AssistantResponse(display_text='STALE_GUIDED_REPLY',delivery_mode='text_only',
                state_updates=PersonalStateUpdates(current_objective='stale note'))
        core.draft=respond
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(core.input,'Which task?','private','inflight');assert entered.wait(5)
            clock[0]=1031.;release.set();reply=future.result(timeout=10)
        assert reply['result']['assistant_response']['reason_code']=='growth_guidance_changed'
        assert core.store.read('turn','inflight')['status']=='interrupted'
        assert core.store.read('brain','state')['working_notes'].get('current_objective')!='stale note'
    finally:release.set();core.close()


def test_source_forget_invalidates_guidance_and_dependent_turn_without_resurrection(tmp_path):
    core=make_core(tmp_path,clock=lambda:1000.)
    try:
        trial,sid=start_trial(core)
        core.input('What about the task?','private','derived')
        core.personal_control('forget-source','forget',{'target_id':sid})
        # Erasure completes in the owner-control transaction, before any growth read.
        assert core.store.read('growth_trial',trial['id'])['status']=='source_removed'
        state=core.growth_state()
        assert not state['active_trial_refs']
        assert next(t for t in state['trials'] if t['id']==trial['id'])['status']=='source_removed'
        assert core.store.read('turn','derived')['redacted'] is True
    finally:core.close()


def prepare(core,key,goal):
    spec=specification('research');spec['goal']=goal
    return core.work_control(key,'prepare',spec=spec,authorize_start=True,budget_seconds=600)['activity']['id']


def test_offered_file_observations_reach_context_and_bind_private_turn_sources(tmp_path):
    core=make_core(tmp_path,clock=lambda:1000.)
    try:
        notes=['A_SOURCE_CANARY triangle has sides three, four and five.',
               'B_SOURCE_CANARY the old man the boats has a verb reading.']
        tasks=[]
        for index,note in enumerate(notes):
            tasks.append(core.work_control('preview'+str(index),'prepare',spec={
                'mode':'research','goal':'Inspect distinct selected example '+str(index),
                'files':{'note.txt':note},'editable':[],'checks':[]},
                authorize_start=True,budget_seconds=600)['activity']['id'])
        core.growth_control('preview-offer','opportunities',{'task_ids':tasks,'budget_seconds':300,'maximum_reviews':1})
        seen=[]
        def respond(text,context):
            seen.append(json.loads(context))
            return AssistantResponse(display_text='A_SOURCE_CANARY is a sourced observation.',delivery_mode='text_only')
        core.draft=respond
        core.input('What is available?','preview-private','preview-derived')
        offered=seen[-1]['exploration']['tasks']
        assert [t['id'] for t in offered]==tasks
        for task,note in zip(offered,notes):
            preview=task['source_observations']
            assert any(f['path']=='note.txt' and f['excerpt']==note and not f['excerpt_is_partial'] for f in preview['files'])
            assert preview['source_ids']
        source_ids=core._observation_sources(seen[-1])
        derived=core.store.read('turn','preview-derived')['personal_receipt']['source_id']
        assert set(source_ids)<=set(core.store.read('personal_source',derived)['dependencies']['sources'])
        target=offered[0]['source_observations']['source_ids'][0]
        core.personal_control('forget-preview','forget',{'target_id':target})
        assert core.store.read('turn','preview-derived')['redacted']
        with core.store.transaction(core.token) as db:
            after=core._context(db,'other','What is available?')
            assert 'A_SOURCE_CANARY' not in after
            assert 'B_SOURCE_CANARY' in after
        assert core.growth.artifacts.get(core.store.read('activity',tasks[0])['initial_checkpoint_ref'])['spec']['files']['note.txt']==notes[0]
    finally:core.close()


def test_quiet_choice_binds_visible_file_sources_and_redacts_derived_copy_on_forget(tmp_path):
    core=make_core(tmp_path,clock=lambda:1000.)
    try:
        tasks=[]
        for index in range(2):
            tasks.append(core.work_control('quiet-preview'+str(index),'prepare',spec={
                'mode':'research','goal':'Distinct bounded quiet example '+str(index),
                'files':{'note.txt':'QUIET_SOURCE_CANARY_'+str(index)},'editable':[],'checks':[]},
                authorize_start=True,budget_seconds=600)['activity']['id'])
        core.growth_control('quiet-preview-offer','opportunities',{'task_ids':tasks,'budget_seconds':300,'maximum_reviews':1})
        seen=[]
        def respond(text,context):
            seen.append(json.loads(context))
            return AssistantResponse(delivery_mode='silent',response_action='stay_silent',
                reason_code='QUIET_SOURCE_CANARY_0',state_updates=PersonalStateUpdates(activity_action='wait'))
        core.draft=respond;core.review()
        source_ids=core._observation_sources(seen[-1])
        choice=core.growth_state()['choices'][0]
        assert choice['accepted'] and choice['action']=='wait'
        assert set(source_ids)==set(choice['source_ids'])
        target=seen[-1]['exploration']['tasks'][0]['source_observations']['source_ids'][0]
        core.personal_control('forget-quiet-preview','forget',{'target_id':target})
        erased=core.store.read('growth_choice',choice['id'])
        assert 'QUIET_SOURCE_CANARY' not in json.dumps(erased)
        assert erased['accepted'] and erased['action']=='wait'
    finally:core.close()


def test_quiet_exploration_dispatches_actual_p3_work_once_and_records_wait(tmp_path):
    clock=[1000.];core=make_core(tmp_path,clock=lambda:clock[0])
    try:
        core.work_runner.propose=lambda *_:{'plan':['Inspect the selected source.'],'edits':{}}
        one=prepare(core,'one','Explore a literary scene.');two=prepare(core,'two','Inspect a file layout.')
        offer=core.growth_control('offer','opportunities',{'task_ids':[one,two],'budget_seconds':300,'maximum_reviews':2})
        assert core.growth_control('offer','opportunities',{'task_ids':[one,two],'budget_seconds':300,'maximum_reviews':2})==offer
        with pytest.raises(ValueError,match='reused'):core.growth_control('offer','opportunities',{'task_ids':[two,one]})
        core.draft=lambda *_:AssistantResponse(delivery_mode='silent',response_action='stay_silent',
            state_updates=PersonalStateUpdates(activity_action='start_work',work_task_id=one))
        core.review()
        activity=core.store.read('activity',one)
        assert activity['status']=='waiting' and activity['origin']=='self_exploration' and not activity['owner_commitment']
        core.tick();core.future.result(timeout=15)
        assert core.store.read('run',one)['status']=='completed'
        core.growth_control('review','review',{})
        core.draft=lambda *_:AssistantResponse(delivery_mode='silent',response_action='stay_silent',
            state_updates=PersonalStateUpdates(activity_action='wait'))
        core.review();core.review()
        state=core.growth_state()
        assert len(state['choices'])==2 and state['exploration']['remaining_reviews']==0
        assert core.store.read('run',two) is None
    finally:core.close()


def test_offer_cannot_claim_unrelated_or_owner_work_and_does_not_require_unrelated_ready_state(tmp_path):
    core=make_core(tmp_path,clock=lambda:1000.)
    try:
        one=prepare(core,'one','Explore a literary scene.');two=prepare(core,'two','Inspect a file layout.')
        old=prepare(core,'old','An unrelated owner task.')
        with core.store.transaction(core.token) as db:
            a=core.store.get(db,'activity',old);a['status']='completed';core.store.put(db,'activity',old,a)
        core.growth_control('offer','opportunities',{'task_ids':[one,two]})
        core.draft=lambda *_:AssistantResponse(delivery_mode='silent',response_action='stay_silent',
            state_updates=PersonalStateUpdates(activity_action='start_work',work_task_id=old))
        core.review()
        assert not core.growth_state()['choices'][0]['accepted']
        assert core.store.read('run',old) is None
    finally:core.close()


def test_quiet_start_cannot_reclassify_an_owner_paused_exploration_commitment(tmp_path):
    core=make_core(tmp_path,clock=lambda:1000.)
    try:
        one=prepare(core,'one','Explore a literary scene.');two=prepare(core,'two','Inspect a file layout.')
        core.growth_control('offer','opportunities',{'task_ids':[one,two]})
        core.draft=lambda *_:AssistantResponse(delivery_mode='silent',response_action='stay_silent',
            state_updates=PersonalStateUpdates(activity_action='start_work',work_task_id=one))
        core.review();core.control('owner-pause','pause',run_id=one)
        before=core.store.read('activity',one)
        assert before['owner_commitment'] is True and before['status']=='paused'
        core.growth_control('review','review',{});core.review()
        after=core.store.read('activity',one)
        assert after['owner_commitment'] is True and after['status']=='paused'
        assert not core.growth_state()['choices'][0]['accepted']
    finally:core.close()


def test_private_growth_route_requires_explicit_audience_and_no_store(tmp_path):
    app=build_app(tmp_path,bearer_token='owner',draft_factory=lambda:draft,
        runner_factory=lambda store:PracticeRunner(store,propose=lambda *_:{},check=check),run_background=False)
    with TestClient(app) as client:
        assert client.get('/v1/coordination/growth?audience=owner_private').status_code==401
        client.headers['Authorization']='Bearer owner'
        assert client.get('/v1/coordination/growth?audience=public').status_code==422
        response=client.get('/v1/coordination/growth?audience=owner_private')
        assert response.status_code==200 and response.headers['Cache-Control']=='no-store'
        assert response.json()['runtime_trial_targets']==['dialogue']


def test_public_contract_does_not_accept_growth_proposal_authority():
    assert 'growth_proposals' not in BoundedStateUpdates(growth_proposals=[]).model_dump()
    with pytest.raises(ValueError):PersonalStateUpdates(growth_proposals=[{'statement':'x','kind':'interest','scope_marker':'x','authority':'approve'}])
