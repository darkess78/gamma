"""Owner decisions, tentative model proposals and private derivative removal."""
from copy import deepcopy
import json

import pytest

from gamma.coordination.growth import GrowthState
from gamma.coordination.personal import PersonalState, identifier, digest
from gamma.coordination.store import Store, OwnershipLost
from test_growth_adversarial import measurement_fixture


@pytest.fixture
def owner(tmp_path):
    store=Store(tmp_path/'state');token=store.claim(0);clock=[1000.]
    personal=PersonalState(store,token,clock=lambda:clock[0]);growth=GrowthState(store,token,personal,clock=lambda:clock[0])
    def source(label):
        sid=identifier('src','owner-test',label)
        with store.transaction(token) as db:
            store.put(db,'personal_source',sid,{'id':sid,'status':'active','scope':'owner_private',
                'source_kind':'private_conversation','origin_kind':'owner_input','text':label,'assistant_text':'',
                'text_sha256':digest(label),'occurred_at':900.,'ingested_at':900.,'dependencies':{'sources':[],'memories':[]}})
        return sid
    def control(action,values):
        with store.transaction(token) as db:return growth.control(db,action,values)
    def hypothesis(sid,marker='task'):
        experience=control('experience',{'source_ids':[sid],'situation':'A specific observed task question.',
            'options':['Look up receipt','Guess'],'action':'Guessed','expected_outcome':'Checked outcome',
            'observed_outcome':'A missing artifact made the guess unreliable.','uncertainty':'An owner report, not independent truth.'})['experience']
        return control('hypothesis',{'statement':'A receipt lookup might help these task questions.','kind':'judgment',
            'scope_target':'dialogue','scope_marker':marker,'experience_ids':[experience['id']],
            'alternatives':['The question may have been unclear.'],'expected_outcome':'A source-bound checked answer.'})['hypothesis']
    def trial(hypothesis):
        artifacts,frozen,_,_=measurement_fixture(tmp_path/'frozen',artifacts=growth.artifacts,source_ids=hypothesis['source_ids'])
        frozen['source_ids']=hypothesis['source_ids']
        frozen['scope_marker']=hypothesis['scope_marker'];ref=artifacts.put(frozen)
        return control('trial',{'hypothesis_id':hypothesis['id'],'guidance':'Use the current checked receipt.',
            'scope_target':'dialogue','scope_marker':hypothesis['scope_marker'],'expires_seconds':300,
            'review_condition':'Review an independent frozen comparison.','review_rationale':'A narrow observed error.',
            'frozen_ref':ref})['trial']
    return store,token,clock,personal,growth,source,control,hypothesis,trial


def test_model_proposal_only_creates_tentative_sourced_interpretation(owner):
    store,token,clock,personal,growth,source,control,hypothesis,trial=owner
    turn={'id':'one','text':'The fictional task needs its actual checked receipt.','session_id':'test','received_at':1000.}
    values={'statement':'Checking the current receipt might improve task answers.','kind':'judgment','scope_target':'dialogue',
        'scope_marker':'task','alternatives':['Maybe this was an ambiguous question.'],'expected_outcome':'A verified status.'}
    with store.transaction(token) as db:
        personal.receive(db,turn);personal.accept(db,turn,{'display_text':'I will check.'},[],{})
        result=growth.accept_turn(db,turn,{'display_text':'I will check.'},[values,{**values,'auto_accept':True}])
        assert result['hypotheses'][0]['accepted'] is True and result['hypotheses'][1]['accepted'] is False
        state=growth.state(db)
        assert state['hypotheses'][0]['status']=='pending_review'
        assert growth.context(db,scope_text='task')['guidance']==[]
        assert state['trials']==[] and state['evaluations']==[]
        assert personal.identity(db)['fields']['name']=='Shana'


def test_forgotten_current_turn_does_not_rollback_personal_forgetting(owner):
    store,token,clock,personal,growth,*_=owner
    turn={'id':'forgotten','text':'A private report.','session_id':'test','received_at':1000.}
    with store.transaction(token) as db:
        current=personal.receive(db,turn);current['status']='forgotten';current['text']='';store.put(db,'personal_source',current['id'],current)
        result=growth.accept_turn(db,turn,{'display_text':''},[])
        assert result['accepted'] is False
        assert store.get(db,'personal_source',current['id'])['status']=='forgotten'
        assert growth.state(db)['experiences']==[]


