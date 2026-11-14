"""Mixed-channel stale inference exits through actual backend admission, without HTTP."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import threading

import pytest

from gamma.coordination.phone import PhoneAccess
from gamma.coordination.store import OwnershipLost
from gamma.coordination.personal import identifier
from gamma.llm import backend_admission
from gamma.llm.local_adapter import current_local_admission,local_inference_admission
from gamma.schemas.response import AssistantResponse,BoundedStateUpdates
from test_coordination_core import make_core
from test_private_interaction import setup as voice_setup,observation

ENDPOINT='http://127.0.0.1:17191'


def response(text):
 return AssistantResponse(display_text='Answer '+text,delivery_mode='text_only',
                          state_updates=BoundedStateUpdates(active_topic=text))


def test_mixed_voice_monitor_phone_skips_superseded_waiter_and_old_review(tmp_path,monkeypatch):
 monkeypatch.setattr(backend_admission,'_coordination_root',lambda:tmp_path/'admission')
 entered={name:threading.Event() for name in ('voice-old','monitor-stale','phone-current')}
 primary_held=threading.Event();release=threading.Event();generated=[];reviewed=[]
 def draft(text,context):
  entered[text].set();foreground,checkpoint=current_local_admission()
  with backend_admission.backend_slot(ENDPOINT,foreground=foreground,timeout=5,checkpoint=checkpoint):
   if text=='voice-old':primary_held.set();assert release.wait(5)
   generated.append(text)
  # This is the reviewer's existing inherited-checkpoint contract.
  with local_inference_admission(foreground=True):
   priority,inherited=current_local_admission()
   with backend_admission.backend_slot(ENDPOINT,foreground=priority,timeout=5,checkpoint=inherited):
    reviewed.append(text)
  return response(text)
 core,voice=voice_setup(tmp_path/'state',draft=draft)
 phone=PhoneAccess(core);secret='S'*43
 phone.register('register','phone',secret);epoch=phone.connect('phone',secret,'connect')['device']['epoch']
 pool=ThreadPoolExecutor(max_workers=3)
 try:
  first=pool.submit(voice.observe,observation('voice-first',text='voice-old'))
  assert primary_held.wait(2)
  second=pool.submit(core.input,'monitor-stale','monitor-private','monitor-second')
  assert entered['monitor-stale'].wait(2)
  third=pool.submit(phone.send,'phone',secret,epoch,'phone-third','phone-current')
  assert entered['phone-current'].wait(2)
  # The old queued request must exit while the first inference is still held.
  assert second.result(timeout=2)['result']['assistant_response']['reason_code']=='superseded'
  assert core.store.read('turn','monitor-second')['status']=='interrupted'
  assert generated==[] and reviewed==[]
  release.set()
  assert first.result(timeout=3)['status'] in {'deferred','cancelled'}
  latest=third.result(timeout=3)
  assert latest['status']=='completed' and latest['response']['display_text']=='Answer phone-current'
  assert generated==['voice-old','phone-current']
  assert reviewed==['phone-current']
  assert voice.status('desktop')['outputs']==[]
  assert core.store.read('brain','state')['working_notes']['active_topic']=='phone-current'
  duplicate=core.input('monitor-stale','monitor-private','monitor-second')
  assert duplicate['result']['assistant_response']['reason_code']=='interrupted'
  assert generated==['voice-old','phone-current']
 finally:
  release.set();pool.shutdown(wait=True);voice.close();core.close()


@pytest.mark.parametrize('invalidation',['sleep','forget','ownership'])
def test_waiting_turn_checkpoint_respects_sleep_forget_and_owner_loss(tmp_path,monkeypatch,invalidation):
 monkeypatch.setattr(backend_admission,'_coordination_root',lambda:tmp_path/'admission')
 waiting=threading.Event();generated=[]
 def draft(text,context):
  foreground,checkpoint=current_local_admission();waiting.set()
  with backend_admission.backend_slot(ENDPOINT,foreground=foreground,timeout=5,checkpoint=checkpoint):
   generated.append(text)
  return response(text)
 core=make_core(tmp_path/'state',draft=draft);pool=ThreadPoolExecutor(max_workers=1)
 try:
  with backend_admission.backend_slot(ENDPOINT,foreground=True,timeout=5):
   future=pool.submit(core.input,'private question','monitor-private','waiting-turn')
   assert waiting.wait(2)
   if invalidation=='sleep':core.control('sleep','sleep')
   elif invalidation=='forget':core.personal_control('forget','forget',{'target_id':identifier('src','turn','waiting-turn')})
   else:core.store.claim(core.token[0])
   if invalidation=='ownership':
    with pytest.raises(OwnershipLost):future.result(timeout=2)
   else:
    assert future.result(timeout=2)['result']['assistant_response']['reason_code']=='superseded'
   assert generated==[]
  state=core.store.read('turn','waiting-turn')
  if invalidation=='sleep':
   assert state['status']=='queued' and state['queue_reason']=='sleep_interrupted_draft'
   core.control('wake','wake');core._drain_queue()
   assert generated==['private question']
   assert core.store.read('turn','waiting-turn')['status']=='completed'
  elif invalidation=='forget':assert state['redacted'] and state['status']=='redacted'
  else:assert state['status']=='recoverable'
 finally:
  pool.shutdown(wait=True);core.close()
