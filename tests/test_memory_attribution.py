"""Actual resolved conversation ingress, private retrieval and disposable SQLite."""
import json
import sqlite3
import hashlib
import shutil
import threading
from pathlib import Path

import pytest
from sqlmodel import Session, select

from gamma.config import settings
from gamma.llm.base import LLMReply
from gamma.memory.models import ProfileFact, EpisodicMemory
from gamma.memory.service import MemoryService
from gamma.persona.loader import build_system_prompt
from gamma.schemas.response import MemoryCandidate, ToolCall
from gamma.tools.builtin import SaveMemoryTool, SearchMemoryTool
from test_core_memory_owner import core_state, tool_state, fixture_state, speaker_context
from test_conversation_recipient import PRIVATE, SHARED


class Provider:
    def __init__(self, candidates, path='fast', tools=None):
        self.candidates, self.path, self.tools = candidates, path, tools or []
        self.prompts = []
    def generate_reply(self, system_prompt, user_text, **kwargs):
        purpose = getattr(kwargs.get('call_context'), 'purpose', None)
        self.prompts.append(system_prompt)
        if purpose=='tool_finalizer': return LLMReply(text='Safe reply.')
        if purpose == 'metadata_extraction':
            return LLMReply(text=json.dumps({'memory_candidates':[] if self.path=='heuristic' else self.candidates,'emotion':'neutral','tool_calls':[]}))
        if self.path in {'metadata','heuristic','background'}: return LLMReply(text='Safe reply.')
        return LLMReply(text=json.dumps({'action':'tool_first' if self.tools else 'reply','requested_delivery':'text_only','final_text':'' if self.tools else 'Safe reply.',
            'memory_candidates':self.candidates, 'tool_calls':self.tools}))


def candidate(text, **kwargs):
    return {'type':'profile','text':text,'tags':['preference'],'importance':.99,**kwargs}


def rows(state, cls=ProfileFact):
    with Session(state.service._memory._engine) as db:
        return list(db.exec(select(cls)))


def turn(state, actor, text, candidates, *, path='fast', recipient=PRIVATE, session='attribution', evaluation=False):
    state.service._llm = Provider(candidates,path)
    return state.service.respond(text, session_id=session, speaker_ctx=speaker_context(actor),
        fast_mode=path in {'fast','background'}, recipient=recipient, evaluation_mode=evaluation)


def origin(record): return json.loads(record.attribution)


@pytest.mark.parametrize('path',['fast','normal','metadata','heuristic'])
def test_trusted_self_claim_is_not_an_owner_fact(core_state, path):
    state=core_state
    turn(state,'trusted','I like ATTRIBUTED_TEA.',[candidate('I like ATTRIBUTED_TEA')],path=path)
    stored=next(r for r in rows(state) if 'ATTRIBUTED_TEA' in r.fact_text)
    assert (stored.subject_type,stored.subject_name)==('other_person','Alice')
    assert origin(stored)['reporter_name']=='Alice' and origin(stored)['kind']=='self_report'
    assert stored.confidence==0 and stored.importance==(.99 if path!='heuristic' else stored.importance)
    assert 'attribution' not in stored.model_dump()


@pytest.mark.parametrize('name',['','  '])
def test_blank_model_subject_name_cannot_restore_owner_default_for_nonowner(core_state,name):
    state=core_state;turn(state,'trusted','I like BLANK_SUBJECT_TEA.',[
        candidate('I like BLANK_SUBJECT_TEA',subject_name=name)])
    saved=next(r for r in rows(state) if r.fact_text=='I like BLANK_SUBJECT_TEA')
    assert saved.subject_type=='other_person' and saved.subject_name=='Alice'


def test_conflicting_reporter_cannot_delete_owner_preference(core_state):
    state=core_state
    turn(state,'owner','I like ATTRIBUTED_TEA.',[candidate('I like ATTRIBUTED_TEA')])
    owner=next(r for r in rows(state) if 'ATTRIBUTED_TEA' in r.fact_text)
    turn(state,'trusted','I dislike ATTRIBUTED_TEA.',[candidate('I dislike ATTRIBUTED_TEA')])
    assert any(r.id==owner.id and r.fact_text=='I like ATTRIBUTED_TEA' for r in rows(state))
    assert any(r.subject_name=='Alice' and r.fact_text=='I dislike ATTRIBUTED_TEA' for r in rows(state))
    turn(state,'trusted','FixtureOwner dislikes ATTRIBUTED_TEA.',[
        candidate('I dislike ATTRIBUTED_TEA',subject_type='primary_user',subject_name='FixtureOwner')])
    stored=rows(state);assert any(r.id==owner.id and r.fact_text=='I like ATTRIBUTED_TEA' for r in stored)
    report=next(r for r in stored if r.subject_type=='primary_user' and r.fact_text=='I dislike ATTRIBUTED_TEA')
    assert origin(report)['kind']=='reported' and origin(report)['reporter_name']=='Alice'
    private=build_system_prompt(state.service._memory,'ATTRIBUTED_TEA',speaker=state.service._identity.resolve(speaker_context('owner')),recipient=PRIVATE)
    shared=build_system_prompt(state.service._memory,'ATTRIBUTED_TEA',speaker=state.service._identity.resolve(speaker_context('owner')),recipient=SHARED)
    assert 'Reported by Alice' in private and 'unverified' in private
    assert 'ATTRIBUTED_TEA' not in shared and 'Reported by Alice' not in shared