def test_owner_revision_preserves_old_interpretation_and_stops_its_trial(owner):
    store,token,clock,personal,growth,source,control,hypothesis,trial=owner
    original=hypothesis(source('First observed failure'));running=trial(original)
    state_before=deepcopy(store.read('growth_experience',original['experience_ids'][0]))
    revised=control('revise',{'hypothesis_id':original['id'],'statement':'Use receipts for task status, not every ordinary reply.',
        'alternatives':['Ordinary conversation may need no task lookup.'],'expected_outcome':'Scoped factual task status.',
        'experience_ids':original['experience_ids']})['hypothesis']
    assert revised['revision']==2 and revised['revises_id']==original['id']
    assert store.read('growth_hypothesis',original['id'])['statement']==original['statement']
    assert store.read('growth_hypothesis',original['id'])['status']=='revised'
    assert store.read('growth_trial',running['id'])['status']=='needs_review'
    assert store.read('growth_experience',original['experience_ids'][0])==state_before
    with store.transaction(token) as db:assert growth.context(db,scope_text='task')['guidance']==[]


def test_work_target_is_descriptive_only_without_a_consumer(owner):
    store,token,clock,personal,growth,source,control,hypothesis,trial=owner
    sid=source('An owner observation');h=hypothesis(sid);t=trial(h)
    with store.transaction(token) as db:
        assert growth.context(db,target='work',scope_text='task')['guidance']==[]
        assert growth.context(db,target='dialogue',scope_text='unrelated hello')['guidance']==[]
        assert growth.context(db,target='dialogue',scope_text='TASK result')['guidance'][0]['trial_id']==t['id']
    with pytest.raises(ValueError):control('trial',{'hypothesis_id':h['id'],'guidance':'Do work','scope_target':'work',
        'scope_marker':'task','expires_seconds':90,'review_condition':'review','review_rationale':'owner','frozen_ref':t['frozen_ref']})


def test_untrusted_shared_source_cannot_be_growth_experience(owner):
    store,token,clock,personal,growth,source,control,*_=owner
    sid=source('A source')
    with store.transaction(token) as db:
        current=store.get(db,'personal_source',sid);current['scope']='public';store.put(db,'personal_source',sid,current)
    with pytest.raises(ValueError):control('experience',{'source_ids':[sid],'situation':'s','options':[],
        'action':'a','expected_outcome':'e','observed_outcome':'o','uncertainty':'reported'})


def test_owner_generation_is_required_for_growth_mutation(owner):
    store,token,clock,personal,growth,source,control,hypothesis,trial=owner
    h=hypothesis(source('An observed failure'));t=trial(h)
    store.release(token);store.claim(token[0])
    with store.transaction() as db:
        with pytest.raises(OwnershipLost):growth.control(db,'withdraw',{'trial_id':t['id'],'version':t['version'],'reason':'old owner'})


def test_same_support_can_also_be_reinterpreted_as_counterevidence(owner):
    store,token,clock,personal,growth,source,control,hypothesis,trial=owner
    h=hypothesis(source('A complicated observation'));t=trial(h)
    control('contradict',{'hypothesis_id':h['id'],'experience_id':h['experience_ids'][0],'note':'This same example does not support the broad claim.'})
    with store.transaction(token) as db:
        state=growth.state(db)
        assert state['hypotheses'][0]['status']=='needs_review'
        assert state['trials'][0]['status']=='needs_review'
        assert state['experiences'][0]['status']=='active'


def test_frozen_cases_never_enter_model_context(owner,tmp_path):
    store,token,clock,personal,growth,source,control,hypothesis,trial=owner
    h=hypothesis(source('A factual task observation'));t=trial(h)
    with store.transaction(token) as db:
        context=json.dumps(growth.context(db,scope_text='task'))
        assert 'case-a' not in context and 'historical development question' not in context
        assert 'expected_sha256' not in context and 'candidate_ref' not in context


@pytest.mark.parametrize('field,value',[('marker',' '*1000+'task'),('guidance',' '*1200+'x'),
    ('review_condition',' '*500+'x'),('review_rationale',' '*500+'x')])
def test_trial_bounds_count_untrimmed_payload(field,value):
    from gamma.coordination.growth_models import validate_scope
    scope={'target':'dialogue','marker':'task','guidance':'Use the actual task receipt.',
        'expires_seconds':300,'review_condition':'Independent comparison','review_rationale':'A scoped check'}
    scope[field]=value
    with pytest.raises(ValueError):validate_scope(scope)
