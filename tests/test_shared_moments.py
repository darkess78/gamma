"""Historical moments retain real capture provenance while pixels can change."""
from copy import deepcopy
import hashlib
import json
import sqlite3
from threading import Event, Thread

import pytest

from gamma.coordination.shared_moments import SharedMoments, invalidate_snapshot, redact_removed_sources
from gamma.schemas.response import AssistantResponse
from test_shared_activity import setup, select


IMAGE = b'\x89PNG\r\n\x1a\ncontrolled-a'
CHANGED = b'\x89PNG\r\n\x1a\ncontrolled-b'


@pytest.fixture
def moments(setup):
    core, activity, now = setup
    mono = [0.]
    service = SharedMoments(activity, monotonic=lambda: mono[0])
    select(activity, seconds=120, maximum_reviews=8, review_interval_seconds=10)
    return core, activity, service, now, mono


def metadata(activity, now, index=0):
    return {'observation_id': 'moment_frame_' + str(index), 'source_id': 'runelite',
            'source_epoch': activity.status()['state']['source_epoch'], 'sequence': index,
            'captured_at': now[0], 'kind': 'osrs_screen'}


def analyzer(activity, calls=None):
    def image(raw, *args):
        if calls is not None: calls.append(raw)
        return {'summary': 'A controlled view with an unknown detail.', 'confidence': .7}
    activity.analyze_image = image


def test_historical_moment_finishes_without_fake_renewals_or_current_authority(moments):
    core, activity, service, now, mono = moments
    inputs, contexts = [], []
    original = metadata(activity, now)
    def image(raw, *args):
        inputs.append(raw); now[0] += 6; mono[0] += 6
        return {'summary': 'A bank interface appears open in this image.', 'confidence': .6}
    def draft(text, context):
        contexts.append(json.loads(context)); now[0] += 14.6; mono[0] += 14.6
        return AssistantResponse(display_text='That was the bank view in the moment you shared.')
    activity.analyze_image, core.draft = image, draft
    result = service.share('one_moment', original, IMAGE, 'image/png')
    assert result['status'] == 'generated' and inputs == [IMAGE]
    assert result['captured_at'] == 1000 and result['analysis_started_at'] == 1000
    assert result['analysis_completed_at'] == result['review_started_at'] == 1006
    assert result['generated_at'] == 1020.6 and result['age_seconds'] == pytest.approx(20.6)
    assert result['current_state'] == 'unknown' and result['action_authority'] is False
    assert activity.status()['context']['observation'] is None
    source = core.store.read('personal_source', result['personal_source_id'])
    assert source['occurred_at'] == source['ingested_at'] == 1000
    assert source['interpreted_at'] == 1006 and source['image_sha256'] == hashlib.sha256(IMAGE).hexdigest()
    assert source['temporal_scope'] == 'historical_capture' and source['text'].startswith('In the captured moment at 1000')
    event = contexts[0]['channel_event']
    assert event['channel'] == 'shared_activity' and event['shared_moment']['source_ids']
    assert event['shared_moment']['current_state'] == 'unknown'
    assert core.store.read('turn', 'one_moment') is None
    with core.store.transaction(core.token) as db:
        assert core.store.list(db, 'run', None) == []


