"""Real full-byte renewals preserve freshness without changing immutable perception history."""
from threading import Event, Thread

import pytest

from gamma.schemas.response import AssistantResponse
from test_shared_activity import setup, select, frame


IMAGE = b'\x89PNG\r\n\x1a\ncontrolled-image-a'
CHANGED = b'\x89PNG\r\n\x1a\ncontrolled-image-b'


def metadata(service, now, index=0):
    value = frame(service, now, observation_id='image_' + str(index), sequence=index)
    return {key: value[key] for key in ('observation_id', 'source_id', 'source_epoch', 'sequence', 'captured_at', 'kind')}


def analyzed(service):
    calls = []
    service.analyze_image = lambda raw, *args: (calls.append(raw) or {'summary': 'A panel is open; detail is unknown.', 'confidence': .7})
    return calls


def test_first_perception_finishes_against_genuine_fresh_same_hash_frames(setup):
    core, service, now = setup
    select(service)
    entered, release = Event(), Event()
    calls, results = [], []
    def analyze(raw, *_):
        calls.append(raw); entered.set(); assert release.wait(5)
        return {'summary': 'A panel with an unknown detail.', 'confidence': .7}
    service.analyze_image = analyze
    original = metadata(service, now)
    thread = Thread(target=lambda: results.append(service.observe_image(original, IMAGE, 'image/png')))
    thread.start()
    try:
        assert entered.wait(5)
        now[0] += 10
        assert service.observe_image(metadata(service, now, 1), IMAGE, 'image/png')['status'] == 'refresh_received'
        now[0] += 10
        assert service.observe_image(metadata(service, now, 2), IMAGE, 'image/png')['status'] == 'refresh_received'
        now[0] += 1
        release.set(); thread.join(5)
        assert results[0]['status'] == 'interpreted' and calls == [IMAGE]
        context = service.status()['context']
        assert context['analyzed_observation']['captured_at'] == 1000
        assert context['observation']['captured_at'] == context['freshness_frame']['captured_at'] == 1020
        assert context['observation']['observation_id'] == 'image_2'
        source = core.store.read('personal_source', context['source_ids'][-1])
        assert source['occurred_at'] == 1000 and source['ingested_at'] == 1021
        assert core.store.read('turn', 'image_2') is None
    finally:
        release.set(); thread.join(5)


def test_pending_core_review_can_finish_after_actual_unchanged_full_frames(setup):
    core, service, now = setup
    select(service)
    calls = analyzed(service)
    service.observe_image(metadata(service, now), IMAGE, 'image/png')
    source = service.status()['context']['source_ids'][-1]
    entered, release = Event(), Event()
    results = []
    def draft(*_):
        entered.set(); assert release.wait(5)
        return AssistantResponse(display_text='That detail is still unknown.')
    core.draft = draft
    thread = Thread(target=lambda: results.append(service.review('reaction')))
    thread.start()
    try:
        assert entered.wait(5)
        now[0] += 10
        assert service.observe_image(metadata(service, now, 1), IMAGE, 'image/png')['status'] == 'refreshed'
        now[0] += 10
        assert service.observe_image(metadata(service, now, 2), IMAGE, 'image/png')['status'] == 'refreshed'
        release.set(); thread.join(5)
        assert results[0]['status'] == 'generated' and results[0]['personal_source_id'] == source
        assert calls == [IMAGE] and service.status()['context']['analyzed_observation']['captured_at'] == 1000
    finally:
        release.set(); thread.join(5)


def test_changed_pixels_and_return_to_old_pixels_invalidate_pending_perception(setup):
    _, service, now = setup
    select(service)
    entered, release = Event(), Event()
    service.analyze_image = lambda *_: (entered.set(), release.wait(5), {'summary': 'Old scene.', 'confidence': .7})[-1]
    results = []
    thread = Thread(target=lambda: results.append(service.observe_image(metadata(service, now), IMAGE, 'image/png')))
    thread.start()
    try:
        assert entered.wait(5)
        now[0] += 1
        assert service.observe_image(metadata(service, now, 1), CHANGED, 'image/png')['status'] == 'busy'
        now[0] += 1
        assert service.observe_image(metadata(service, now, 2), IMAGE, 'image/png')['status'] == 'busy'
        release.set(); thread.join(5)
        assert results[0]['status'] == 'cancelled'
        assert service.status()['context']['observation'] is None
    finally:
        release.set(); thread.join(5)


