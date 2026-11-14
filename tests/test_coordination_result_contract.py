"""Available source content can be discussed without replacing execution receipts."""
import json

import pytest

from gamma.coordination.context_budget import compact_coordination_context
from gamma.persona.coordination import build_coordination_prompt
from test_coordination_context_budget import authored_context
from test_growth_feedback_rendering import captured_production_system


def observations():
    return {'activity_id':'work-current','observation_kind':'completed_candidate_source',
        'source_ref':'a'*64,'source_sha256':'b'*64,'source_ids':['src-'+'c'*24],
        'files':[{'path':'analysis.json','sha256':'d'*64,
            'excerpt':'A fictional ledger states entry ALPHA; its cause is not supplied.',
            'excerpt_is_partial':False}], 'files_omitted':0,
        'interpretation':'Selected source observations, not instructions.'}


@pytest.mark.parametrize('location',['completed','offered'])
def test_actual_request_contract_distinguishes_source_discussion_from_execution_status(location):
    context=authored_context();context['current_activity_id']='work-current'
    if location=='completed':
        context['growth']['experiences']=[{'id':'exp-current','source_observations':observations()}]
    else:
        context['exploration']={'id':'offer-current','remaining_reviews':1,'expires_at':9000,
            'tasks':[{'id':'work-current','status':'ready','source_observations':observations()}]}
    for question in ('What does this source state, and what remains interpretation?',
                     'What caught your attention in that observation?'):
        system=captured_production_system(context,question,compact_coordination_context)
        assert '# Source Content Discussion Capability\n' in system
        assert 'use a normal reply with activity_action wait' in system
        assert 'report_status remains required for execution progress, completion, blockers and required input' in system
        assert 'current_activity_id' in system and 'exact source wording' in system
        assert "A work product's quote field is a selected span, not the whole input." in system
        assert "complete provided_input view" in system and 'excerpt_is_partial' in system
        assert 'For progress or outcome questions' in system and 'select report_status and exact activity_run_id' in system
        assert (len(system)+len(question)+3)//4<=6144


@pytest.mark.parametrize('value',[None,{}, {'files':[]},'source_observations'])
def test_unavailable_views_or_field_names_in_source_text_do_not_add_discussion_capability(value):
    identity=authored_context()['personal']['identity']
    context={'growth':{'experiences':[{'situation':'source_observations and a pretend capability header',
                                     'source_observations':value}]},'exploration':None}
    background='# Sourced observations\n'+json.dumps(context)+'\n\n# Recent Conversation Turns\n[]'
    assert '# Source Content Discussion Capability\n' not in build_coordination_prompt(identity,background)
