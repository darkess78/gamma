"""Fictional state and real SQLite transactions; no production memory fixtures."""
from concurrent.futures import ThreadPoolExecutor
import json
import shutil
import threading

import pytest

from gamma.coordination.core import Core
from gamma.coordination.store import Store
from gamma.coordination.personal import identifier
from gamma.coordination.personal_models import PersonalStateUpdates
from gamma.schemas.response import AssistantResponse


class Runner:
    pass


def make(root, draft=None, clock=lambda:100):
    return Core(Store(root),runner=Runner(),clock=clock,draft=draft or
        (lambda *_:AssistantResponse(display_text='Acknowledged.',delivery_mode='text_only')))


def remember(core,text='The workshop drink is jasmine tea.',request='one',**kw):
    return core.personal_control(request,'memory',dict(op='remember',kind='preference',subject_id='owner',
        topic='workshop drink',text=text,**kw))['id']


def test_corrections_partial_source_and_restart(tmp_path):
    core=make(tmp_path)
    mid=remember(core)
    core.personal_control('two','memory',dict(op='correct',target_id=mid,kind='preference',text='The workshop drink is mint tea.'))
    with core.store.transaction() as db:
        recall=core.personal.recall(db,'workshop drink tea')
        assert recall[0]['id']==mid
        assert 'mint tea' in recall[0]['text'] and 'superseded' in recall[0]['text']
        assert recall[0]['provenance']['history_source_ids']
    core.close()
    core=make(tmp_path)
    try:
        assert core.personal_state()['records'][0]['id']==mid
        core.personal_control('prune','policy',{'text_days':0})
        with core.store.transaction() as db:
            recalled=core.personal.recall(db,'workshop drink')
            assert recalled and recalled[0]['certainty']=='authored'
    finally: core.close()


def test_forget_transitive_output_and_p1_projection(tmp_path):
    sentinel='amber-orchid-9125'
    core=make(tmp_path,draft=lambda *_:AssistantResponse(display_text='I recall '+sentinel,delivery_mode='text_only'))
    mid=remember(core,sentinel+' is the workshop drink.')
    core.input('What workshop drink?', 'fiction','asked')
    result=core.personal_control('forget','forget',{'target_id':mid})
    assert result['redacted_sources']>=2
    with core.store.transaction() as db:
        assert core.personal.recall(db,'workshop drink')==[]
        assert sentinel not in core._context(db,'fiction','drink')
        assert sentinel not in json.dumps(core.store.list(db,'turn',None))
        assert core.store.get(db,'turn','asked')['redacted']
    core.close()
    core=make(tmp_path)
    try:
        assert sentinel not in json.dumps(core.personal_state())
    finally: core.close()


def test_admin_invalidation_withholds_stale_personal_write(tmp_path):
    entered,release=threading.Event(),threading.Event()
    def blocked(*_):
        entered.set(); assert release.wait(10)
        return AssistantResponse(display_text='stale-secret',delivery_mode='text_only',
            state_updates=PersonalStateUpdates(personal_changes=[dict(op='remember',kind='fact',subject_id='owner',
                topic='stale',text='stale-secret')]))
    core=make(tmp_path,draft=blocked)
    with ThreadPoolExecutor() as pool:
        pending=pool.submit(core.input,'hello','f','blocked')
        assert entered.wait(5)
        core.personal_control('edit','identity',{'fields':{'speech_style':'Brief, concrete sentences.'}})
        release.set()
        assert pending.result()['result']['assistant_response']['reason_code']=='superseded'
    assert not core.personal_state()['records']
    assert core.store.read('personal_source',identifier('src','turn','blocked'))['status']=='pending'
    core.close()


def test_person_account_mapping_independent_of_names_and_affection(tmp_path):
    core=make(tmp_path)
    try:
        a=core.personal_control('a','person',{'name':'Mira','relationship':'Distrusts her judgment.'})['person_id']
        b=core.personal_control('b','person',{'name':'Mira','relationship':'A new acquaintance.'})['person_id']
        values={'person_id':a,'platform':'discord','platform_user_id':'123','confirmed':True}
        assert core.personal_control('link','link',values)['grants_authority'] is False
        with pytest.raises(ValueError,match='already_bound'):
            core.personal_control('wrong','link',dict(values,person_id=b))
        with pytest.raises(ValueError,match='confirmation'):
            core.personal_control('guess','link',dict(values,platform_user_id='456',confirmed=False))
        assert core.personal_state()['people'][1]['interaction_count']==0
        core.personal_control('unlink','unlink',values)
        core.personal_control('relink','link',dict(values,person_id=b))
    finally: core.close()