def test_changed_frames_during_interpretation_and_core_do_not_rewrite_the_moment(moments):
    core, activity, service, now, mono = moments
    image_entered, image_release, core_entered, core_release = Event(), Event(), Event(), Event()
    captures, replies = [], []
    original = metadata(activity, now)
    def image(raw, *_):
        captures.append(raw); image_entered.set(); assert image_release.wait(5)
        return {'summary': 'The original selected moment.', 'confidence': .6}
    def draft(*_):
        core_entered.set(); assert core_release.wait(5)
        return AssistantResponse(display_text='Reflecting on the original moment only.')
    activity.analyze_image, core.draft = image, draft
    thread = Thread(target=lambda: replies.append(service.share('changing', original, IMAGE, 'image/png')))
    thread.start()
    try:
        assert image_entered.wait(5)
        now[0] += 2; mono[0] += 2
        assert activity.observe_image(metadata(activity, now, 1), CHANGED, 'image/png')['status'] == 'busy'
        now[0] += 4; mono[0] += 4; image_release.set(); assert core_entered.wait(5)
        analyzer(activity)
        now[0] += 2; mono[0] += 2
        assert activity.observe_image(metadata(activity, now, 2), CHANGED, 'image/png')['status'] == 'interpreted'
        now[0] += 12.6; mono[0] += 12.6; core_release.set(); thread.join(5)
        assert not thread.is_alive() and replies[0]['status'] == 'generated'
        assert captures == [IMAGE] and replies[0]['captured_at'] == 1000
        assert replies[0]['image_sha256'] == hashlib.sha256(IMAGE).hexdigest()
    finally:
        image_release.set(); core_release.set(); thread.join(5)


@pytest.mark.parametrize('change', ['stop', 'source', 'expiry', 'sleep', 'goal', 'mode', 'sequence', 'forget', 'forget_goal'])
def test_authority_changes_still_cancel_pending_historical_choice(moments, change):
    core, activity, service, now, mono = moments
    analyzer(activity)
    if change == 'forget_goal': activity.control('goal_before', 'goal', {'text': 'Keep this goal private.'})
    entered, release = Event(), Event()
    outputs = []
    def draft(*_):
        entered.set(); assert release.wait(5)
        return AssistantResponse(display_text='This must not escape the fence.')
    core.draft = draft
    thread = Thread(target=lambda: outputs.append(service.share('fenced', metadata(activity, now), IMAGE, 'image/png')))
    thread.start()
    try:
        assert entered.wait(5)
        if change == 'stop': activity.control('stop', 'stop', {})
        elif change == 'source': select(activity, 'other_source')
        elif change == 'sleep': core.control('sleep', 'sleep')
        elif change == 'goal': activity.control('goal', 'goal', {'text': 'A new private goal.'})
        elif change == 'mode': activity.control('mode', 'mode', {'mode': 'quiet'})
        elif change == 'sequence': core.control('focus', 'focus', focus='focused')
        elif change == 'expiry': now[0] = activity.status()['state']['expires_at']
        elif change == 'forget_goal':
            core.personal_control('forget_goal', 'forget', {'target_id': activity.status()['state']['goal_source_id']})
        else:
            source_id = core.store.read('shared_moment', 'fenced')['personal_source_id']
            core.personal_control('forget', 'forget', {'target_id': source_id})
        now[0] += 20; mono[0] += 20; release.set(); thread.join(5)
        assert outputs[0]['status'] == 'cancelled' and not outputs[0].get('text')
    finally:
        release.set(); thread.join(5)


@pytest.mark.parametrize('clock_kind', ['wall', 'monotonic'])
def test_processing_deadline_does_not_become_world_freshness(moments, clock_kind):
    core, activity, service, now, mono = moments
    drafts = []
    core.draft = lambda *_: drafts.append(1) or AssistantResponse(display_text='Not allowed.')
    def image(*_):
        if clock_kind == 'wall': now[0] += 45
        else: mono[0] += 45
        return {'summary': 'A valid old capture.', 'confidence': .6}
    activity.analyze_image = image
    result = service.share('deadline', metadata(activity, now), IMAGE, 'image/png')
    assert result['status'] == 'cancelled' and drafts == []


def test_stale_input_is_rejected_before_any_interpretation(moments):
    _, activity, service, now, _ = moments
    calls = []; analyzer(activity, calls)
    value = metadata(activity, now); value['captured_at'] -= 13
    with pytest.raises(ValueError, match='stale'):
        service.share('stale', value, IMAGE, 'image/png')
    assert calls == []


