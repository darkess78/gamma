"""P6 source leases and semantic choices exercise the actual shared owner/store."""
from copy import deepcopy
import hashlib
import json
from threading import Event, Thread

import pytest

from gamma.coding.durable_practice import PracticeRunner
from gamma.coordination.core import Core
from gamma.coordination.shared_activity import SharedActivities
from gamma.coordination.shared_activity_policy import fresh_timestamp, normalize_observation
from gamma.coordination.store import OwnershipLost, Store
from gamma.schemas.response import AssistantResponse


@pytest.fixture
def setup(tmp_path):
    now = [1000.0]
    store = Store(tmp_path)
    core = Core(store, clock=lambda: now[0], draft=lambda *_: AssistantResponse(display_text='A fitting reaction.'),
                runner=PracticeRunner(store, propose=lambda *_: {}, check=lambda *_: {}))
    service = SharedActivities(core, clock=lambda: now[0])
    try:
        yield core, service, now
    finally:
        core.close()


def select(service, request='select', **values):
    return service.control(request, 'select', {'source_id': 'runelite', 'label': 'RuneLite window',
                           'kind': 'osrs_screen', **values})


def frame(service, now, **values):
    return {'observation_id': 'frame_1', 'source_id': 'runelite',
            'source_epoch': service.status()['state']['source_epoch'], 'sequence': 0,
            'captured_at': now[0], 'kind': 'osrs_screen', 'summary': 'A bank window appears open.',
            'confidence': .6, 'content_sha256': 'a' * 64, **values}


def test_selected_source_and_report_are_distinct_from_understanding(setup):
    core, service, now = setup
    assert service.status()['state']['enabled'] is False
    with pytest.raises(ValueError, match='disabled'):
        service.observe(frame(service, now))
    select(service)
    observation = frame(service, now)
    assert service.observe(observation)['evidence_kind'] == 'owner_report'
    context = service.status()['context']
    assert context['observation'] == observation and context['evidence_kind'] == 'owner_report'
    source = core.store.read('personal_source', context['source_ids'][0])
    assert source['source_kind'] == 'owner_reported_observation' and source['promoted'] is False
    assert core.store.read('turn', 'frame_1') is None


def test_selection_and_observation_ids_do_not_replenish_or_replay(setup):
    _, service, now = setup
    first = select(service, seconds=30, maximum_reviews=1)
    now[0] += 10
    assert select(service, seconds=30, maximum_reviews=1) == first
    assert service.status()['state']['expires_at'] == 1030
    observation = frame(service, now)
    assert service.observe(observation)['duplicate'] is False
    assert service.observe(observation)['duplicate'] is True
    with pytest.raises(ValueError, match='reused'):
        service.observe({**observation, 'summary': 'Different content.'})
    with pytest.raises(ValueError, match='sequence'):
        service.observe({**observation, 'observation_id': 'frame_2'})
    now[0] = 1030
    assert service.status()['state']['enabled'] is False


@pytest.mark.parametrize('field,value', [('source_id', 'another'), ('source_epoch', 0),
                                       ('captured_at', 987), ('kind', 'minecraft_state')])
def test_cross_source_and_stale_observations_fail_closed(setup, field, value):
    _, service, now = setup
    select(service)
    with pytest.raises(ValueError):
        service.observe(frame(service, now, **{field: value}))
    assert service.status()['context']['observation'] is None


@pytest.mark.parametrize('value', [True, '1000', float('nan'), float('inf'), -1, 1010])
def test_capture_time_is_not_coerced(value):
    with pytest.raises(ValueError):
        fresh_timestamp(value, now=1000)


def test_goal_is_authored_and_removed_sources_leave_context(setup):
    core, service, now = setup
    service.control('goal', 'goal', {'text': 'Reach 70 fishing together.'})
    context = service.status()['context']
    assert context['shared_goal'] == 'Reach 70 fishing together.'
    source = core.store.read('personal_source', context['source_ids'][0])
    assert source['source_kind'] == 'administrative_edit'
    select(service)
    service.observe(frame(service, now))
    source_id = service.status()['context']['source_ids'][-1]
    core.personal_control('forget_frame', 'forget', {'target_id': source_id})
    assert service.status()['context']['observation'] is None
    core.personal_control('forget_goal', 'forget', {'target_id': context['source_ids'][0]})
    assert service.status()['context']['shared_goal'] == ''