def test_uninterpreted_changed_frame_cancels_pending_core_output(setup):
    core, service, now = setup
    select(service); analyzed(service)
    service.observe_image(metadata(service, now), IMAGE, 'image/png')
    entered, release, new_entered, new_release = Event(), Event(), Event(), Event()
    core.draft = lambda *_: (entered.set(), release.wait(5), AssistantResponse(display_text='Old scene reply.'))[-1]
    service.analyze_image = lambda *_: (new_entered.set(), new_release.wait(5), {'summary': 'Changed scene.', 'confidence': .6})[-1]
    replies, images = [], []
    thread = Thread(target=lambda: replies.append(service.review('old_reply')))
    thread.start()
    image_thread = None
    try:
        assert entered.wait(5)
        now[0] += 1
        image_thread = Thread(target=lambda: images.append(service.observe_image(metadata(service, now, 1), CHANGED, 'image/png')))
        image_thread.start(); assert new_entered.wait(5)
        assert service.status()['context']['observation'] is None
        release.set(); thread.join(5)
        assert replies[0]['status'] == 'cancelled' and not replies[0].get('text')
    finally:
        release.set(); new_release.set(); thread.join(5)
        if image_thread: image_thread.join(5)


def test_erasing_original_interpretation_invalidates_all_renewals_and_caches(setup):
    core, service, now = setup
    select(service); analyzed(service)
    first = metadata(service, now)
    service.observe_image(first, IMAGE, 'image/png')
    now[0] += 1
    service.observe_image(metadata(service, now, 1), IMAGE, 'image/png')
    result = service.review('reaction')
    core.personal_control('erase_original', 'forget', {'target_id': result['personal_source_id']})
    assert not service.status()['state']['enabled']
    assert service.status()['context']['observation'] is None
    assert core.store.read('shared_reaction', 'reaction')['text'] == ''
    for key in ('image_0', 'image_1'):
        receipt = core.store.read('shared_frame_receipt', key)
        assert receipt['redacted'] and receipt['frame'] is None and receipt['content_sha256'] is None
        assert receipt['fingerprint']
    with pytest.raises(ValueError, match='disabled'):
        service.observe_image(first, IMAGE, 'image/png')


def test_duplicate_or_stale_packets_do_not_pretend_to_be_new_captures(setup):
    _, service, now = setup
    select(service); calls = analyzed(service)
    original = metadata(service, now)
    service.observe_image(original, IMAGE, 'image/png')
    now[0] += 11
    assert service.observe_image(original, IMAGE, 'image/png')['duplicate']
    with pytest.raises(ValueError, match='reused'):
        service.observe_image({**original, 'captured_at': now[0]}, IMAGE, 'image/png')
    with pytest.raises(ValueError, match='sequence'):
        service.observe_image({**metadata(service, now, 1), 'sequence': 0}, IMAGE, 'image/png')
    with pytest.raises(ValueError):
        service.observe_image({**metadata(service, now, 1), 'captured_at': now[0] - 13}, IMAGE, 'image/png')
    now[0] += 2
    assert service.status()['context']['observation'] is None and calls == [IMAGE]


def test_frame_budget_and_receipt_capacity_preserve_hashes_and_allow_stop(setup, monkeypatch):
    import gamma.coordination.shared_activity_frames as frames
    _, service, now = setup
    select(service, maximum_frames=2); analyzed(service)
    service.observe_image(metadata(service, now), IMAGE, 'image/png')
    now[0] += 1
    assert service.observe_image(metadata(service, now, 1), IMAGE, 'image/png')['status'] == 'refreshed'
    now[0] += 1
    with pytest.raises(ValueError, match='budget'):
        service.observe_image(metadata(service, now, 2), IMAGE, 'image/png')
    select(service, 'second_lease')
    monkeypatch.setattr(frames, 'FRAME_LIMIT', 2)
    with pytest.raises(ValueError, match='capacity'):
        service.observe_image(metadata(service, now, 2), IMAGE, 'image/png')
    service.control('stop', 'stop', {})
    assert not service.status()['state']['enabled']


def test_fast_changed_pixels_still_invalidate_old_meaning(setup):
    _, service, now = setup
    select(service); calls = analyzed(service)
    service.observe_image(metadata(service, now), IMAGE, 'image/png')
    now[0] += .1
    result = service.observe_image(metadata(service, now, 1), CHANGED, 'image/png')
    assert result['status'] == 'rate_limited'
    assert service.status()['context']['observation'] is None and calls == [IMAGE]


