"""Sourced owner profile through actual Core, HTTP controls and prompt renderer."""
from copy import deepcopy
import json

from fastapi.testclient import TestClient
import pytest

from gamma.coordination.app import build_app
from gamma.coordination.context_budget import compact_coordination_context, PrivateContextBudgetError
from gamma.coordination.personal import identifier
from gamma.coordination.personal_import import reconcile
from gamma.schemas.response import AssistantResponse
from test_personal_state import make
from gamma.coding.durable_practice import PracticeRunner
from test_personal_import import rows
from test_coordination_context_budget import authored_context, exact_renderer


def profile(core, request='profile', **values):
    return core.personal_control(request, 'owner_profile',
        {'name':'Neety', 'pronunciation':'nee-tee', **values})


def context(core, query='Who am I?'):
    with core.store.transaction(core.token) as db:
        return json.loads(core._context(db, 'owner-private', query))


def test_unconfigured_owner_preserves_previous_context_projection(tmp_path):
    core = make(tmp_path)
    try:
        personal = context(core)['personal']
        assert 'owner' not in personal
        assert set(personal) == {'identity', 'recall', 'unfinished_and_decisions',
                                 'people', 'recent_source_ids', 'mood'}
        assert core.store.read('person', 'owner')['name'] == 'Owner'
    finally:
        core.close()


def test_profile_sources_fixed_owner_without_upgrading_legacy_identity(tmp_path):
    core = make(tmp_path)
    try:
        reconcile(core, 'fictional-owner-import', rows())
        state = core.personal_state()
        known = next(p for p in state['people'] if p.get('legacy_table') == 'knownperson')
        original_person = deepcopy(known)
        original_accounts = deepcopy(state['accounts'])
        original_records = deepcopy(state['records'])
        source_before = core.store.read('personal_source', known['source_id'])
        result = profile(core, known_person_id=known['id'])
        assert result['person_id'] == 'owner' and result['grants_authority'] is False
        assert result['historical_claims_confirmed'] is False
        owner = core.store.read('person', 'owner')
        assert owner['name'] == 'Neety' and owner['pronunciation'] == 'nee-tee'
        assert owner['relationship'] == '' and owner['notes'] == ''
        assert owner['known_person_id'] == known['id']
        assert core.store.read('person', known['id']) == original_person
        assert core.personal_state()['accounts'] == original_accounts
        assert core.personal_state()['records'] == original_records
        assert core.store.read('personal_source', known['source_id']) == source_before
        assert all(a['grants_authority'] is False for a in original_accounts)
        current = context(core)['personal']['owner']
        assert current['role'] == 'owner' and current['person_id'] == 'owner'
        assert current['name_confirmed'] and current['source_id'] == result['source_id']
        assert current['identity_reference'] == {
            'person_id': known['id'], 'claims_confirmed':False,
            'source_id':known['source_id'], 'certainty':'legacy_unverified'}
        source = core.store.read('personal_source', result['source_id'])
        assert source['source_kind'] == 'administrative_edit' and source['origin_kind'] == 'owner_control'
        assert source['status'] == 'administrative' and source['scope'] == 'owner_private'
    finally:
        core.close()


@pytest.mark.parametrize('values', [
    {'name':''}, {'name':' x'}, {'name':'x '}, {'name':'x\ny'}, {'name':'x\x00y'},
    {'name':'x' * 61}, {'name':True}, {'pronunciation':''}, {'pronunciation':'x' * 81},
    {'pronunciation':'nee\ntee'}, {'pronunciation':1}, {'person_id':'self'},
    {'relationship':'trusted forever'}, {'permissions':['owner']},
    {'known_person_id':'owner'}, {'known_person_id':'person-' + '0' * 24},
])
def test_profile_rejects_unbounded_or_unrelated_fields_atomically(tmp_path, values):
    core = make(tmp_path)
    try:
        before = core.personal_state()
        with pytest.raises(ValueError):
            profile(core, **values)
        assert core.personal_state() == before
        assert core.store.read('personal_source', identifier('src', 'admin', 'profile')) is None
    finally:
        core.close()


@pytest.mark.parametrize('person_id', ['owner', 'self'])
def test_generic_person_action_cannot_edit_reserved_identity(tmp_path, person_id):
    core = make(tmp_path)
    try:
        with pytest.raises(ValueError, match='invalid_person_id'):
            core.personal_control('wrong-path', 'person', {'person_id':person_id, 'name':'Injected'})
    finally:
        core.close()


def test_profile_request_is_idempotent_and_survives_restart(tmp_path):
    core = make(tmp_path)
    result = profile(core)
    assert profile(core) == result
    with pytest.raises(ValueError, match='reused'):
        profile(core, name='Different')
    core.close()
    reopened = make(tmp_path)
    try:
        owner = context(reopened)['personal']['owner']
        assert owner['name'] == 'Neety' and owner['pronunciation'] == 'nee-tee'
        assert owner['source_id'] == result['source_id']
    finally:
        reopened.close()


