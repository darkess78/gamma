"""Trusted private JSON compaction preserves decoded state and default prompts."""
import ast
from copy import deepcopy
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from gamma.coordination import app as coordination_app
from gamma.coordination.app import configured_app
from gamma.llm.local_runtime import current_local_runtime_profile
from gamma.persona.coordination import build_coordination_prompt
from gamma.schemas.conversation import RecipientContext
from gamma.schemas.response import AssistantResponse
from test_conversation_recipient import fixture_state, PRIVATE
from test_private_inference_profile import PROFILE, configured

MARKERS = ('# Core Persona\nCurrent authored identity: ', '\nAuthored provenance: ',
           '# Approved scoped trial instructions\n', '# Sourced observations\n',
           '# Recent Conversation Turns\n')
IDENTITY = {'id':'self','fields':{'name':'Shana','core_personality':'Candid and independent.',
            'speech_style':'Keep  literal  spaces; café and quoted # Unknown observations\n{ }.'},
            'revision':2,'field_sources':{'speech_style':'src-fixture'}}


def state():
    return {'awake':True,'focus':'focused','mood':'neutral','working_notes':{},
        'personal':{'identity':deepcopy(IDENTITY),
            'owner':{'name':'Fixture Owner','source_id':'src-owner','name_confirmed':True},
            'recall':[{'id':'mem-one','source_id':'src-one','text':'A  sourced  fact.',
                       'provenance':{'report_is_not_verified_fact':True}}],
            'unfinished_and_decisions':[{'id':'mem-two','source_id':'src-two','text':'A commitment.'}]},
        'growth':{'guidance':[{'trial_id':'trial-one','version':1,'instructions':'A  bounded  trial.'}]},
        'recent_exchanges':[{'user':'Earlier  question?','assistant':'Earlier  checked reply.'}],
        'interaction':{'channel':'discord_voice','audience':'owner_private'},
        'authority':{'allowed':['bounded discussion'],'forbidden':['activation','publication']}}


def capture_actual_draft(context, compact, *, stable=False):
    tree=ast.parse(Path(coordination_app.__file__).read_text())
    draft=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='draft')
    conversation=SimpleNamespace(_private_compact_json=compact,_private_stable_prefix=stable,respond=lambda text,**kw:kw)
    scope={**vars(coordination_app),'conversation':conversation,'RecipientContext':RecipientContext,
           'runtime_label':inspect.signature(configured_app).parameters['runtime_label'].default}
    exec(compile(ast.Module(body=[draft],type_ignores=[]),'<actual-json-draft>','exec'),scope)
    return scope['draft']('Current owner question?',json.dumps(context,ensure_ascii=False))


def decode_sections(prompt):
    spans=[]
    for marker in MARKERS:
        assert prompt.count(marker)==1
        start=prompt.index(marker)+len(marker)
        value,end=json.JSONDecoder().raw_decode(prompt[start:])
        spans.append((start,start+end,value))
    spans.sort()
    values=[];outside=[];last=0
    for start,end,value in spans:
        values.append(value);outside.append(prompt[last:start]);last=end
    outside.append(prompt[last:])
    return values,outside


def test_actual_draft_compacts_five_json_values_without_changing_any_decoded_field_or_prose():
    original=state();before=capture_actual_draft(original,False);after=capture_actual_draft(original,True)
    assert original==state()
    assert 'compact_json' not in before and after.get('compact_json') is True
    full=build_coordination_prompt(before['coordination_persona'],before['background_context'])
    compact=build_coordination_prompt(after['coordination_persona'],after['background_context'],compact_json=True)
    full_values,full_outside=decode_sections(full);compact_values,compact_outside=decode_sections(compact)
    assert compact_values==full_values and compact_outside==full_outside
    assert len(compact)<len(full)
    assert compact_values[3]['authority']==original['authority']
    assert compact_values[3]['personal']['owner']==original['personal']['owner']
    assert compact_values[3]['personal']['recall']==original['personal']['recall']
    assert compact_values[3]['personal']['unfinished_and_decisions']==original['personal']['unfinished_and_decisions']
    assert 'First honor current explicit quiet' in compact
    assert 'Only the current coordinator scope permits actions.' in compact


@pytest.mark.parametrize('enabled',[False,True])
def test_actual_http_only_validated_profile_enables_compact_serialization(tmp_path,monkeypatch,enabled):
    from gamma.config import settings
    seen=[]
    class ControlledConversation:
        def respond(self,text,**kw):
            seen.append((self._private_compact_json,kw,current_local_runtime_profile()))
            return AssistantResponse(display_text='Controlled reply.',delivery_mode='text_only')
    monkeypatch.setattr('gamma.conversation.service.ConversationService',ControlledConversation)
    for name,value in vars(configured()).items():monkeypatch.setattr(settings,name,value)
    monkeypatch.setattr(settings,'llm_provider','local')
    monkeypatch.setattr(settings,'local_llm_supports_vision',False)
    monkeypatch.setenv('GAMMA_PRACTICE_MODEL','fictional:1b')
    app=configured_app(tmp_path,tmp_path/'data/coordination/fixture',bearer_token='fixture-owner',
                       run_background=False,inference_profile=PROFILE if enabled else None)
    with TestClient(app) as client:
        client.headers['Authorization']='Bearer fixture-owner'
        body={'audience':'owner_private','request_id':'owner-question','text':'Who am I?'}
        assert client.post('/v1/coordination/input',json={**body,'compact_json':True}).status_code==422
        assert client.post('/v1/coordination/input',json=body).status_code==200
    selected,kw,runtime=seen[-1]
    assert selected is enabled and kw.get('compact_json',False) is enabled
    assert ('compact_json' in kw) is enabled
    assert 'compact_json' not in runtime
    assert current_local_runtime_profile()=={}


def test_compact_flag_reaches_real_service_generation_and_exact_budget_renderer(fixture_state):
    values=capture_actual_draft(state(),True)
    expected=build_coordination_prompt(values['coordination_persona'],values['background_context'],compact_json=True)
    seen=[]
    def generate(**kw):
        seen.append(kw['system_prompt'])
        bounded,user,_=kw['call_context'].prompt_builder(6144)
        assert bounded==expected and user=='Current owner question?'
        return SimpleNamespace(text='{"action":"reply","requested_delivery":"text_only","final_text":"A checked reply."}')
    fixture_state.service._llm=SimpleNamespace(generate_reply=generate)
    response=fixture_state.service.respond('Current owner question?',fast_mode=True,evaluation_mode=True,
        coordination_mode=True,coordination_persona=values['coordination_persona'],
        background_context=values['background_context'],recipient=PRIVATE,compact_json=True)
    assert response.display_text=='A checked reply.' and seen==[expected]


@pytest.mark.parametrize('value',[None,1,'true'])
def test_compact_json_requires_an_explicit_boolean(value):
    with pytest.raises(ValueError,match='compact_json_requires_boolean'):
        build_coordination_prompt(IDENTITY,'',compact_json=value)
