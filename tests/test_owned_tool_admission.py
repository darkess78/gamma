"""Only respond/structured parsing/owned journal/core tool; synthetic isolated state."""
import hashlib,json,os,sqlite3,sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event,Lock
from types import ModuleType,SimpleNamespace
import pytest
from sqlmodel import Session,select
from gamma.config import settings
from gamma.conversation import service as conversation_module
from gamma.conversation.service import ConversationService
from gamma.conversation.turn_contract import StructuredTurnParser
from gamma.identity.profile import SpeakerProfile
from gamma.llm.base import LLMReply
from gamma.memory.continuity import ContinuityService,ConversationJournalEntry,JournalOwnershipError
from gamma.memory.service import MemoryService
from gamma.schemas.conversation import RecipientContext
from gamma.schemas.response import ConversationTurnDraft,BoundedStateUpdates,ToolCall
from gamma.safety.speech_filter import SpeechFilterResult
from gamma.tools import builtin
from gamma.tools.registry import ToolRegistry

PRIVATE=RecipientContext(kind='owner_private',owner_authorized=True)
OWNER=SpeakerProfile('FixtureOwner','owner',is_owner=True,resolved_via='synthetic_fixture')
CASES=[]

def digest(value):return hashlib.sha256(value).hexdigest()
def snapshot_digest(value):return digest(json.dumps(value,sort_keys=True,default=str).encode())

class Timeline:
 def __init__(self):self.events=[];self.lock=Lock()
 def note(self,event,**fields):
  with self.lock:
   self.events.append({'index':len(self.events),'event':event,**fields})
   assert len(self.events)<=40

class Provider:
 def __init__(self,timeline,held=True):self.timeline=timeline;self.held=held;self.entered=Event();self.release=Event();self.calls=[]
 def generate_reply(self,system_prompt,user_text,**kwargs):
  purpose=getattr(kwargs.get('call_context'),'purpose',None);self.calls.append(purpose)
  if purpose=='conversation_draft':
   self.timeline.note('draft_entered');self.entered.set()
   if self.held:
    assert self.release.wait(8),'fixture_provider_not_released'
    self.timeline.note('draft_released')
   draft=ConversationTurnDraft(action='tool_first',requested_delivery='text_only',final_text='Safe synthetic acknowledgement.',reason_code='fixture',
    internal_summary='fixture summary',state_updates=BoundedStateUpdates(current_objective='late fixture objective'),
    tool_calls=[ToolCall(tool='save_core_memory',args={'fact':'Probe fresh core fact.','is_owner':True})])
  elif purpose=='conversation_finalizer':
   self.timeline.note('synthetic_finalizer');draft=ConversationTurnDraft(action='reply',requested_delivery='text_only',final_text='Safe synthetic acknowledgement.',reason_code='fixture')
  else:raise AssertionError('unexpected_provider_purpose')
  return LLMReply(text=draft.model_dump_json())

class CoreTool(builtin.SaveCoreMemoryTool):
 def __init__(self,path,timeline):self.path=path;self.timeline=timeline;self.starts=0;self.saves=0;self.hold_after=False;self.written=Event();self.release=Event()
 def run(self,fact,**kwargs):
  self.starts+=1;self.timeline.note('core_tool_started')
  result=super().run(fact,**kwargs)
  self.saves+=result.metadata.get('saved',0);self.timeline.note('core_tool_finished',saved=result.metadata.get('saved',0));self.written.set()
  if self.hold_after:assert self.release.wait(8),'fixture_tool_not_released'
  return result

@pytest.fixture(autouse=True,scope='session')
def record_cases():
 yield
 Path(os.environ['TMPDIR'],'cancel-boundary-cases.json').write_text(json.dumps(CASES,indent=2))