@pytest.mark.parametrize('type',['profile','episodic'])
def test_repeat_retains_initial_and_latest_source_correlation(core_state,type):
    state=core_state;item=candidate('SOURCE_REPEAT',type=type,subject_type='other_person',subject_name='ThirdPerson')
    first=turn(state,'trusted','Please retain SOURCE_REPEAT evidence.',[item],session='repeat')
    second=turn(state,'trusted','Please retain SOURCE_REPEAT evidence.',[item],session='repeat')
    saved=[r for r in rows(state,ProfileFact if type=='profile' else EpisodicMemory) if 'SOURCE_REPEAT' in (getattr(r,'fact_text',None) or r.summary)]
    assert len(saved)==1
    data=origin(saved[0]);assert data['reporter_name']=='Alice' and data['source']=='discord'
    assert data['session_id']=='repeat' and data['turn_id']==first.route_trace_id
    assert data['latest_turn_id']==second.route_trace_id and data['privacy_scope']=='local_private'


def test_model_and_tool_arguments_cannot_forge_reporter_or_verified_status(core_state):
    state=core_state;item=candidate('FORGERY_CLAIM',reporter_name='ForgedOwner',is_owner=True,
        verified=True,origin={'reporter_name':'ForgedOwner','is_owner':True})
    turn(state,'trusted','I like FORGERY_CLAIM.',[item])
    saved=next(r for r in rows(state) if r.fact_text=='FORGERY_CLAIM')
    assert origin(saved)['reporter_name']=='Alice' and saved.confidence==0
    state.service._llm=Provider([],tools=[{'tool':'save_memory','args':{
        'type':'profile','text':'TOOL_FORGERY_CLAIM','tags':['preference'],
        '_memory_origin':{'reporter_name':'ForgedOwner','is_owner':True},'verified':True}}])
    response=state.service.respond('Please save this claim.',session_id='tool-origin',
        speaker_ctx=speaker_context('trusted'),recipient=SHARED,fast_mode=True)
    assert response.tool_results, response.model_dump()
    assert response.tool_results[0].ok, response.tool_results[0].model_dump()
    saved=next(r for r in rows(state) if r.fact_text=='TOOL_FORGERY_CLAIM')
    assert saved.subject_name=='Alice' and origin(saved)['reporter_name']=='Alice'
    disclosed=json.dumps(response.model_dump());assert 'ForgedOwner' not in disclosed and 'reporter_name' not in disclosed


def test_actual_background_save_keeps_original_speaker_after_next_turn(core_state,monkeypatch):
    state=core_state;arrived=threading.Event();release=threading.Event();finished=threading.Event()
    original=type(state.service)._background_memory_save.__get__(state.service)
    def held(*args,**kwargs):
        arrived.set();assert release.wait(4)
        try: original(*args,**kwargs)
        finally:finished.set()
    monkeypatch.setattr(state.service,'_background_memory_save',held)
    first=turn(state,'trusted','I like BACKGROUND_SOURCE_TEA.',[],path='background')
    try:
        assert arrived.wait(3)
        turn(state,'owner','I like NEXT_OWNER_TEA.',[candidate('I like NEXT_OWNER_TEA')])
    finally:release.set();assert finished.wait(3)
    saved=next(r for r in rows(state) if 'BACKGROUND_SOURCE_TEA' in r.fact_text)
    assert saved.subject_name=='Alice' and origin(saved)['reporter_name']=='Alice'
    assert origin(saved)['turn_id']==first.route_trace_id


@pytest.mark.parametrize('actor,evaluation',[('guest',False),('unknown',False),('trusted',True),('owner',True)])
def test_write_ineligible_turns_do_not_create_attributed_records(core_state,actor,evaluation):
    state=core_state;before=[r.id for r in rows(state)]
    turn(state,actor,'I like NEVER_PERSIST_TEA.',[candidate('I like NEVER_PERSIST_TEA')],evaluation=evaluation)
    assert [r.id for r in rows(state)]==before