def test_sleep_ends_capture_and_wake_does_not_reenable(setup):
    core, service, now = setup
    select(service)
    row = frame(service, now)
    core.control('sleep', 'sleep')
    assert service.status()['state']['reason'] == 'sleep_stopped_capture'
    core.control('wake', 'wake')
    assert not service.status()['state']['enabled']
    with pytest.raises(ValueError, match='disabled'):
        service.observe(row)


def test_restart_keeps_goal_but_revokes_capture(setup):
    core, service, _ = setup
    select(service)
    epoch = service.status()['state']['source_epoch']
    service.control('goal', 'goal', {'text': 'Practice this route.'})
    replacement = SharedActivities(core)
    state = replacement.status()
    assert state['state']['source_epoch'] == epoch + 1
    assert state['state']['enabled'] is False
    assert state['context']['shared_goal'] == 'Practice this route.'


def test_real_core_channel_hook_preserves_identity_and_no_fictitious_owner_turn(setup):
    core, service, now = setup
    contexts = []
    core.draft = lambda text, context: (contexts.append(json.loads(context)) or
                                      AssistantResponse(display_text='That is a lot of fish.'))
    select(service, maximum_reviews=1)
    service.observe(frame(service, now))
    identity = core.personal_state()['identity_id']
    result = service.review('reaction')
    assert result['status'] == 'generated' and result['text'] == 'That is a lot of fish.'
    assert result['delivery_status'] == 'not_delivered'
    assert service.review('reaction') == result
    assert service.status()['state']['remaining_reviews'] == 0
    assert core.personal_state()['identity_id'] == identity
    assert core.store.read('turn', 'reaction') is None
    assert contexts and contexts[0]['channel_event']['shared_activity']['observation'] is not None


@pytest.mark.parametrize('change', ['stop', 'new_source', 'new_frame', 'sleep', 'focus', 'forget'])
def test_pending_reaction_is_fenced_by_real_changes(setup, change):
    core, service, now = setup
    select(service)
    service.observe(frame(service, now))
    entered, release = Event(), Event()
    def draft(*_):
        entered.set()
        assert release.wait(5)
        return AssistantResponse(display_text='This late text must be withheld.')
    core.draft = draft
    results = []
    thread = Thread(target=lambda: results.append(service.review('pending')))
    thread.start()
    try:
        assert entered.wait(5)
        if change == 'stop':
            service.control('stop', 'stop', {})
        elif change == 'new_source':
            select(service, 'replacement', source_id='other')
        elif change == 'new_frame':
            service.observe(frame(service, now, observation_id='frame_2', sequence=1))
        elif change in {'sleep', 'focus'}:
            core.control(change, change if change == 'sleep' else 'focus', **({'focus': 'focused'} if change == 'focus' else {}))
        else:
            source = service.status()['context']['source_ids'][-1]
            core.personal_control('forget', 'forget', {'target_id': source})
        release.set()
        thread.join(5)
        assert not thread.is_alive()
        assert results[0]['status'] in {'cancelled', 'deferred'} and not results[0].get('text')
    finally:
        release.set()
        thread.join(5)


def test_removed_reaction_source_cannot_be_replayed_by_duplicate_request(setup):
    core, service, now = setup
    select(service)
    service.observe(frame(service, now))
    result = service.review('reaction')
    core.personal_control('forget', 'forget', {'target_id': result['personal_source_id']})
    duplicate = service.review('reaction')
    assert duplicate['status'] == 'cancelled' and not duplicate.get('text')
    assert core.store.read('shared_observation', 'frame_1')['observation'] is None
    assert core.store.read('shared_reaction', 'reaction')['text'] == ''