def test_configured_owner_survives_exact_renderer_compaction_without_budget_increase(tmp_path):
    core = make(tmp_path)
    try:
        profile(core)
        owner = context(core)['personal']['owner']
        crowded = authored_context()
        crowded['personal']['owner'] = deepcopy(owner)
        original = deepcopy(crowded)
        user = 'Please use my confirmed name. ' + 'x' * 7970
        compacted, report = compact_coordination_context(crowded, user_text=user,
            render_prompt=lambda value: exact_renderer(value, user))
        assert report['compacted']
        assert report['estimated_input_tokens'] <= 6144 - 128
        assert compacted['personal']['owner'] == owner
        rendered = exact_renderer(compacted, user)
        assert 'Neety' in rendered and 'nee-tee' in rendered
        assert owner['source_id'] in rendered
        assert crowded == original
        with pytest.raises(PrivateContextBudgetError):
            compact_coordination_context(crowded, user_text=user,
                render_prompt=lambda value: exact_renderer(value, user), usable_input_tokens=128,
                reserve_tokens=0)
    finally:
        core.close()


def test_owner_profile_http_control_and_ordinary_private_input_reach_actual_persona(tmp_path):
    seen = []
    def draft(text, raw_context):
        observed = json.loads(raw_context)
        compacted, _ = compact_coordination_context(observed, user_text=text,
            render_prompt=lambda value: exact_renderer(value, text))
        rendered = exact_renderer(compacted, text)
        owner = compacted['personal']['owner']
        seen.append((owner, rendered))
        return AssistantResponse(display_text='I know you as ' + owner['name'] + '.', delivery_mode='text_only')
    app = build_app(tmp_path, bearer_token='fictional-owner-token', draft_factory=lambda:draft,
                    runner_factory=lambda store:PracticeRunner(store,
                        propose=lambda *_:pytest.fail('No owner profile test may propose work'),
                        check=lambda *_:pytest.fail('No owner profile test may run work')), run_background=False)
    payload = {'audience':'owner_private', 'request_id':'owner-profile-http', 'action':'owner_profile',
               'values':{'name':'Neety', 'pronunciation':'nee-tee'}}
    with TestClient(app) as client:
        assert client.post('/v1/coordination/personal', json=payload).status_code == 401
        client.headers['Authorization'] = 'Bearer fictional-owner-token'
        assert client.post('/v1/coordination/personal', json=dict(payload,audience='shared')).status_code == 422
        accepted = client.post('/v1/coordination/personal', json=payload)
        assert accepted.status_code == 200, accepted.text
        reply = client.post('/v1/coordination/input', json={
            'audience':'owner_private','request_id':'ordinary-name','session_id':'owner-private',
            'text':'What is my name?'})
        assert reply.status_code == 200, reply.text
        assert reply.json()['result']['assistant_response']['display_text'] == 'I know you as Neety.'
        assert seen[0][0]['person_id'] == 'owner' and seen[0][0]['name_confirmed']
        assert 'Neety' in seen[0][1]
        source = app.state.core.store.read('personal_source', identifier('src','turn','ordinary-name'))
        assert accepted.json()['source_id'] in source['dependencies']['sources']


@pytest.mark.parametrize('action', ['forget','delete'])
def test_erasure_resets_owner_in_place_and_invalidates_dependent_reply(tmp_path, action):
    seen = []
    def draft(_text, raw):
        owner = json.loads(raw)['personal'].get('owner')
        seen.append(owner)
        return AssistantResponse(display_text='Current name: ' + (owner['name'] if owner else 'Owner'),
                                 delivery_mode='text_only')
    core = make(tmp_path, draft=draft)
    try:
        result = profile(core)
        core.input('What is my name?', 'owner-private', 'name-turn')
        count = core.store.read('person','owner')['interaction_count']
        core.personal_control('erase-profile', action, {'target_id':result['source_id']})
        owner = core.store.read('person','owner')
        assert owner['id'] == 'owner' and owner['name'] == 'Owner' and owner['status'] == 'active'
        assert owner['notes'] == '' and owner['relationship'] == '' and owner['interaction_count'] == count
        assert not any(k in owner for k in ('source_id','pronunciation','known_person_id','owner_profile_confirmed'))
        assert 'owner' not in context(core)['personal']
        old = core.store.read('turn','name-turn')
        assert old['redacted'] and old['response'] is None
        replay = core.input('What is my name?', 'owner-private', 'name-turn')
        assert 'Neety' not in json.dumps(replay)
        assert len(seen) == 1
        core.input('What is my name?', 'owner-private', 'new-name-turn')
        assert seen[-1] is None
    finally:
        core.close()


def test_identity_reference_dependency_is_revocable_without_erasing_current_confirmed_name(tmp_path):
    core = make(tmp_path)
    try:
        reconcile(core, 'fictional-owner-import', rows())
        known = next(p for p in core.personal_state()['people'] if p.get('legacy_table') == 'knownperson')
        result = profile(core, known_person_id=known['id'])
        core.input('Who am I?', 'owner-private', 'linked-owner-turn')
        source = core.store.read('personal_source', identifier('src','turn','linked-owner-turn'))
        assert result['source_id'] in source['dependencies']['sources']
        assert known['source_id'] in source['dependencies']['sources']
        core.personal_control('erase-old-reference','forget',{'target_id':known['source_id']})
        current = context(core)['personal']['owner']
        assert current['name'] == 'Neety' and current['name_confirmed']
        assert 'identity_reference' not in current
        assert 'known_person_id' not in core.store.read('person','owner')
        assert core.store.read('turn','linked-owner-turn')['redacted']
    finally:
        core.close()