@pytest.fixture
def fixture(tmp_path,monkeypatch):
 data=tmp_path/'data';data.mkdir();seed=data/'seed.db';database=data/'copied.db'
 url='sqlite:///'+str(seed);seed_service=ContinuityService(database_url=url)
 seed_exchange=seed_service.begin_exchange(session_id='target',text='prior checkpoint',speaker_name='FixtureOwner',input_source='local',privacy_scope='local_private')
 seed_service.complete_exchange(exchange_id=seed_exchange,session_id='target',assistant_text='',trace_id=None,output_target='dashboard_monitor',privacy_scope='local_private',state_updates={'current_objective':'preserved checkpoint'})
 seed_service._engine.dispose();seed.chmod(0o444);seed_hash=digest(seed.read_bytes())
 with sqlite3.connect('file:'+str(seed)+'?mode=ro',uri=True) as original,sqlite3.connect(database) as copied:original.backup(copied)
 continuity=ContinuityService(database_url='sqlite:///'+str(database))
 core=data/'core_memories.md';core.write_text('# Core Memories\n\n- Prior committed core fact.\n')
 monkeypatch.setattr(settings,'project_root',tmp_path);monkeypatch.setattr(settings,'data_dir',data);monkeypatch.setattr(settings,'database_url','sqlite:///'+str(database));monkeypatch.setattr(settings,'assistant_state_enabled',False);monkeypatch.setattr(settings,'speech_filter_llm_enabled',False)
 monkeypatch.setattr(builtin,'_CORE_MEMORIES_PATH',core)
 monkeypatch.setattr(conversation_module,'build_system_prompt',lambda **kwargs:'# Identity\nSafe synthetic character context.')
 monkeypatch.setattr(conversation_module,'load_persona_config',lambda **kwargs:None)
 presence=ModuleType('gamma.presence');presence.load_presence_state=lambda:None;monkeypatch.setitem(sys.modules,'gamma.presence',presence)
 timeline=Timeline();tool=CoreTool(core,timeline);registry=ToolRegistry.__new__(ToolRegistry);registry._tools={'save_core_memory':tool}
 service=ConversationService.__new__(ConversationService);service._continuity=continuity;service._memory=MemoryService.__new__(MemoryService);service._memory._engine=continuity._engine;service._tools=registry;service._turn_parser=StructuredTurnParser()
 state=SimpleNamespace(service=service,continuity=continuity,core=core,tool=tool,timeline=timeline,profile=OWNER,seed=seed,seed_hash=seed_hash)
 service._identity=SimpleNamespace(resolve=lambda context:state.profile)
 service._speech_filter=SimpleNamespace(apply=lambda text,**kwargs:SpeechFilterResult(spoken_text=text,blocked=False,matched_rules=[],action='allow',layers=[]))
 service._append_timing_log=lambda **kwargs:None;service._background_memory_save=lambda *args,**kwargs:None
 def forbidden(*args,**kwargs):raise AssertionError('forbidden_voice_presence_or_model_runtime')
 service._tts_service=forbidden;service._record_presence_interaction=forbidden
 yield state
 assert digest(seed.read_bytes())==seed_hash
 continuity._engine.dispose()

def rows(state):
 with Session(state.continuity._engine) as db:
  entries=list(db.exec(select(ConversationJournalEntry).order_by(ConversationJournalEntry.id)))
 return [{'id':r.id,'exchange_id':r.exchange_id,'role':r.role,'status':r.completion_status,'session':r.session_id,'scope':r.privacy_scope} for r in entries]

def pending(state):
 return next(r['exchange_id'] for r in rows(state) if r['role']=='user' and r['session']=='target' and r['status']=='pending')

def checkpoint(state):return state.continuity.snapshot('target',privacy_scope='local_private')

def respond(state,fast=True,recipient=PRIVATE):
 return state.service.respond('held intent',session_id='target',recipient=recipient,fast_mode=fast,synthesize_speech=False)

def record(state,case,**values):
 CASES.append({'case':case,'timeline':list(state.timeline.events),'core_tool_starts':state.tool.starts,'core_saved_count':state.tool.saves,
  'core_after_sha256':digest(state.core.read_bytes()),'original_seed_sha256':state.seed_hash,'original_seed_preserved':digest(state.seed.read_bytes())==state.seed_hash,'journal':rows(state),**values})