def test_unknown_perception_failure_stops_feed_instead_of_automatic_retry(setup):
    _, service, now = setup
    select(service)
    def unavailable(*_):
        raise RuntimeError('simulated unknown transport outcome')
    service.analyze_image = unavailable
    with pytest.raises(RuntimeError):
        service.observe_image(metadata(service, now), IMAGE, 'image/png')
    assert not service.status()['state']['enabled']
    with pytest.raises(ValueError, match='disabled'):
        service.observe_image(metadata(service, now, 1), IMAGE, 'image/png')


@pytest.mark.parametrize('change', ['goal', 'mode', 'stop', 'forget'])
def test_refreshes_do_not_override_goal_mode_privacy_or_stop_during_review(setup, change):
    core, service, now = setup
    select(service); analyzed(service)
    service.observe_image(metadata(service, now), IMAGE, 'image/png')
    source_id = service.status()['context']['source_ids'][-1]
    entered, release = Event(), Event()
    results = []
    core.draft = lambda *_: (entered.set(), release.wait(5), AssistantResponse(display_text='Must be withheld.'))[-1]
    thread = Thread(target=lambda: results.append(service.review('pending')))
    thread.start()
    try:
        assert entered.wait(5)
        now[0] += 10
        assert service.observe_image(metadata(service, now, 1), IMAGE, 'image/png')['status'] == 'refreshed'
        if change == 'goal': service.control('goal_change', 'goal', {'text': 'A different goal.'})
        elif change == 'mode': service.control('mode_change', 'mode', {'mode': 'quiet'})
        elif change == 'stop': service.control('stop', 'stop', {})
        else: core.personal_control('forget', 'forget', {'target_id': source_id})
        release.set(); thread.join(5)
        assert results[0]['status'] == 'cancelled' and not results[0].get('text')
    finally:
        release.set(); thread.join(5)


def test_at_most_four_frame_callers_and_one_perception_are_admitted(setup):
    _, service, now = setup
    select(service)
    entered, release, payload_release, all_payloads = Event(), Event(), Event(), Event()
    calls, results, count = [], [], []
    def analyze(raw, *_):
        calls.append(raw); entered.set(); assert release.wait(5)
        return {'summary': 'A test panel.', 'confidence': .6}
    service.analyze_image = analyze
    first = Thread(target=lambda: results.append(service.observe_image(metadata(service, now), IMAGE, 'image/png')))
    first.start(); waiting = []
    try:
        assert entered.wait(5)
        now[0] += 1
        refresh = metadata(service, now, 1)
        original = service.frames._payload
        def payload(*args):
            value = original(*args); count.append(1)
            if len(count) == 3: all_payloads.set()
            assert payload_release.wait(5)
            return value
        service.frames._payload = payload
        for _ in range(3):
            thread = Thread(target=lambda: results.append(service.observe_image(refresh, IMAGE, 'image/png')))
            waiting.append(thread); thread.start()
        assert all_payloads.wait(5)
        assert service.observe_image(metadata(service, now, 2), IMAGE, 'image/png')['reason'] == 'frame_capacity'
        payload_release.set()
        for thread in waiting: thread.join(5)
        assert calls == [IMAGE]
        release.set(); first.join(5)
        assert calls == [IMAGE] and service.status()['state']['remaining_frames'] == 118
    finally:
        payload_release.set(); release.set(); first.join(5)
        for thread in waiting: thread.join(5)


def test_copied_snapshot_never_restores_frame_or_reaction_authority(setup):
    from gamma.coordination.shared_activity import invalidate_snapshot
    core, service, now = setup
    select(service); analyzed(service)
    service.observe_image(metadata(service, now), IMAGE, 'image/png')
    now[0] += 1
    service.observe_image(metadata(service, now, 1), IMAGE, 'image/png')
    before = core.store.read('shared_frame_receipt', 'image_1')['fingerprint']
    with core.store.transaction(core.token) as db:
        invalidate_snapshot(db)
    assert not service.status()['state']['enabled']
    assert service.status()['context']['observation'] is None
    receipt = core.store.read('shared_frame_receipt', 'image_1')
    assert receipt['fingerprint'] == before and receipt['invalidated']
    assert receipt['frame'] is None and receipt['result']['status'] == 'cancelled'
    assert core.store.read('shared_observation', 'image_0')['observation']['summary']