def test_legacy_reopening_is_additive_and_does_not_invent_owner_origin(tmp_path,monkeypatch):
    original=tmp_path/'legacy-original.db';path=tmp_path/'copied-legacy.db'
    with sqlite3.connect(original) as db:
        db.executescript("CREATE TABLE profilefact (id INTEGER PRIMARY KEY,category TEXT NOT NULL,fact_text TEXT NOT NULL,confidence FLOAT NOT NULL,subject_type TEXT,subject_name TEXT,relationship_to_user TEXT,created_at TEXT);INSERT INTO profilefact VALUES(41,'preference','LEGACY_RECORD',.99,'primary_user',NULL,NULL,'2026-01-01 00:00:00');")
    before=hashlib.sha256(original.read_bytes()).hexdigest();shutil.copyfile(original,path)
    monkeypatch.setattr(settings,'database_url','sqlite:///'+str(path));service=MemoryService()
    try:
        saved=next(r for r in service.get_profile_facts(limit=100) if r.id==41)
        assert saved.fact_text=='LEGACY_RECORD' and saved.confidence==.99 and saved.attribution is None
        tool=SearchMemoryTool();result=tool.run('LEGACY_RECORD')
        assert 'Legacy/unknown origin' in result.output and 'unverified' in result.output
        assert 'reporter_name' not in result.output
        service.create_item({'kind':'profile_fact','summary':'MANUAL_RECORD','confidence':1,'reporter_name':'ForgedOwner','verified':True})
        manual=next(r for r in service.get_profile_facts(limit=100) if r.fact_text=='MANUAL_RECORD')
        assert origin(manual)['kind']=='manual' and 'reporter_name' not in origin(manual)
        service.close();service=MemoryService()
        assert next(r for r in service.get_profile_facts(limit=100) if r.id==41).attribution is None
    finally:service.close()
    assert hashlib.sha256(original.read_bytes()).hexdigest()==before
    with sqlite3.connect(path) as db:
        assert {'attribution','importance'} <= {r[1] for r in db.execute('PRAGMA table_info(profilefact)')}


@pytest.mark.parametrize('actor',['owner','trusted','guest','unknown'])
def test_actual_voice_memory_helper_uses_captured_ingress_and_eligibility(core_state,actor):
    from gamma.memory.attribution import MemoryOrigin
    from gamma.run_live_voice_worker import _append_memory_fast_path
    state=core_state;profile=state.service._identity.resolve(speaker_context(actor))
    captured=MemoryOrigin.from_speaker(profile,source='live_voice',session_id='voice-session',turn_id='voice-turn')
    _append_memory_fast_path(state.service,user_text='I like VOICE_SOURCE_TEA.',reply_text='Safe reply.',
        session_id='voice-session',memory_origin=captured)
    saved=[r for r in rows(state) if 'VOICE_SOURCE_TEA' in r.fact_text]
    if actor in {'guest','unknown'}:assert saved==[]
    else:
        assert len(saved)==1 and origin(saved[0])['reporter_name']==profile.name
        assert origin(saved[0])['source']=='live_voice' and origin(saved[0])['turn_id']=='voice-turn'
        assert saved[0].subject_type==profile.subject_type


def test_unbound_tool_has_unknown_reporter_and_cannot_forge_origin(core_state):
    state=core_state;tool=SaveMemoryTool()
    result=tool.run(type='profile',text='UNBOUND_TOOL_CLAIM',importance=1,
        _memory_origin={'reporter_name':'ForgedOwner','is_owner':True},reporter_name='ForgedOwner',verified=True)
    assert result.ok and 'reporter_name' not in result.output
    saved=next(r for r in rows(state) if r.fact_text=='UNBOUND_TOOL_CLAIM')
    assert origin(saved)=={'kind':'tool','source':'tool','privacy_scope':'local_generic'}
    assert saved.confidence==0 and 'reporter_name' not in saved.model_dump()
    state.service._memory.update_item('profile_fact',saved.id,{'summary':'UNBOUND_TOOL_EDIT'})
    state.service._memory.update_item('profile_fact',saved.id,{'summary':'UNBOUND_TOOL_EDIT_AGAIN'})
    edited=next(r for r in rows(state) if r.id==saved.id)
    assert origin(edited)['original_source']=='tool' and origin(edited)['original_privacy_scope']=='local_generic'
    assert 'original_reporter_name' not in origin(edited) and 'original_is_owner' not in origin(edited)