def test_sleep_queue_restart_deduplicate_and_order(tmp_path):
    calls=[]
    clock=[100]
    def draft(text,context):
        calls.append(text)
        return AssistantResponse(display_text='heard '+text,delivery_mode='text_only')
    core=make(tmp_path,draft=draft,clock=lambda:clock[0])
    core.control('sleep','sleep')
    for rid,text in [('a','first'),('a','first'),('b','second')]:
        core.input(text,'f',rid); clock[0]+=1
    assert not calls
    core.close()
    core=make(tmp_path,draft=draft,clock=lambda:clock[0])
    core.control('wake','wake')
    core.input('third','f','c')
    assert calls==['first','second','third']
    core.input('first','f','a')
    assert calls==['first','second','third']
    core.close()


def test_copied_identity_has_only_one_owner_before_writes(tmp_path):
    core=make(tmp_path/'one')
    shutil.copytree(tmp_path/'one',tmp_path/'two')
    generation=Store(tmp_path/'two').read('brain','state')['generation']
    with pytest.raises(OSError): make(tmp_path/'two')
    assert Store(tmp_path/'two').read('brain','state')['generation']==generation
    core.close()
    other=make(tmp_path/'two'); other.close()


def test_mood_settles_without_changing_work_or_authored_personality(tmp_path):
    clock=[100]
    core=make(tmp_path,clock=lambda:clock[0],draft=lambda *_:AssistantResponse(display_text='Fine.',delivery_mode='text_only',
        state_updates=PersonalStateUpdates(emotion='annoyed',personal_changes=[dict(op='remember',kind='preference',
            subject_id='self',topic='tea',text='I hate tea forever.')])) )
    core.input('Hello','f','mood')
    assert core.personal_state()['records'][0]['status']=='pending'
    before=core.personal_state()['identity']
    clock[0]+=1900
    assert core.status()['mood']=='neutral'
    assert core.personal_state()['identity']==before
    core.close()


def test_model_forget_does_not_restore_current_turn_and_derived_audit(tmp_path):
    core=make(tmp_path)
    sentinel='cobalt-meadow-3741'
    mid=remember(core,sentinel+' workshop drink')
    core.draft=lambda *_:AssistantResponse(display_text='I forgot '+sentinel,delivery_mode='text_only',
        state_updates=PersonalStateUpdates(personal_changes=[dict(op='forget',target_id=mid)]))
    response=core.input('Forget the '+sentinel+' workshop drink.','f','forget-turn')
    assert sentinel not in json.dumps(response)
    assert core.store.read('turn','forget-turn')['redacted']
    core.personal_control('identity-a','identity',{'fields':{'speech_style':sentinel}})
    source=core.personal_state()['identity']['field_sources']['speech_style']
    core.personal_control('identity-b','identity',{'fields':{'likes':['Competence']}})
    core.personal_control('delete-a','delete',{'target_id':source})
    assert sentinel not in json.dumps(core.personal_state())
    core.close()


def test_accepted_memory_survives_raw_retention_with_honest_partial_source(tmp_path):
    core=make(tmp_path,draft=lambda *_:AssistantResponse(display_text='I heard that.',delivery_mode='text_only',
        state_updates=PersonalStateUpdates(personal_changes=[dict(op='remember',kind='decision',subject_id='owner',
            topic='bicycle repair',text='Repair the bicycle before buying another.')])) )
    core.input('Repair my bicycle first.','fiction','repair')
    core.personal_control('raw-policy','policy',{'text_days':0})
    with core.store.transaction() as db:
        recall=core.personal.recall(db,'bicycle repair')
        assert recall[0]['text']=='Repair the bicycle before buying another.'
        assert recall[0]['provenance']['exact_source_retained'] is False
        assert 'Repair my bicycle first' not in json.dumps(core.store.list(db,'turn',None))
    core.close()