@pytest.mark.parametrize('fast',[True,False])
def test_explicit_owned_cancellation_before_tool_prevents_new_core_write(fixture,fast):
 state=fixture;provider=Provider(state.timeline);state.service._llm=provider;before_core=state.core.read_bytes()
 with ThreadPoolExecutor(max_workers=1) as pool:
  future=pool.submit(respond,state,fast)
  try:
   assert provider.entered.wait(5);exchange=pending(state);before=checkpoint(state)
   assert state.tool.starts==0 and state.core.read_bytes()==before_core
   state.timeline.note('owned_cancel_requested',exchange_id=exchange);assert state.continuity.fail_exchange(exchange,status='cancelled')
   state.timeline.note('owned_cancel_committed',exchange_id=exchange);provider.release.set()
   try:future.result(timeout=10);terminal_error=None
   except JournalOwnershipError:terminal_error='JournalOwnershipError'
  finally:provider.release.set()
 after=checkpoint(state);journal=rows(state)
 assert next(r for r in journal if r['exchange_id']==exchange and r['role']=='user')['status']=='cancelled'
 assert not any(r['exchange_id']==exchange and r['role']=='assistant' for r in journal)
 assert after==before and b'Prior committed core fact.' in state.core.read_bytes()
 state.timeline.note('terminal_observed',error=terminal_error)
 record(state,'cancel_before_tool_fast' if fast else 'cancel_before_tool_standard',core_before_sha256=digest(before_core),checkpoint_before_sha256=snapshot_digest(before),checkpoint_after_sha256=snapshot_digest(after),cancelled_journal_preserved=True,terminal_error=terminal_error)
 assert state.tool.starts==0 and state.core.read_bytes()==before_core,'cancelled_exchange_started_new_core_write'

@pytest.mark.parametrize('fast',[True,False])
def test_uncancelled_intent_writes_once(fixture,fast):
 state=fixture;provider=Provider(state.timeline,held=False);state.service._llm=provider;response=respond(state,fast)
 assert state.tool.starts==state.tool.saves==1 and state.core.read_bytes().count(b'- Probe fresh core fact.')==1
 assert response.tool_results[0].ok and any(r['status']=='completed' and r['session']=='target' and r['role']=='assistant' for r in rows(state))
 record(state,'uncancelled_fast' if fast else 'uncancelled_standard')

@pytest.mark.parametrize('session,scope',[('other','local_private'),('target','public'),('target','local_private')])
def test_cancelling_other_exchange_never_suppresses_valid_turn(fixture,session,scope):
 state=fixture;provider=Provider(state.timeline);state.service._llm=provider
 with ThreadPoolExecutor(max_workers=1) as pool:
  future=pool.submit(respond,state)
  try:
   assert provider.entered.wait(5);own=pending(state)
   other=state.continuity.begin_exchange(session_id=session,text='other intent',speaker_name='FixtureOwner',input_source='local',privacy_scope=scope)
   assert other!=own and state.continuity.fail_exchange(other,status='cancelled');state.timeline.note('other_cancel_committed',exchange_id=other)
   provider.release.set();response=future.result(timeout=10)
  finally:provider.release.set()
 assert state.tool.starts==state.tool.saves==1 and response.tool_results[0].ok
 assert next(r for r in rows(state) if r['exchange_id']==other)['status']=='cancelled'
 assert next(r for r in rows(state) if r['exchange_id']==own and r['role']=='user')['status']=='completed'
 record(state,'other_cancel_'+session+'_'+scope)

def test_newer_input_without_explicit_cancel_keeps_older_effects_and_history(fixture):
 state=fixture;provider=Provider(state.timeline);state.service._llm=provider
 with ThreadPoolExecutor(max_workers=1) as pool:
  future=pool.submit(respond,state)
  try:
   assert provider.entered.wait(5);own=pending(state);before=checkpoint(state)
   newer=state.continuity.begin_exchange(session_id='target',text='newer input',speaker_name='FixtureOwner',input_source='local',privacy_scope='local_private')
   state.timeline.note('newer_input_received',exchange_id=newer);provider.release.set();future.result(timeout=10)
  finally:provider.release.set()
 assert state.tool.starts==state.tool.saves==1
 assert next(r for r in rows(state) if r['exchange_id']==own and r['role']=='user')['status']=='completed'
 assert next(r for r in rows(state) if r['exchange_id']==newer)['status']=='pending'
 assert any(r['exchange_id']==own and r['role']=='assistant' for r in rows(state))
 assert checkpoint(state)['working_state']['current_objective']==before['working_state']['current_objective']
 record(state,'newer_input_not_cancellation')