def test_duplicate_request_keeps_original_times_and_never_reinterprets(moments):
    _, activity, service, now, mono = moments
    calls = []; analyzer(activity, calls)
    original = metadata(activity, now)
    first = service.share('once', original, IMAGE, 'image/png')
    now[0] += 20; mono[0] += 20
    again = service.share('once', original, IMAGE, 'image/png')
    assert again['status'] == 'generated' and calls == [IMAGE]
    assert again['captured_at'] == first['captured_at'] and again['generated_at'] == first['generated_at']
    assert again['age_seconds'] == 20
    with pytest.raises(ValueError, match='reused'):
        service.share('once', {**original, 'captured_at': now[0]}, IMAGE, 'image/png')
    now[0] += 25; mono[0] += 25
    assert service.share('once', original, IMAGE, 'image/png')['status'] == 'cancelled'
    assert calls == [IMAGE]


def test_unknown_failure_cannot_replay_a_model_call(moments):
    _, activity, service, now, _ = moments
    calls = []
    def unknown(*_): calls.append(1); raise RuntimeError('controlled unknown provider outcome')
    activity.analyze_image = unknown
    original = metadata(activity, now)
    with pytest.raises(RuntimeError): service.share('unknown', original, IMAGE, 'image/png')
    assert service.share('unknown', original, IMAGE, 'image/png')['status'] == 'failed'
    assert calls == [1]


def test_redactor_clears_moment_and_core_cache_and_retains_only_no_replay_identity(moments):
    core, activity, service, now, _ = moments
    analyzer(activity)
    result = service.share('private', metadata(activity, now), IMAGE, 'image/png')
    original = core.store.read('shared_moment', 'private')
    core.personal_control('forget', 'forget', {'target_id': result['personal_source_id']})
    row = core.store.read('shared_moment', 'private')
    assert row['redacted'] and row['capture'] is None and row['snapshot'] is None
    assert row['summary'] is None and not row['result']['text'] and row['fingerprint'] == original['fingerprint']
    assert core.store.read('channel_review', original['review_id'])['response'] is None
    assert not activity.status()['state']['enabled']
    assert core.store.read('shared_frame_receipt', 'moment_frame_0')['frame'] is None


@pytest.mark.parametrize('withdrawal', ['stop', 'reselect', 'expiry'])
def test_opaque_selected_handle_cannot_replay_cached_text_after_authority_loss(moments, withdrawal):
    core, activity, service, now, _ = moments
    analyzer(activity)
    original = metadata(activity, now)
    result = service.share('cached', original, IMAGE, 'image/png')
    assert result['status'] == 'generated'
    assert core.store.read('personal_source', 'runelite') is None
    if withdrawal == 'stop': activity.control('stop', 'stop', {})
    elif withdrawal == 'reselect': select(activity, 'replacement', source_id='another_handle')
    else: now[0] = activity.status()['state']['expires_at']
    # The old handle may remain in meta, but enabled/epoch/expiry are authority.
    cached = service.share('cached', original, IMAGE, 'image/png')
    assert cached['status'] == 'cancelled' and not cached['text']


def test_copied_snapshot_invalidates_moment_without_touching_original(moments, tmp_path):
    core, activity, service, now, _ = moments
    analyzer(activity); service.share('copy', metadata(activity, now), IMAGE, 'image/png')
    original = core.store.read('shared_moment', 'copy')
    source = sqlite3.connect('file:' + str(core.store.path) + '?mode=ro', uri=True)
    copied = sqlite3.connect(tmp_path / 'copied.sqlite3')
    try:
        source.backup(copied)
        with copied: invalidate_snapshot(copied)
        body = json.loads(copied.execute("SELECT body FROM records WHERE kind='shared_moment' AND id='copy'").fetchone()[0])
        assert body['status'] == 'cancelled' and body['capture'] is None
        assert body['fingerprint'] == original['fingerprint']
        assert core.store.read('shared_moment', 'copy')['status'] == 'generated'
    finally:
        copied.close(); source.close()