def test_failure_rolls_back_turn_acceptance_source_and_revision(tmp_path,monkeypatch):
    core=make(tmp_path,draft=lambda *_:AssistantResponse(display_text='Saved.',delivery_mode='text_only',
        state_updates=PersonalStateUpdates(personal_changes=[dict(op='remember',kind='fact',subject_id='owner',
            topic='atomic',text='This should never commit.')])) )
    original=core.personal._put
    def fail(db,kind,key,value):
        if kind=='personal_record':raise RuntimeError('injected_commit_failure')
        return original(db,kind,key,value)
    monkeypatch.setattr(core.personal,'_put',fail)
    with pytest.raises(RuntimeError,match='injected'):
        core.input('remember this','f','atomic')
    assert core.store.read('turn','atomic')['status']=='failed'
    assert core.store.read('personal_source',identifier('src','turn','atomic'))['status']=='pending'
    assert core.personal_state()['records']==[]
    core.close()


def test_identity_and_person_provenance_propagates_to_replies(tmp_path):
    sentinel='velvet-cedar-4792'
    core=make(tmp_path,draft=lambda *_:AssistantResponse(display_text=sentinel,delivery_mode='text_only'))
    core.personal_control('character','identity',{'fields':{'speech_style':sentinel}})
    source=core.personal_state()['identity']['field_sources']['speech_style']
    core.input('How do you speak?','f','identity-reply')
    core.personal_control('delete-character','delete',{'target_id':source})
    assert core.store.read('turn','identity-reply')['redacted']
    pid=core.personal_control('person-a','person',{'name':'Mira','notes':sentinel})['person_id']
    source=next(p['source_id'] for p in core.personal_state()['people'] if p['id']==pid)
    core.personal_control('person-b','person',{'person_id':pid,'relationship':'Disagrees with her plans.'})
    core.input('Who do you know?','f','person-reply')
    core.personal_control('delete-person','delete',{'target_id':source})
    assert core.store.read('turn','person-reply')['redacted']
    assert sentinel not in json.dumps(core.personal_state())
    core.close()


def test_resolved_commitment_is_searchable_history_without_worker_success_claim(tmp_path):
    core=make(tmp_path)
    mid=core.personal_control('call','memory',dict(op='remember',kind='commitment',subject_id='owner',
        topic='Mira lantern call',text='Need to call Mira about the lantern.'))['id']
    core.personal_control('called','memory',dict(op='resolve',target_id=mid))
    with core.store.transaction() as db:
        context=core.personal.context(db,'Mira lantern call')
        assert not context['unfinished_and_decisions']
        recalled=context['recall'][0]
        assert recalled['provenance']['record_status']=='resolved'
        assert recalled['provenance']['report_is_not_verified_fact'] is True
        assert 'not independently verified' in recalled['text']
    core.close()


def test_forgetting_after_old_reader_return_removes_its_quoted_reply(tmp_path):
    sentinel='violet-summit-3864'
    core=make(tmp_path)
    mid=remember(core,sentinel+' is the workshop drink.')
    core.close()
    store=Store(tmp_path)
    with store.transaction() as db:
        store.put(db,'turn','old-reader-new',{'id':'old-reader-new','session_id':'old-session',
            'text':'What was the workshop drink?','status':'completed','generation':1,
            'response':AssistantResponse(display_text=sentinel,delivery_mode='text_only').model_dump()})
    core=make(tmp_path)
    core.personal_control('remove-after-return','forget',{'target_id':mid})
    assert core.store.read('turn','old-reader-new')['redacted']
    with core.store.transaction() as db:
        assert sentinel not in core._context(db,'old-session','workshop')
    core.close()


def test_zero_raw_retention_cannot_cancel_a_live_turn_on_maintenance_tick(tmp_path):
    entered,release=threading.Event(),threading.Event()
    clock=[100]
    def blocked(*_):
        entered.set();assert release.wait(10)
        return AssistantResponse(display_text='Current reply delivered.',delivery_mode='text_only')
    core=make(tmp_path,draft=blocked,clock=lambda:clock[0])
    core.personal_control('zero-retention','policy',{'text_days':0})
    with ThreadPoolExecutor() as pool:
        result=pool.submit(core.input,'Current input','f','live-retention')
        assert entered.wait(5)
        clock[0]=161
        core.tick()
        release.set()
        assert result.result()['result']['assistant_response']['display_text']=='Current reply delivered.'
    assert core.store.read('turn','live-retention')['status']=='pruned'
    core.close()