def test_cancel_after_committed_effect_preserves_it_without_rollback(fixture):
 state=fixture;provider=Provider(state.timeline,held=False);state.service._llm=provider;state.tool.hold_after=True
 with ThreadPoolExecutor(max_workers=1) as pool:
  future=pool.submit(respond,state)
  try:
   assert state.tool.written.wait(5);own=pending(state);committed=state.core.read_bytes();assert state.tool.saves==1
   assert state.continuity.fail_exchange(own,status='cancelled');state.timeline.note('cancel_after_core_commit',exchange_id=own);state.tool.release.set()
   with pytest.raises(JournalOwnershipError):future.result(timeout=10)
  finally:state.tool.release.set()
 assert state.core.read_bytes()==committed and state.tool.saves==1 and b'Prior committed core fact.' in committed
 record(state,'already_committed_effect_preserved',committed_core_sha256=digest(committed))

@pytest.mark.parametrize('profile',[SpeakerProfile('Guest','guest'),SpeakerProfile('Friend','trusted'),SpeakerProfile('Fake','owner',is_owner=False)])
def test_nonowners_cannot_authorize_core_tool_via_model_arguments(fixture,profile):
 state=fixture;state.profile=profile;state.service._llm=Provider(state.timeline,held=False);before=state.core.read_bytes();response=respond(state)
 assert state.tool.starts==0 and state.core.read_bytes()==before
 if profile.tools_allowed:assert response.tool_results[0].metadata['reason']=='owner_required'
 record(state,'nonowner_'+profile.trust)

@pytest.mark.parametrize('recipient',[None,RecipientContext(),RecipientContext(kind='shared')])
def test_nonprivate_recipients_keep_fixed_disclosure(fixture,recipient):
 state=fixture;state.service._llm=Provider(state.timeline,held=False);response=respond(state,recipient=recipient)
 assert state.tool.saves==1
 assert response.tool_results[0].metadata=={'disclosure':'status_only','status':'completed'}
 assert 'Probe fresh core fact.' not in response.display_text and all(not c.args for c in response.tool_calls)
 record(state,'nonprivate_'+('none' if recipient is None else recipient.kind))


def test_main_cancel_between_tools_keeps_committed_first_but_blocks_second(fixture):
 state=fixture
 class TwoTools(Provider):
  def generate_reply(self,*args,**kwargs):
   reply=super().generate_reply(*args,**kwargs)
   if getattr(kwargs.get('call_context'),'purpose',None)=='conversation_draft':
    value=json.loads(reply.text);value['tool_calls'].append({'tool':'save_core_memory','args':{'fact':'Second fresh core fact.'}})
    return LLMReply(text=json.dumps(value))
   return reply
 state.service._llm=TwoTools(state.timeline,held=False);state.tool.hold_after=True
 with ThreadPoolExecutor(max_workers=1) as pool:
  future=pool.submit(respond,state)
  try:
   assert state.tool.written.wait(5);exchange=pending(state)
   committed=state.core.read_bytes();assert state.tool.starts==state.tool.saves==1
   assert b'Probe fresh core fact.' in committed and b'Second fresh core fact.' not in committed
   assert state.continuity.fail_exchange(exchange,status='cancelled')
   state.timeline.note('main_cancel_between_tools_committed',exchange_id=exchange);state.tool.release.set()
   with pytest.raises(JournalOwnershipError):future.result(timeout=10)
  finally:state.tool.release.set()
 record(state,'main_cancel_between_tools',first_committed_sha256=digest(committed))
 assert state.tool.starts==state.tool.saves==1 and state.core.read_bytes()==committed,'cancelled_exchange_started_second_tool_after_first_commit'