def test_image_analyzer_uses_real_bytes_and_does_not_store_raw_image(setup):
    core, service, now = setup
    select(service)
    calls = []
    service.analyze_image = lambda *args: (calls.append(args) or {'summary': 'Unknown view.', 'confidence': .1})
    raw = b'\x89PNG\r\n\x1a\n' + b'controlled image fixture'
    metadata = {key: value for key, value in frame(service, now).items()
                if key not in {'summary', 'confidence', 'content_sha256'}}
    result = service.observe_image(metadata, raw, 'image/png')
    assert result['evidence_kind'] == 'vision_interpretation'
    assert calls == [(raw, 'image/png', 'osrs_screen')]
    assert service.status()['context']['observation']['content_sha256'] == hashlib.sha256(raw).hexdigest()
    assert service.observe_image(metadata, raw, 'image/png')['duplicate'] is True and len(calls) == 1
    assert not list(core.store.root.glob('*.png'))


def test_source_change_during_image_analysis_discards_result(setup):
    _, service, now = setup
    select(service)
    def analyze(*_):
        service.control('stop', 'stop', {})
        return {'summary': 'Must not be accepted.', 'confidence': .8}
    service.analyze_image = analyze
    raw = b'\x89PNG\r\n\x1a\nfixture'
    metadata = {key: value for key, value in frame(service, now).items()
                if key not in {'summary', 'confidence', 'content_sha256'}}
    with pytest.raises(ValueError, match='disabled'):
        service.observe_image(metadata, raw, 'image/png')
    assert service.status()['context']['observation'] is None


def test_missing_analyzer_is_honest_and_does_not_create_observation(setup):
    _, service, now = setup
    select(service)
    metadata = {key: value for key, value in frame(service, now).items()
                if key not in {'summary', 'confidence', 'content_sha256'}}
    assert service.observe_image(metadata, b'\x89PNG\r\n\x1a\nfixture', 'image/png')['status'] == 'unavailable'
    assert service.status()['context']['observation'] is None


def test_observation_policy_rejects_unknown_keys_and_bad_types(setup):
    _, service, now = setup
    select(service)
    row = frame(service, now)
    for key, value in [('summary', '\x00hidden'), ('confidence', True), ('sequence', True),
                       ('source_epoch', 2.0), ('content_sha256', 'x' * 64), ('extra', 'execute')]:
        with pytest.raises(ValueError):
            service.observe({**row, key: value})
    assert service.status()['context']['observation'] is None


def test_control_capacity_retains_fingerprints_and_stop_still_works(setup):
    core, service, _ = setup
    first = select(service)
    with core.store.transaction(core.token) as db:
        for index in range(1023):
            core.store.put(db, 'shared_command', 'retained_' + str(index),
                           {'id': 'retained_' + str(index), 'fingerprint': 'old', 'result': first})
    with pytest.raises(ValueError, match='capacity'):
        service.control('new_goal', 'goal', {'text': 'Must not discard old hashes.'})
    assert select(service) == first
    assert service.control('full_stop', 'stop', {})['enabled'] is False
    assert core.store.read('shared_command', 'retained_0')['fingerprint'] == 'old'


def test_stop_invalidates_a_pending_select_even_while_disabled(setup):
    _, service, _ = setup
    previous_epoch = service.status()['state']['source_epoch']
    assert not service.status()['state']['enabled']
    service.control('privacy_withdrawn', 'stop', {})
    with pytest.raises(ValueError, match='epoch_changed'):
        select(service, 'delayed_select', expected_epoch=previous_epoch)
    current = service.status()['state']
    assert not current['enabled'] and current['source_epoch'] == previous_epoch + 1
    assert select(service, 'current_select', expected_epoch=current['source_epoch'])['enabled']


@pytest.mark.parametrize('expected', [True, -1, 1.0, '1'])
def test_select_expected_epoch_is_strict(setup, expected):
    _, service, _ = setup
    with pytest.raises(ValueError, match='epoch_changed'):
        select(service, expected_epoch=expected)
