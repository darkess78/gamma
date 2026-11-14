"""Cancellation through configured_app, ConversationService and real local adapters.

Only the provider HTTP boundary and authored STT transcript are controlled. The
actual provider-admission locks, parser, safety reviewer and HTTP routes run.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
import io,json,threading
from types import SimpleNamespace
from pathlib import Path
from urllib import error
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from gamma.config import settings
from gamma.coordination.app import configured_app
from gamma.coordination.discord_client import RemotePrivateInteraction
from gamma.coordination.interaction_models import InteractionObservation
from gamma.coordination.personal_models import PrivateTurnDraft
from gamma.llm import backend_admission,local_adapter
from test_private_interaction import wav

AUTH='technical-owner-token-'+'x'*32
ENDPOINT='http://127.0.0.1:17196'
LABELS=('voice-old','monitor-stale','phone-current','owner-wait','provider-failure','quiet-practice','quiet-exploration')

def label_for(text):
 if text.startswith('Quiet-time review.'):return 'quiet-practice'
 if text.startswith('Quiet opportunity review.'):return 'quiet-exploration'
 return next((v for v in LABELS if v in text),None)


class ASGIOpener:
 def __init__(self,client):self.client=client;self.statuses=[]
 def open(self,req,timeout=None):
  url=urlsplit(req.full_url)
  response=self.client.request(req.get_method(),url.path+('?' + url.query if url.query else ''),content=req.data,headers=dict(req.header_items()))
  self.statuses.append(response.status_code)
  if response.status_code>=400:
   raise error.HTTPError(req.full_url,response.status_code,'controlled ASGI response',{},io.BytesIO(response.content))
  return io.BytesIO(response.content)


@pytest.fixture
def actual(tmp_path,monkeypatch):
 data=tmp_path/'data';data.mkdir()
 config=tmp_path/'config';config.mkdir()
 (config/'persona.yaml').write_bytes((Path(__file__).resolve().parents[1]/'config/persona.yaml').read_bytes())
 for name,value in {'project_root':tmp_path,'data_dir':data,'database_url':'sqlite:///'+str(data/'conversation.sqlite3'),
  'audio_output_dir':data/'audio','llm_provider':'local','llm_router_enabled':False,'local_llm_endpoint':ENDPOINT,
  'local_llm_model':'qwen3.8:27b','local_llm_light_model':'','local_llm_tagging_model':'',
  'speech_filter_level':'strict','speech_filter_llm_enabled':True,'speech_filter_llm_model':'gemma4:e4b',
  'memory_enabled':False,'assistant_state_enabled':False,'stt_provider':'stub','tts_provider':'stub','tts_profile':''}.items():
  monkeypatch.setattr(settings,name,value)
 monkeypatch.setenv('GAMMA_PRACTICE_MODEL','qwen3.8:27b')
 monkeypatch.setattr(backend_admission,'_coordination_root',lambda:tmp_path/'admission')
 http=[];priorities={};admitted={label:threading.Event() for label in LABELS};held=threading.Event();release=threading.Event()
 label_scope=ContextVar('actual_checkpoint_test_label',default=None)
 original_generate=local_adapter.LocalLLMAdapter.generate_reply
 def traced_generate(self,*args,**kwargs):
  text=kwargs.get('user_text','');label=label_for(text)
  token=label_scope.set(label)
  try:return original_generate(self,*args,**kwargs)
  finally:label_scope.reset(token)
 monkeypatch.setattr(local_adapter.LocalLLMAdapter,'generate_reply',traced_generate)
 original_slot=backend_admission.backend_slot
 @contextmanager
 def observed_slot(endpoint,**kwargs):
  label=label_scope.get();checkpoint=kwargs.get('checkpoint')
  if label:priorities[label]=kwargs['foreground']
  if checkpoint:
   def observed_checkpoint():
    checkpoint()
    if label:admitted[label].set()
   kwargs['checkpoint']=observed_checkpoint
  elif label:admitted[label].set()
  with original_slot(endpoint,**kwargs):yield
 monkeypatch.setattr(local_adapter,'backend_slot',observed_slot)
 def model_http(req,timeout=None):
  assert req.full_url==ENDPOINT+'/api/generate','No actual model server may be contacted'
  payload=json.loads(req.data);prompt=payload.get('prompt','');label=label_for(prompt)
  is_review=payload['model']=='gemma4:e4b'
  http.append({'kind':'review' if is_review else 'primary','label':label})
  if label=='provider-failure':raise error.URLError('controlled provider unavailable')
  if not is_review and label=='voice-old':held.set();assert release.wait(5)
  value=({'action':'allow','reason':'safe authored fixture','confidence':1.0} if is_review else
   PrivateTurnDraft(action='reply',requested_delivery='text_only',final_text='Ready '+str(label)+'.',
    reason_code='technical_fixture_reply',emotion='neutral',state_updates={'activity_action':'wait'}).model_dump())
  return io.BytesIO(json.dumps({'response':json.dumps(value),'done':True}).encode())
 monkeypatch.setattr(local_adapter.request,'urlopen',model_http)
 app=configured_app(tmp_path,tmp_path/'state',bearer_token=AUTH,run_background=False)
 with TestClient(app,headers={'Authorization':'Bearer '+AUTH},raise_server_exceptions=False) as client:
  app.state.interaction.transcribe=lambda _:'voice-old'
  yield SimpleNamespace(app=app,client=client,http=http,admitted=admitted,held=held,release=release,priorities=priorities)
 release.set()


def test_actual_http_voice_monitor_phone_cancellation_is_not_server_error(actual):
 c=actual.client;opener=ASGIOpener(c);voice=RemotePrivateInteraction('http://127.0.0.1:18709',AUTH,opener=opener)
 voice.connect('discord-voice');voice.control_consumer('discord-voice','voice_enabled',True)
 phone={'audience':'owner_private','device_id':'phone','device_secret':'S'*43}
 assert c.post('/v1/coordination/phone/register',json={**phone,'request_id':'register'}).status_code==200
 connected=c.post('/v1/coordination/phone/connect',json={**phone,'request_id':'connect'});assert connected.status_code==200
 phone['epoch']=connected.json()['device']['epoch'];pool=ThreadPoolExecutor(max_workers=3)
 try:
  obs=InteractionObservation(audience='owner_private',request_id='discord-voice-'+'a'*32,session_id='discord-private',consumer_id='discord-voice',channel='discord_voice',speaker='owner')
  first=pool.submit(voice.submit_audio,obs,wav())
  assert actual.held.wait(2),'Actual primary HTTP call was not reached'
  second=pool.submit(c.post,'/v1/coordination/input',json={'audience':'owner_private','request_id':'monitor-second','session_id':'monitor-private','text':'monitor-stale'})
  assert actual.admitted['monitor-stale'].wait(2),'Actual LocalLLMAdapter admission was not reached'
  third=pool.submit(c.post,'/v1/coordination/phone/input',json={**phone,'request_id':'phone-third','text':'phone-current'})
  assert actual.admitted['phone-current'].wait(2)
  cancelled=second.result(timeout=2)
  assert cancelled.status_code==200,cancelled.text
  assert cancelled.json()['result']['assistant_response']['reason_code']=='superseded'
  assert actual.app.state.core.store.read('turn','monitor-second')['status']=='interrupted'
  actual.release.set();first_result=first.result(timeout=3);latest=third.result(timeout=3)
  assert first_result['status'] in {'deferred','cancelled'}
  assert latest.status_code==200,latest.text
  assert latest.json()['status']=='completed' and latest.json()['response']['display_text']=='Ready phone-current.'
  assert voice.failed is False and all(status==200 for status in opener.statuses)
  assert actual.http==[{'kind':'primary','label':'voice-old'},{'kind':'primary','label':'phone-current'},{'kind':'review','label':'phone-current'}]
  assert not actual.app.state.interaction.status('discord-voice')['outputs']
 finally:actual.release.set();pool.shutdown(wait=True)


def test_actual_conversation_checkpoint_owner_loss_remains_http_409(actual):
 pool=ThreadPoolExecutor(max_workers=1)
 try:
  with backend_admission.backend_slot(ENDPOINT,foreground=True,timeout=5):
   future=pool.submit(actual.client.post,'/v1/coordination/input',json={'audience':'owner_private','request_id':'owner-wait','text':'owner-wait'})
   assert actual.admitted['owner-wait'].wait(2)
   core=actual.app.state.core;core.store.claim(core.token[0])
   response=future.result(timeout=2)
   assert response.status_code==409,response.text
   assert response.json()['detail']=='coordinator ownership changed; result withheld'
   assert core.store.read('turn','owner-wait')['status']=='recoverable'
   assert actual.http==[]
 finally:pool.shutdown(wait=True)


def test_genuine_provider_failure_is_not_hidden_as_superseded(actual):
 response=actual.client.post('/v1/coordination/input',json={'audience':'owner_private','request_id':'provider-failure','text':'provider-failure'})
 assert response.status_code==500
 assert actual.app.state.core.store.read('turn','provider-failure')['status']=='failed'
 assert actual.http==[{'kind':'primary','label':'provider-failure'}]



def prepare_quiet(core,kind,monkeypatch):
 now=[core.clock()]
 monkeypatch.setattr(core,'clock',lambda:now[0])
 if kind=='practice':
  core.control('permit','permit',exercise_ids=['retry_budget'],budget_seconds=300)
  now[0]+=31
  return now,None
 tasks=[]
 for index in range(2):
  activity=core.work_control('quiet-task-'+str(index),'prepare',spec={'mode':'research','goal':'Inspect distinct authored example '+str(index),'files':{'note.txt':'Fixture note '+str(index)},'editable':[],'checks':[]},authorize_start=True,budget_seconds=600)['activity']
  tasks.append(activity['id'])
 offer=core.growth_control('offer','opportunities',{'task_ids':tasks,'budget_seconds':300,'maximum_reviews':1})['offer']
 return now,offer['id']


@pytest.mark.parametrize('kind',['practice','exploration'])
@pytest.mark.parametrize('invalidate',['owner','expiry','ownership'])
def test_actual_quiet_review_yields_and_cancels_without_generation(actual,monkeypatch,kind,invalidate):
 from gamma.coordination.store import OwnershipLost
 core=actual.app.state.core;now,offer_id=prepare_quiet(core,kind,monkeypatch)
 label='quiet-'+kind;pool=ThreadPoolExecutor(max_workers=2)
 try:
  with backend_admission.backend_slot(ENDPOINT,foreground=True,timeout=5):
   quiet=pool.submit(core.review)
   assert actual.admitted[label].wait(2),'Quiet review did not reach actual local adapter admission'
   if invalidate=='owner':
    owner=pool.submit(actual.client.post,'/v1/coordination/input',json={'audience':'owner_private','request_id':'owner-now','text':'phone-current'})
    assert actual.admitted['phone-current'].wait(2)
   elif invalidate=='expiry':now[0]+=600
   else:core.store.claim(core.token[0])
   if invalidate=='ownership':
    with pytest.raises(OwnershipLost):quiet.result(timeout=2)
   else:assert quiet.result(timeout=2) is None
   assert actual.priorities[label] is False,'Quiet work must yield to foreground requests'
   assert actual.http==[],'Cancelled quiet review reached actual provider HTTP'
  if invalidate=='owner':
   response=owner.result(timeout=3);assert response.status_code==200,response.text
   assert actual.http==[{'kind':'primary','label':'phone-current'},{'kind':'review','label':'phone-current'}]
  if invalidate!='ownership':
   assert not core.store.read('brain','state').get('last_decision') or invalidate=='owner'
   if kind=='practice':
    assert core.store.read('brain','state')['permit']['remaining_reviews']==2
   else:
    assert core.store.read('growth_opportunity',offer_id)['remaining_reviews']==0
    with core.store.transaction(core.token) as db:assert core.store.list(db,'growth_choice',None)==[]
 finally:pool.shutdown(wait=True)



def test_actual_waiting_exploration_cancels_when_selected_source_is_forgotten(actual,monkeypatch):
 core=actual.app.state.core;_now,offer_id=prepare_quiet(core,'exploration',monkeypatch)
 source=core.growth_state()['exploration']['tasks'][0]['source_observations']['source_ids'][0]
 pool=ThreadPoolExecutor(max_workers=1)
 try:
  with backend_admission.backend_slot(ENDPOINT,foreground=True,timeout=5):
   future=pool.submit(core.review)
   assert actual.admitted['quiet-exploration'].wait(2)
   core.personal_control('forget-selected','forget',{'target_id':source})
   assert future.result(timeout=2) is None
   assert actual.http==[]
  assert core.store.read('growth_opportunity',offer_id)['remaining_reviews']==0
  with core.store.transaction(core.token) as db:assert core.store.list(db,'growth_choice',None)==[]
 finally:pool.shutdown(wait=True)


@pytest.mark.parametrize('kind',['practice','exploration'])
def test_genuine_quiet_provider_failure_still_propagates(actual,monkeypatch,kind):
 from gamma.errors import ExternalServiceError
 core=actual.app.state.core;prepare_quiet(core,kind,monkeypatch)
 def unavailable(*args,**kwargs):raise error.URLError('controlled provider unavailable')
 monkeypatch.setattr(local_adapter.request,'urlopen',unavailable)
 with pytest.raises(ExternalServiceError):core.review()
 assert core._review_lock.acquire(blocking=False)
 core._review_lock.release()



@pytest.mark.parametrize('kind',['practice','exploration'])
def test_current_actual_quiet_review_still_runs_once_with_reserved_budget(actual,monkeypatch,kind):
 core=actual.app.state.core;_now,offer_id=prepare_quiet(core,kind,monkeypatch)
 core.review()
 label='quiet-'+kind
 assert actual.priorities[label] is False
 assert actual.http==[{'kind':'primary','label':label},{'kind':'review','label':label}]
 core.review()
 assert len(actual.http)==2,'Quiet review must not replay before its reserved reconsideration/budget'
 if kind=='practice':
  assert core.store.read('brain','state')['permit']['remaining_reviews']==2
 else:
  assert core.store.read('growth_opportunity',offer_id)['remaining_reviews']==0
  with core.store.transaction(core.token) as db:
   choices=core.store.list(db,'growth_choice',None)
   assert len(choices)==1 and choices[0]['accepted'] and choices[0]['action']=='wait'
