"""Actual private composition, explicit manifest pins and shared model scopes."""
from copy import deepcopy
from types import SimpleNamespace
import json
import pytest
from fastapi.testclient import TestClient
from gamma.coordination.inference_profile import release_inference_options, validate_inference_runtime
from gamma.coordination import private_release_manifest as contract
from gamma.coordination.app import configured_app
from gamma.llm.local_runtime import current_local_runtime_profile
from gamma.schemas.response import AssistantResponse
from test_private_release import packet

PROFILE={'name':'gamma_warm_local_v1','review_model':'gemma4:e4b'}


def configured():
 return SimpleNamespace(speech_filter_llm_model='gemma4:e4b',local_llm_tagging_model='',local_llm_light_model='',speech_filter_llm_enabled=True,local_llm_endpoint='http://127.0.0.1:11434')


def test_profile_is_explicit_finite_and_cannot_change_reviewer_or_routes(packet):
 assert release_inference_options(None)=={}
 contract.validate_manifest(packet)
 p=deepcopy(packet);p['inference_profile']=dict(PROFILE);contract.validate_manifest(p)
 options=release_inference_options(PROFILE)
 assert options['primary_keep_alive']=='30m' and options['review_keep_alive']=='30m'
 assert options['review_num_gpu']==32 and options['review_num_threads']==8
 assert options['review_endpoint']=='http://127.0.0.1:11435'
 assert p['models']==packet['models'] and p['effects']==packet['effects']
 for value in (None,True,'gamma_warm_local_v1',{}, {**PROFILE,'endpoint':'https://public.invalid'}, {**PROFILE,'review_model':'different'}, {**PROFILE,'name':'other'}):
  invalid=deepcopy(packet);invalid['inference_profile']=value
  with pytest.raises(ValueError):contract.validate_manifest(invalid)


def test_runtime_rejects_changed_or_disabled_independent_review():
 assert validate_inference_runtime(PROFILE,configured())==release_inference_options(PROFILE)
 for key,value in [('speech_filter_llm_enabled',False),('speech_filter_llm_model','qwen3.8:27b'),('local_llm_endpoint','http://public.invalid')]:
  settings=configured();setattr(settings,key,value)
  with pytest.raises(ValueError):validate_inference_runtime(PROFILE,settings)
 assert validate_inference_runtime(None,configured())=={}


@pytest.mark.parametrize('enabled',[False,True])
def test_actual_http_text_composition_uses_only_trusted_profile(tmp_path,monkeypatch,enabled):
 from gamma.config import settings
 seen=[]
 class ControlledConversation:
  def respond(self,text,**kwargs):
   seen.append(current_local_runtime_profile())
   return AssistantResponse(display_text='Controlled checked output.',delivery_mode='text_only')
 monkeypatch.setattr('gamma.conversation.service.ConversationService',ControlledConversation)
 for name,value in vars(configured()).items():monkeypatch.setattr(settings,name,value)
 monkeypatch.setattr(settings,'llm_provider','local');monkeypatch.setenv('GAMMA_PRACTICE_MODEL','qwen3.8:27b')
 profile=PROFILE if enabled else None
 app=configured_app(tmp_path,tmp_path/'data/coordination/fixture',bearer_token='fixture-auth',run_background=False,inference_profile=profile)
 with TestClient(app) as client:
  r=client.post('/v1/coordination/input',headers={'Authorization':'Bearer fixture-auth'},json={'audience':'owner_private','request_id':'profile-text','session_id':'technical','text':'Who am I?'})
  assert r.status_code==200,r.text
  assert r.json()['result']['assistant_response']['display_text']=='Controlled checked output.'
 assert seen==[release_inference_options(profile)]
 assert current_local_runtime_profile()=={}


def test_selected_frame_background_uses_same_profile_without_action_authority(monkeypatch):
 from gamma.coordination import shared_vision
 from gamma.config import settings
 from gamma.llm.base import LLMReply
 from gamma.llm.local_adapter import current_local_admission
 seen=[]
 class Perception:
  def __init__(self,**kwargs):pass
  def generate_reply(self,**kwargs):
   seen.append((current_local_runtime_profile(),current_local_admission()[0],kwargs['call_context'].purpose))
   return LLMReply(text=json.dumps({'summary':'Controlled visible label','confidence':.5}))
 monkeypatch.setattr(settings,'local_llm_supports_vision',True)
 monkeypatch.setattr('gamma.llm.local_adapter.LocalLLMAdapter',Perception)
 analyze=shared_vision.local_image_analyzer(runtime_options=release_inference_options(PROFILE))
 assert analyze.background(b'controlled','image/png','osrs_screen')['confidence']==.5
 assert seen==[(release_inference_options(PROFILE),False,'vision_analysis')]
 assert current_local_runtime_profile()=={}