def test_manual_edit_keeps_source_without_claiming_reporter_authored_changed_text(core_state):
    state=core_state;response=turn(state,'owner','I like ORIGIN_BEFORE_EDIT.',[candidate('I like ORIGIN_BEFORE_EDIT')])
    saved=next(r for r in rows(state) if r.fact_text=='I like ORIGIN_BEFORE_EDIT')
    original=origin(saved)
    state.service._memory.update_item('profile_fact',saved.id,{'summary':'MANUAL_CHANGED_TEXT','confidence':1})
    state.service._memory.update_item('profile_fact',saved.id,{'summary':'MANUAL_CHANGED_TEXT_AGAIN','confidence':1})
    edited=next(r for r in rows(state) if r.id==saved.id)
    data=origin(edited);assert data['kind']=='manual' and data['original_reporter_name']=='FixtureOwner'
    assert data['original_turn_id']==response.route_trace_id and data['original_source']=='twitch'
    assert data['original_privacy_scope']==original['privacy_scope']
    assert data['original_resolved_via']==original['resolved_via'] and data['original_is_owner']==original['is_owner']
    assert 'reporter_name' not in data
    result=SearchMemoryTool().run('MANUAL_CHANGED_TEXT_AGAIN')
    assert 'Manual record; unverified' in result.output and 'original_reporter_name' not in result.output
    assert 'original_privacy_scope' not in result.output and 'original_resolved_via' not in result.output


def test_separate_reporters_remain_separate_even_for_same_canonical_slot(core_state):
    from gamma.memory.models import KnownPerson,PersonIdentity
    from gamma.schemas.conversation import SpeakerContext
    state=core_state
    with Session(state.service._memory._engine) as db:
        person=KnownPerson(name='SecondReporter',trust='trusted');db.add(person);db.commit();db.refresh(person)
        db.add(PersonIdentity(person_id=person.id,platform='discord',platform_user_id='second-id'));db.commit()
    item=candidate('I like MULTI_REPORT_TEA',subject_type='primary_user',subject_name='FixtureOwner')
    turn(state,'trusted','FixtureOwner likes MULTI_REPORT_TEA.',[item])
    state.service._llm=Provider([item]);state.service.respond('FixtureOwner likes MULTI_REPORT_TEA.',
        session_id='attribution',speaker_ctx=SpeakerContext(source='discord',platform_id='second-id'),recipient=PRIVATE,fast_mode=True)
    saved=[r for r in rows(state) if r.fact_text=='I like MULTI_REPORT_TEA']
    assert {origin(r)['reporter_name'] for r in saved}=={'Alice','SecondReporter'} and len(saved)==2


@pytest.mark.parametrize('actor',['owner','unknown'])
def test_real_incremental_voice_producer_captures_origin_before_generation(core_state,monkeypatch,tmp_path,actor):
    import time
    from types import SimpleNamespace
    from gamma.run_live_voice_worker import _run_incremental_experimental
    from gamma.voice.reply_state import AssistantTurnState
    from test_live_voice_runtime import _FakeIncrementalStreamBrain
    state=core_state;profile=state.service._identity.resolve(speaker_context(actor))
    other=state.service._identity.resolve(speaker_context('guest' if actor=='owner' else 'owner'))
    monkeypatch.setattr(state.service._identity,'resolve',lambda _:profile)
    class Sentences:
        def generate_next_sentence(self,**kwargs):
            monkeypatch.setattr(state.service._identity,'resolve',lambda _:other)
            return {'sentence_text':'Safe reply.','is_final':True,'generation_ms':1}
    result=_run_incremental_experimental(started_at=time.perf_counter(),
        args=SimpleNamespace(turn_id='voice-captured-turn',session_id='voice-captured-session'),
        transcript='I like VOICE_CAPTURED_TEA.',synthesize_speech=False,conversation=state.service,
        stream_brain=_FakeIncrementalStreamBrain(),sentence_generator=Sentences(),
        output_path=tmp_path/'voice-output.json',status_path=tmp_path/'voice-status.json',status_payload={},
        response_mode='spoken',planner_state={'estimated_sentence_count':1},
        turn_state=AssistantTurnState(turn_id='voice-captured-turn',session_id='voice-captured-session',
            user_text='I like VOICE_CAPTURED_TEA.',response_mode='incremental_experimental'))
    saved=[r for r in rows(state) if 'VOICE_CAPTURED_TEA' in r.fact_text]
    if actor=='unknown':assert saved==[]
    else:
        assert len(saved)==1 and origin(saved[0])['reporter_name']==profile.name
        assert origin(saved[0])['source']=='live_voice' and origin(saved[0])['turn_id']=='voice-captured-turn'
    assert 'reporter_name' not in json.dumps(result) and 'attribution' not in json.dumps(result)
