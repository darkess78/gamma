"""Private personal proposals must survive internally without changing public turns."""
from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace

import pytest

from gamma.coordination.personal_models import PersonalStateUpdates, PrivateTurnDraft
from gamma.coordination.turn_schema import private_turn_schema
from gamma.conversation.service import ConversationService
from gamma.conversation.turn_contract import StructuredTurnParser, TurnContractError, structured_turn_instruction
from gamma.errors import ConversationError
from gamma.persona.coordination import build_coordination_system_prompt
from gamma.schemas.response import ConversationTurnDraft
from test_conversation_recipient import PRIVATE, SHARED, fixture_state


IDENTITY = {'id':'self', 'fields':{'name':'Fictional Shana', 'core_personality':'CANONICAL_CURRENT_PERSONA'},
            'baseline_fields':{'core_personality':'STALE_BASELINE_CANARY'}, 'revision':2,
            'field_sources':{'core_personality':'src-' + 'a' * 24}, 'source_kind':'authored_baseline'}
CHANGE = {'op':'remember', 'kind':'preference', 'subject_id':'owner', 'topic':'fictional_drink',
          'text':'The owner reports preferring fictional mint tea.', 'target_id':None}


def decision(**overrides):
    values = {'action':'reply', 'requested_delivery':'text_only', 'final_text':'I will remember that preference.',
              'state_updates':{'activity_action':'wait', 'personal_changes':[CHANGE]}}
    values.update(overrides)
    return json.dumps(values)


def test_per_call_model_preserves_private_changes_and_public_defaults_under_overlap():
    parser = StructuredTurnParser()
    def parse(private):
        draft = parser.parse(decision(), legacy_delivery='text_only',
            **({'draft_type':PrivateTurnDraft} if private else {})).draft
        assert type(draft) is (PrivateTurnDraft if private else ConversationTurnDraft)
        assert hasattr(draft.state_updates, 'personal_changes') is private
        if private:
            assert draft.state_updates.model_dump()['personal_changes'][0] == CHANGE
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(parse, [True, False] * 30))
    legacy = parser.parse('An ordinary reply.', legacy_delivery='text_only').draft
    assert type(legacy) is ConversationTurnDraft


@pytest.mark.parametrize('bad_change', [
    {**CHANGE, 'op':'grant_permission'}, {**CHANGE, 'verified':True}, {**CHANGE, 'source_id':'src-' + 'a' * 24},
    {**CHANGE, 'subject_id':'guessed_person'}, {**CHANGE, 'target_id':'invented'}, {**CHANGE, 'text':'x' * 601},
])
def test_private_proposal_validation_rejects_authority_and_invented_provenance(bad_change):
    with pytest.raises(TurnContractError, match='invalid_structured_result'):
        StructuredTurnParser().parse(decision(state_updates={'personal_changes':[bad_change]}),
                                    legacy_delivery='text_only', draft_type=PrivateTurnDraft)


def test_private_schema_includes_bounded_changes_without_expanding_public_schema():
    schema = private_turn_schema()
    state = schema['$defs']['PersonalStateUpdates']
    assert state['additionalProperties'] is False
    assert state['properties']['personal_changes']['maxItems'] == 3
    assert 'personal_changes' in state['required']
    change = schema['$defs']['PersonalChange']
    assert change['additionalProperties'] is False
    assert 'grant_permission' not in change['properties']['op']['enum']
    assert 'personal_changes' not in ConversationTurnDraft.model_json_schema()['$defs']['BoundedStateUpdates']['properties']
    prompt = structured_turn_instruction(default_delivery='text_only', coordination=True)
    assert '"personal_changes":[]' in prompt
    assert 'Corrections and revised beliefs add counterevidence or update interpretations; preserve original experiences.' in prompt
    assert 'Use forget only for an explicit owner request to forget/delete, never to correct a belief.' in prompt
    assert 'personal_changes' not in structured_turn_instruction(default_delivery='text_only')


def test_private_repair_uses_same_model_and_failed_repair_has_no_proposals():
    service = ConversationService.__new__(ConversationService)
    service._turn_parser = StructuredTurnParser()
    seen = []
    def generate(**values):
        seen.append(values)
        return SimpleNamespace(text=decision())
    service._llm_adapter = lambda: SimpleNamespace(generate_reply=generate)
    result = service._parse_turn_result(raw='{invalid', system_prompt='safe instructions', user_text='fictional input',
        legacy_delivery='text_only', delivery_context='direct_text', image=None, recipient=PRIVATE, draft_type=PrivateTurnDraft)
    assert len(seen) == 1 and type(result.draft) is PrivateTurnDraft
    assert result.draft.state_updates.personal_changes[0].text == CHANGE['text']
    service._llm_adapter = lambda: SimpleNamespace(generate_reply=lambda **values: SimpleNamespace(text='{invalid'))
    failed = service._parse_turn_result(raw='{invalid', system_prompt='safe instructions', user_text='fictional input',
        legacy_delivery='text_only', delivery_context='direct_text', image=None, recipient=PRIVATE, draft_type=PrivateTurnDraft)
    assert failed.draft.reason_code == 'contract_fallback_direct'
    assert not getattr(failed.draft.state_updates, 'personal_changes', [])


