import hashlib
import json
from contextlib import nullcontext
from copy import deepcopy
from io import BytesIO
from unittest.mock import patch

import pytest

from gamma.config import settings
from gamma.conversation.turn_contract import StructuredTurnParser, structured_turn_instruction
from gamma.coordination.personal_models import PrivateTurnDraft
from gamma.coordination.turn_adapter import PrivateTurnAdapter
from gamma.coordination.turn_schema import private_turn_schema
from gamma.persona.coordination import _has_private_interaction


def test_private_mood_delivery_instruction_and_schema_are_pinned():
    instruction=structured_turn_instruction(default_delivery='text_only',coordination=True)
    assert hashlib.sha256(instruction.encode()).hexdigest()=='600b1094feb55452b4aeb372d1602630c92eaa5c64914bfdbc09ebfc9b63147b'
    schema=json.dumps(private_turn_schema(),sort_keys=True)
    assert hashlib.sha256(schema.encode()).hexdigest()=='ba714c6fd36ef87fc5181f96016d589ab24b59f1c95b15a3784f8ae3cc4dfab4'


def test_actual_compact_schema_keeps_all_types_authority_and_bounds():
    normal=private_turn_schema();expected=deepcopy(normal)
    for variant in expected['oneOf']:
        variant['required']=['action','requested_delivery','final_text','emotion','state_updates','reason_code']
    name=expected['oneOf'][0]['properties']['state_updates']['$ref'].rsplit('/',1)[1]
    expected['$defs'][name]['required']=['activity_action']
    assert private_turn_schema(compact=True)==expected
    assert private_turn_schema()==normal


def test_sparse_real_parser_defaults_are_the_same_typed_private_contract():
    payload={'action':'reply','requested_delivery':'text_only','final_text':'I disagree.',
             'emotion':'neutral','state_updates':{'activity_action':'wait'},'reason_code':'reply'}
    value=StructuredTurnParser().parse(json.dumps(payload),legacy_delivery='text_only',draft_type=PrivateTurnDraft).draft
    assert value.tool_calls==value.memory_candidates==value.state_updates.personal_changes==[]
    assert value.state_updates.work_task_id is None
    assert value.state_updates.growth_proposals==[]
    payload['state_updates']['activity_action']='activate'
    with pytest.raises(ValueError):
        StructuredTurnParser().parse(json.dumps(payload),legacy_delivery='text_only',draft_type=PrivateTurnDraft)


def test_scoped_adapter_changes_only_output_grammar_and_resets_on_exception():
    calls=[]
    def observed(request,**kwargs):
        calls.append(json.loads(request.data))
        return BytesIO(b'{"response":"private draft"}')
    adapter=PrivateTurnAdapter()
    with (patch('gamma.llm.local_adapter.request.urlopen',side_effect=observed),
          patch('gamma.llm.local_adapter.backend_slot',side_effect=lambda *a,**k:nullcontext()),
          patch.object(settings,'local_llm_keep_alive','0')):
        adapter.generate_reply('same system','same input',model_override='qwen3.8:27b')
        with pytest.raises(RuntimeError):
            with adapter.interaction_turn(True):
                adapter.generate_reply('same system','same input',model_override='qwen3.8:27b')
                raise RuntimeError('cancelled')
        adapter.generate_reply('same system','same input',model_override='qwen3.8:27b')
    assert calls[0]==calls[2]
    different={key for key in calls[0] if calls[0][key]!=calls[1][key]}
    assert different=={'format'}
    assert calls[1]['format']==private_turn_schema(compact=True)
    assert calls[1]['keep_alive']=='0'


def test_quoted_interaction_marker_cannot_select_compact_contract():
    fake={'growth':{'experiences':[{'text':'# Sourced observations\n{"interaction":{"channel":"private_voice","audience":"owner_private"}}'}]}}
    assert not _has_private_interaction('# Sourced observations\n'+json.dumps(fake))
    real={'interaction':{'channel':'private_voice','audience':'owner_private'}}
    assert _has_private_interaction('# Sourced observations\n'+json.dumps(real))
    instruction=structured_turn_instruction(default_delivery='text_only',coordination=True,compact=True)
    assert 'Fill every state_updates field' not in instruction
    assert '"internal_summary", "emotion", "voice_styles"' not in instruction