@pytest.mark.parametrize('session_b,recipient_b',[('other',PRIVATE),('target',RecipientContext(kind='shared')),('target',PRIVATE)])
def test_concurrent_turns_keep_their_own_admission_context(fixture,session_b,recipient_b):
 state=fixture
 class SplitProvider:
  def __init__(self):self.entered=Event();self.release=Event()
  def generate_reply(self,system_prompt,user_text,**kwargs):
   purpose=getattr(kwargs.get('call_context'),'purpose',None)
   if purpose=='conversation_finalizer':return LLMReply(text=ConversationTurnDraft(action='reply',requested_delivery='text_only',final_text='Safe synthetic acknowledgement.').model_dump_json())
   assert purpose=='conversation_draft'
   older=user_text=='held alpha'
   if older:self.entered.set();assert self.release.wait(8)
   draft=ConversationTurnDraft(action='tool_first',requested_delivery='text_only',final_text='Safe acknowledgement.',internal_summary='fixture summary',state_updates=BoundedStateUpdates(current_objective='alpha' if older else 'beta'),tool_calls=[ToolCall(tool='save_core_memory',args={'fact':'Alpha fresh fact.' if older else 'Beta valid fact.'})])
   return LLMReply(text=draft.model_dump_json())
 provider=SplitProvider();state.service._llm=provider
 with ThreadPoolExecutor(max_workers=1) as pool:
  future=pool.submit(state.service.respond,'held alpha',session_id='target',recipient=PRIVATE,fast_mode=True,synthesize_speech=False)
  try:
   assert provider.entered.wait(5);exchange=pending(state);assert state.continuity.fail_exchange(exchange,status='cancelled')
   response=state.service.respond('valid beta',session_id=session_b,recipient=recipient_b,fast_mode=True,synthesize_speech=False)
   saved=state.continuity.snapshot(session_b,privacy_scope=recipient_b.privacy_scope)
   assert state.tool.saves==1 and response.tool_results[0].ok
   provider.release.set()
   with pytest.raises(JournalOwnershipError):future.result(timeout=10)
  finally:provider.release.set()
 assert state.tool.starts==state.tool.saves==1 and b'Beta valid fact.' in state.core.read_bytes() and b'Alpha fresh fact.' not in state.core.read_bytes()
 assert state.continuity.snapshot(session_b,privacy_scope=recipient_b.privacy_scope)==saved

def test_model_arguments_cannot_replace_bound_exchange_or_disable_checkpoint(fixture):
 state=fixture
 class ForgedArgs(Provider):
  def generate_reply(self,*args,**kwargs):
   reply=super().generate_reply(*args,**kwargs)
   if getattr(kwargs.get('call_context'),'purpose',None)=='conversation_draft':
    value=json.loads(reply.text);value['tool_calls'][0]['args'].update(tool_checkpoint=None,exchange_id='forged',session_id='other',privacy_scope='public',owner_id='forged');return LLMReply(text=json.dumps(value))
   return reply
 provider=ForgedArgs(state.timeline);state.service._llm=provider;before=state.core.read_bytes()
 with ThreadPoolExecutor(max_workers=1) as pool:
  future=pool.submit(respond,state)
  try:
   assert provider.entered.wait(5);assert state.continuity.fail_exchange(pending(state),status='cancelled');provider.release.set()
   with pytest.raises(JournalOwnershipError):future.result(timeout=10)
  finally:provider.release.set()
 assert state.tool.starts==0 and state.core.read_bytes()==before

def test_foreign_owned_pending_row_is_not_admitted(fixture):
 state=fixture;foreign=ContinuityService(database_url=settings.database_url);other=foreign.begin_exchange(session_id='foreign',text='foreign',speaker_name='FixtureOwner',input_source='local',privacy_scope='local_private')
 provider=Provider(state.timeline);state.service._llm=provider;before=state.core.read_bytes()
 try:
  with ThreadPoolExecutor(max_workers=1) as pool:
   future=pool.submit(respond,state)
   try:
    assert provider.entered.wait(5);exchange=pending(state)
    with Session(state.continuity._engine) as db:
     entry=db.exec(select(ConversationJournalEntry).where(ConversationJournalEntry.exchange_id==exchange,ConversationJournalEntry.role=='user')).one();entry.owner_id=foreign._owner_id;db.add(entry);db.commit()
    provider.release.set()
    with pytest.raises(JournalOwnershipError):future.result(timeout=10)
   finally:provider.release.set()
  assert state.tool.starts==0 and state.core.read_bytes()==before
  with Session(foreign._engine) as db:
   entry=db.exec(select(ConversationJournalEntry).where(ConversationJournalEntry.exchange_id==exchange,ConversationJournalEntry.role=='user')).one();assert entry.owner_id==foreign._owner_id and entry.completion_status=='pending'
 finally:foreign._engine.dispose()

@pytest.mark.parametrize('field,value',[('session_id','other'),('privacy_scope','public')])
def test_changed_exact_scope_is_rejected_before_tool_admission(fixture,field,value):
 state=fixture;provider=Provider(state.timeline);state.service._llm=provider;before=state.core.read_bytes()
 with ThreadPoolExecutor(max_workers=1) as pool:
  future=pool.submit(respond,state)
  try:
   assert provider.entered.wait(5);exchange=pending(state)
   with Session(state.continuity._engine) as db:
    entry=db.exec(select(ConversationJournalEntry).where(ConversationJournalEntry.exchange_id==exchange,ConversationJournalEntry.role=='user')).one();setattr(entry,field,value);db.add(entry);db.commit()
   provider.release.set()
   with pytest.raises(JournalOwnershipError):future.result(timeout=10)
  finally:provider.release.set()
 assert state.tool.starts==0 and state.core.read_bytes()==before