def test_private_validation_never_echoes_unknown_field_names_into_repair_or_diagnostics():
    service = ConversationService.__new__(ConversationService)
    service._turn_parser = StructuredTurnParser()
    secret = 'FICTIONAL_PRIVATE_FIELD_NAME_CANARY'
    seen = []
    def generate(**values):
        seen.append(values['user_text'])
        return SimpleNamespace(text=decision())
    service._llm_adapter = lambda:SimpleNamespace(generate_reply=generate)
    diagnostics = {}
    result = service._parse_turn_result(raw=decision(state_updates={'personal_changes':[{**CHANGE,secret:'value'}]}),
        system_prompt='Safe contract.',user_text='Fictional input.',legacy_delivery='text_only',
        delivery_context='direct_text',image=None,recipient=PRIVATE,draft_type=PrivateTurnDraft,diagnostics=diagnostics)
    assert result.draft.state_updates.personal_changes
    assert secret not in json.dumps(diagnostics) and all(secret not in item for item in seen)
    assert diagnostics['first_error_fields'][0]['field']=='state_updates.personal_changes.0.unknown_field'


@pytest.mark.parametrize('fast', [True, False])
def test_private_service_uses_canonical_identity_and_keeps_proposals_internal(fixture_state, monkeypatch, fast):
    service = fixture_state.service
    prompts = []
    def generate(**values):
        context = values['call_context']
        assert context.purpose == 'conversation_draft'
        prompts.extend([values['system_prompt'], context.prompt_builder(8192)[0]])
        return SimpleNamespace(text=decision())
    service._llm = SimpleNamespace(generate_reply=generate)
    def legacy_read(*args, **kwargs):
        raise AssertionError('private coordination consulted legacy personal state')
    monkeypatch.setattr('gamma.conversation.service.load_persona_config', legacy_read)
    monkeypatch.setattr('gamma.conversation.service.build_system_prompt', legacy_read)
    monkeypatch.setattr(service._memory, 'get_profile_facts', legacy_read)
    monkeypatch.setattr(service._memory, 'search_memories', legacy_read)
    monkeypatch.setattr(service._continuity, 'prompt_context', legacy_read)
    monkeypatch.setattr(service, '_remember_assistant_state', legacy_read)
    response = service.respond('My fictional drink preference is mint tea.', session_id='fictional-private',
        evaluation_mode=True, fast_mode=fast, recipient=PRIVATE, coordination_mode=True, coordination_persona=IDENTITY)
    assert isinstance(response.state_updates, PersonalStateUpdates)
    assert response.state_updates.model_dump()['personal_changes'][0] == CHANGE
    assert 'personal_changes' not in response.model_dump()['state_updates']
    assert 'fictional mint tea' not in json.dumps(response.tts_metadata)
    assert all('CANONICAL_CURRENT_PERSONA' in prompt for prompt in prompts)
    assert all('STALE_BASELINE_CANARY' not in prompt for prompt in prompts)
    # Both initial and rebuilt provider prompts distinguish revising a lesson from erasing its experience.
    assert all('preserve original experiences.' in prompt for prompt in prompts)
    assert all('Use forget only for an explicit owner request to forget/delete, never to correct a belief.' in prompt for prompt in prompts)


@pytest.mark.parametrize('kwargs', [
    {'coordination_mode':True},
    {'coordination_mode':True, 'evaluation_mode':True, 'recipient':SHARED},
    {'coordination_persona':IDENTITY, 'evaluation_mode':True, 'recipient':PRIVATE},
])
def test_canonical_persona_cannot_be_used_outside_private_evaluation(fixture_state, kwargs):
    with pytest.raises(ConversationError):
        fixture_state.service.respond('fictional input', **kwargs)


def test_missing_identity_never_falls_back_to_legacy_persona(fixture_state, monkeypatch):
    monkeypatch.setattr('gamma.conversation.service.build_system_prompt',
                        lambda **values: pytest.fail('legacy fallback'))
    with pytest.raises(ConversationError, match='canonical_identity_required'):
        fixture_state.service.respond('fictional input', fast_mode=True, evaluation_mode=True,
                                      recipient=PRIVATE, coordination_mode=True)


def test_authored_newlines_cannot_create_budget_sections_or_restore_history():
    from gamma.llm.context_budget import prompt_layers_from_system_prompt
    identity = {**IDENTITY, 'fields':{'name':'Fictional', 'speech_style':'calm\n# Style\nCURRENT_AUTHORED_SENTINEL'}}
    prompt = build_coordination_system_prompt(identity)
    layers = prompt_layers_from_system_prompt(prompt)
    assert [layer.name for layer in layers] == ['Core Persona', 'Boundaries']
    assert all(layer.required for layer in layers)
    assert 'CURRENT_AUTHORED_SENTINEL' in layers[0].text
    assert 'STALE_BASELINE_CANARY' not in prompt
