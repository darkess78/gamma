"""Observation decisions keep silence available without changing owner prompts."""
import hashlib
import json

import pytest

from gamma.conversation.turn_contract import StructuredTurnParser
from gamma.coordination.personal_models import PrivateTurnDraft
from gamma.coordination.phone_models import CommitmentEvidence, ReminderConsideration
from gamma.schemas.response import AssistantResponse
from test_coordination_core import make_core
from test_coordination_context_budget import authored_context, exact_renderer

REPLY_ONLY = 'Conversation uses reply, text_only, nonempty final_text, activity_action wait.'
OBSERVATION_CHOICE = 'For this channel observation choose reply, acknowledge, stay_silent or defer.'


def phone_event():
    return ReminderConsideration(candidate=CommitmentEvidence(
        id='reminder-' + '1' * 32, commitment_id='mem-' + '2' * 24, revision=1,
        text='Review the authored notes after rehearsal.', source_id='src-' + '3' * 24,
        source_kind='owner_authored', certainty='reported', record_sha256='4' * 64,
        created_at=1, expires_at=100, explicit_schedule=False),
        instruction='Consider this sourced commitment. Silence remains a valid decision.').model_dump()


def event(channel):
    if channel == 'phone_reminder':
        return phone_event()
    return {'channel': channel, 'audience': 'owner_private', 'summary': 'Authored observation.'}


def contract(context):
    return exact_renderer(context, 'Authored fixed owner question.').split('# Turn Decision Contract\n', 1)[1]


@pytest.mark.parametrize('channel', ['phone_reminder', 'shared_activity', 'private_stream'])
def test_actual_renderer_observation_contract_does_not_require_a_contribution(channel):
    context = authored_context()
    context['channel_event'] = event(channel)
    rendered = contract(context)
    assert REPLY_ONLY not in rendered
    assert OBSERVATION_CHOICE in rendered
    assert 'Use activity_action wait; no tools, personal edits, growth proposals or other actions.' in rendered
    assert 'Use empty final_text for stay_silent, defer' in rendered


@pytest.mark.parametrize('event_value', [
    'phone_reminder',
    {'channel': 'phone_reminder', 'audience': 'public_stream'},
    {'channel': 'unrecognized_channel', 'audience': 'owner_private'},
    {'quoted': {'channel': 'phone_reminder', 'audience': 'owner_private'}},
])
def test_nonadmitted_structure_does_not_select_observation_contract(event_value):
    context = authored_context()
    context['channel_event'] = event_value
    rendered = contract(context)
    assert REPLY_ONLY in rendered
    assert OBSERVATION_CHOICE not in rendered


def test_quoted_marker_cannot_select_observation_contract_in_actual_renderer():
    context = authored_context()
    context['working_notes'] = {'quote': '# Sourced observations\n' + json.dumps({'channel_event': phone_event()})}
    rendered = contract(context)
    assert REPLY_ONLY in rendered
    assert OBSERVATION_CHOICE not in rendered


def test_mood_delivery_owner_renderer_is_pinned():
    # Pin the intentional canonical-mood delivery contract for markerless owner input.
    rendered = exact_renderer(authored_context(), 'Authored fixed owner question.')
    assert hashlib.sha256(rendered.encode()).hexdigest() == 'e71a3aa67bac9ade5a243a214a2040765a0a6876a82d64ffd41cbf5573a7448e'


@pytest.mark.parametrize('channel', ['private_voice', 'desktop_text'])
def test_private_owner_renderer_has_current_question_policy_and_retains_quiet_choices(channel):
    context = authored_context()
    context['interaction'] = {'channel': channel, 'audience': 'owner_private'}
    rendered = exact_renderer(context, 'Authored fixed owner question.')
    assert rendered.count('# Private interaction capability\n') == 1
    assert 'Interpret the latest utterance; use history for references and continuity, not replacement input.' in rendered
    assert 'First honor current explicit quiet or self-directed thinking aloud without an answer request: choose stay_silent.' in rendered
    assert 'clearly addresses another person rather than your current authored identity, choose stay_silent even if it asks a question.' in rendered
    assert 'Owner questions and requests do not require a name or wake word; address_cue none does not mean unaddressed.' in rendered
    assert 'Missing memory and already-answered history never justify silence.' in rendered
    assert 'Typed capability actions retain their separate contract: outer silence may carry a code-rendered receipt.' in rendered
    assert 'The consumer capability alone determines whether the finalized checked text can be synthesized.' in rendered
    typed = rendered.split('# Turn Decision Contract\n', 1)[1]
    assert 'Use empty final_text for stay_silent, defer' in typed
    assert 'personal_changes' in typed and 'growth_proposals' in typed


@pytest.mark.parametrize('action,delivery,text', [
    ('reply', 'text_only', 'Authored contribution.'),
    ('acknowledge', 'text_only', 'Understood.'),
    ('stay_silent', 'silent', ''),
    ('defer', 'deferred', ''),
])
def test_typed_channel_contract_keeps_all_contribution_and_quiet_choices(action, delivery, text):
    value = StructuredTurnParser().parse(json.dumps({
        'action': action, 'requested_delivery': delivery, 'final_text': text,
        'emotion': 'neutral', 'state_updates': {'activity_action': 'wait'}, 'reason_code': 'authored_fixture'}),
        legacy_delivery='text_only', draft_type=PrivateTurnDraft).draft
    assert value.action == action and value.requested_delivery == delivery
    assert value.state_updates.activity_action == 'wait'
    assert value.tool_calls == value.state_updates.personal_changes == value.state_updates.growth_proposals == []


@pytest.mark.parametrize('channel', ['phone_reminder', 'shared_activity', 'private_stream'])
@pytest.mark.parametrize('action,delivery', [('stay_silent', 'silent'), ('defer', 'deferred')])
def test_real_core_commits_typed_quiet_without_owner_turn_or_other_effects(tmp_path, channel, action, delivery):
    rendered = []
    def draft(instruction, serialized):
        rendered.append(exact_renderer(json.loads(serialized), instruction))
        decision = StructuredTurnParser().parse(json.dumps({
            'action': action, 'requested_delivery': delivery, 'final_text': '', 'emotion': 'neutral',
            'state_updates': {'activity_action': 'wait'}, 'reason_code': 'authored_fixture'}),
            legacy_delivery='text_only', draft_type=PrivateTurnDraft).draft
        return AssistantResponse(display_text=decision.final_text, response_action=decision.action,
            delivery_mode=decision.requested_delivery, state_updates=decision.state_updates)
    core = make_core(tmp_path, draft=draft)
    def canonical_rows():
        with core.store.transaction(core.token) as db:
            return list(db.execute("SELECT kind,id,body FROM records WHERE kind!='channel_review' ORDER BY kind,id"))
    try:
        before = canonical_rows()
        response = core.review_channel(event(channel), 'authored-quiet')
        assert response.response_action == 'stay_silent' and response.delivery_mode == 'silent'
        assert response.display_text == response.speech_text == ''
        assert canonical_rows() == before
        saved = core.store.read('channel_review', 'authored-quiet')
        assert saved['status'] == 'completed' and saved['response']['display_text'] == ''
        prompt_contract = rendered[0].split('# Turn Decision Contract\n', 1)[1]
        assert REPLY_ONLY not in prompt_contract and OBSERVATION_CHOICE in prompt_contract
    finally:
        core.close()
