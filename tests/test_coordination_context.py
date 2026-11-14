"""The real prompt budget and repair path must retain bounded coordination context."""
from types import SimpleNamespace
from gamma.conversation.service import ConversationService
from gamma.conversation.turn_contract import StructuredTurnParser
from gamma.llm.context_budget import ContextBudgetManager, prompt_layers_from_system_prompt
from gamma.llm.capabilities import estimate_text_tokens
from gamma.schemas.conversation import RecipientContext
from gamma.schemas.response import EmotionTag
from gamma.schemas.response import BoundedStateUpdates
import pytest
from gamma.conversation.turn_contract import structured_turn_instruction
from typing import get_args
import json
from test_coordination_core import make_core


def test_coordination_scope_and_situation_are_required_under_budget_pressure():
    text = '# Core Persona\nShana\n# Private Coordination Context\nowner_private focused permit retry_budget\n# Style\n' + 'optional ' * 2000
    layers = prompt_layers_from_system_prompt(text)
    assert any(l.name == 'Private Coordination Context' and l.required for l in layers)
    result = ContextBudgetManager().build(layers=layers, user_text='hello', usable_input_tokens=700)
    assert 'owner_private focused permit retry_budget' in result.system_prompt
    assert 'drop:Style' in result.compaction_actions


def test_structured_repair_uses_bounded_builder_and_reserves_suffix():
    service = ConversationService.__new__(ConversationService)
    service._turn_parser = StructuredTurnParser()
    seen = []
    def builder(budget):
        seen.append(budget)
        return '# Private Coordination Context\nowner_private permit retry_budget', 'hello', {'included_layers': ['Private Coordination Context']}
    def generate(**values):
        system, user, _ = values['call_context'].prompt_builder(700)
        assert 'owner_private permit retry_budget' in system
        assert 'Regenerate the decision once as exact JSON' in user
        assert estimate_text_tokens(system, user) <= 700
        return SimpleNamespace(text='{"action":"reply","requested_delivery":"text_only","final_text":"Here.","reason_code":"reply"}')
    service._llm = SimpleNamespace(generate_reply=generate)
    service._llm_adapter = lambda: service._llm
    result = service._parse_turn_result(raw='{bad', system_prompt='large ' * 10000, user_text='hello',
        legacy_delivery='text_only', delivery_context='direct_text', image=None,
        recipient=RecipientContext('owner_private', 'dashboard_monitor', True), prompt_builder=builder)
    assert result.draft.final_text == 'Here.' and seen and seen[0] < 700


def test_required_context_stays_bounded_with_several_full_activity_records(tmp_path):
    core = make_core(tmp_path)
    try:
        core.control('scope', 'permit', exercise_ids=['retry_budget', 'message_chunks'])
        task = {'id': 'retry_budget', 'status': 'needs_checker', 'reason': 'attempt_limit', 'attempt_count': 3,
                'checks_passed': False, 'failed_public_checks': ['regression', 'feature'], 'independent_evaluation_passed': False}
        with core.store.transaction(core.token) as db:
            for number in range(6):
                core.store.put(db, 'activity', 'practice-' + str(number), {
                    'id': 'practice-' + str(number), 'goal': 'message_chunks', 'origin': 'owner_request',
                    'created_at': number, 'status': 'needs_checker', 'worker_run': 'practice-' + str(number),
                    'requested_goal': 'x' * 1000, 'task_results': [task] * 4,
                    'reason': 'bounded_practice_failed', 'next_step': 'review_practice_evidence'})
            context = json.loads(core._context(db, 'private'))
        context.pop('recent_exchanges')
        assert len(json.dumps(context)) <= 8000
        assert len(context['activities']) == 6
        assert context['authorized_practice']['exercise_ids'] == ['retry_budget', 'message_chunks']
    finally:
        core.close()


def test_prompt_declares_emotion_schema_and_repair_names_fields_without_payload():
    prompt = structured_turn_instruction(default_delivery='text_only', coordination=True)
    assert all(tag in prompt for tag in get_args(EmotionTag))
    service = ConversationService.__new__(ConversationService)
    service._turn_parser = StructuredTurnParser()
    def generate(**values):
        assert 'emotion' in values['user_text'] and 'literal_error' in values['user_text']
        assert 'UNSUPPORTED_PRIVATE_VALUE' not in values['user_text']
        return SimpleNamespace(text='{"action":"reply","requested_delivery":"text_only","final_text":"Here.","emotion":"neutral"}')
    service._llm_adapter = lambda: SimpleNamespace(generate_reply=generate)
    result = service._parse_turn_result(raw='{"action":"reply","requested_delivery":"text_only","final_text":"Here.","emotion":"UNSUPPORTED_PRIVATE_VALUE"}',
        system_prompt=prompt, user_text='hello', legacy_delivery='text_only', delivery_context='direct_text', image=None)
    assert result.draft.final_text == 'Here.'


def test_wait_cannot_masquerade_as_a_durable_exercise_request():
    with pytest.raises(ValueError, match='wait cannot select'):
        BoundedStateUpdates(activity_action='wait', practice_exercise='message_chunks', deferred_intention='do it next')
    assert BoundedStateUpdates(activity_action='wait').practice_exercise is None


def test_private_schema_and_parser_keep_action_delivery_pairs_coherent():
    from gamma.coordination.turn_schema import private_turn_schema
    from gamma.conversation.turn_contract import TurnContractError
    schema = private_turn_schema()
    parser = StructuredTurnParser()
    state = schema['$defs']['PersonalStateUpdates']
    assert set(state['required']) == set(state['properties'])
    assert 'resume' in state['properties']['activity_action']['enum']
    assert 'anyOf' not in state['properties']['activity_action']
    assert {tuple(v['properties']['action']['enum']) for v in schema['oneOf']} == {
        ('reply', 'acknowledge'), ('stay_silent',), ('defer',)}
    for variant in schema['oneOf']:
        props = variant['properties']
        for name in ('tool_calls', 'memory_candidates', 'voice_styles', 'motions'):
            assert props[name]['maxItems'] == 0
        for action in props['action']['enum']:
            draft = dict(action=action, requested_delivery=props['requested_delivery']['const'],
                         final_text='Here.' if action in {'reply', 'acknowledge'} else '')
            assert parser.parse(json.dumps(draft), legacy_delivery='text_only').draft.action == action
    for invalid in [dict(action='resume', requested_delivery='text_only', final_text='Here.'),
                    dict(action='stay_silent', requested_delivery='text_only', final_text=''),
                    dict(action='reply', requested_delivery='text_only', final_text='   ')]:
        with pytest.raises(TurnContractError):
            parser.parse(json.dumps(invalid), legacy_delivery='text_only')