@pytest.mark.parametrize('failure',['cancel','provider','tool'])
def test_exception_does_not_leak_context_into_later_turn(fixture,failure):
 state=fixture
 if failure=='cancel':
  provider=Provider(state.timeline);state.service._llm=provider
  with ThreadPoolExecutor(max_workers=1) as pool:
   future=pool.submit(respond,state)
   try:
    assert provider.entered.wait(5);assert state.continuity.fail_exchange(pending(state),status='cancelled');provider.release.set()
    with pytest.raises(JournalOwnershipError):future.result(timeout=10)
   finally:provider.release.set()
 elif failure=='provider':
  class FailedProvider:
   def generate_reply(self,*args,**kwargs):raise RuntimeError('synthetic_provider_failure')
  state.service._llm=FailedProvider()
  from gamma.errors import ConversationError
  with pytest.raises(ConversationError):respond(state)
 else:
  class FailedTool:
   def run(self,**kwargs):raise ValueError('synthetic_tool_failure')
  state.service._tools._tools['save_core_memory']=FailedTool();state.service._llm=Provider(state.timeline,held=False)
  response=respond(state);assert not response.tool_results[0].ok and response.tool_results[0].metadata['reason']=='execution_error'
  state.service._tools._tools['save_core_memory']=state.tool
 state.service._llm=Provider(state.timeline,held=False);response=respond(state)
 assert response.tool_results[0].ok and state.tool.starts==state.tool.saves==1

def test_already_admitted_work_survives_cancel_and_db_read_is_closed(fixture,monkeypatch):
 state=fixture;state.service._llm=Provider(state.timeline,held=False);original=state.continuity.require_owned_pending_exchange;admitted=Event();release=Event()
 def after_read(*args,**kwargs):
  original(*args,**kwargs);admitted.set();assert release.wait(8)
 monkeypatch.setattr(state.continuity,'require_owned_pending_exchange',after_read)
 with ThreadPoolExecutor(max_workers=1) as pool:
  future=pool.submit(respond,state)
  try:
   assert admitted.wait(5);assert state.tool.starts==0
   # Cancellation acquires its independent write transaction while the callback waits.
   assert state.continuity.fail_exchange(pending(state),status='cancelled');release.set()
   with pytest.raises(JournalOwnershipError):future.result(timeout=10)
  finally:release.set()
 assert state.tool.starts==state.tool.saves==1 and b'Probe fresh core fact.' in state.core.read_bytes()

@pytest.mark.parametrize('mode',['sessionless','evaluation','direct'])
def test_absent_context_compatibility_does_not_invent_a_binding(fixture,monkeypatch,mode):
 state=fixture;state.service._llm=Provider(state.timeline,held=False);before=state.core.read_bytes();before_rows=rows(state)
 def forbidden(*args,**kwargs):raise AssertionError('absent_context_has_no_journal_admission_binding')
 monkeypatch.setattr(state.continuity,'require_owned_pending_exchange',forbidden,raising=False)
 if mode=='direct':result=state.service._execute_tool_calls([ToolCall(tool='save_core_memory',args={'fact':'Direct fixture fact.'})],speaker=OWNER,recipient=PRIVATE);assert result[0].ok
 else:state.service.respond('fixture request',session_id=None if mode=='sessionless' else 'target',evaluation_mode=mode=='evaluation',recipient=PRIVATE,fast_mode=True,synthesize_speech=False)
 if mode=='evaluation':assert state.tool.starts==0 and state.core.read_bytes()==before
 else:assert state.tool.starts==state.tool.saves==1
 assert rows(state)==before_rows

def test_long_session_uses_existing_journal_normalization(fixture):
 state=fixture;state.service._llm=Provider(state.timeline,held=False);session='scope-'+('x'*130)
 response=state.service.respond('fixture request',session_id=session,recipient=PRIVATE,fast_mode=True,synthesize_speech=False)
 assert response.tool_results[0].ok and state.tool.saves==1
 assert any(r['session']==session[:120] and r['status']=='completed' for r in rows(state))
