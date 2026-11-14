"""Real Core/store and transport checks with declared synthetic dialogue choices."""
import json
from types import SimpleNamespace

import pytest

from gamma.coordination.core import Core
from gamma.coordination.phone import PhoneAccess, invalidate_snapshot, redact_sources
from gamma.coordination.phone_policy import contact_block_reason
from gamma.coordination.store import Store, OwnershipLost
from gamma.schemas.response import AssistantResponse


SECRET = 'S' * 43


@pytest.fixture
def room(tmp_path):
    now = [1000.]
    drafted = []
    def draft(text, context):
        drafted.append((text, json.loads(context)))
        return AssistantResponse(display_text='You planned to review the workshop notes. Is now a useful moment?',
                                 delivery_mode='text_only')
    core = Core(Store(tmp_path), runner=SimpleNamespace(), draft=draft, clock=lambda: now[0])
    phone = PhoneAccess(core)
    device = 'android-test'
    phone.register('register', device, SECRET, 'Private Android browser')
    epoch = phone.connect(device, SECRET, 'connect')['device']['epoch']
    state = SimpleNamespace(core=core, phone=phone, now=now, drafted=drafted,
                            device=device, epoch=epoch, closed=False, root=tmp_path)
    yield state
    if not state.closed:
        core.close()


def commitment(room, text='Review workshop notes', topic='workshop'):
    return room.core.personal_control('memory-' + topic, 'memory', {'op': 'remember', 'kind': 'commitment',
        'subject_id': 'owner', 'topic': topic, 'text': text})['id']


def allow(room, **changes):
    values = {'enabled': True, 'authority_expires_at': room.now[0] + 3600, 'quiet_until': 0.,
              'minimum_interval': 60, 'maximum_per_day': 3, **changes}
    return room.phone.control(room.device, SECRET, room.epoch, 'contact_policy', values)


def offer(room):
    allow(room)
    result = room.phone.consider_due()
    assert result['status'] == 'ready', result
    value = room.phone.poll(room.device, SECRET, room.epoch)
    assert value['message'], value
    return value['message']


def test_phone_reuses_desktop_identity_and_exact_turn_without_duplicate_commitment(room):
    room.core.input('Remember our workshop plan.', 'monitor-private', 'desktop-turn')
    memory = commitment(room)
    response = room.phone.send(room.device, SECRET, room.epoch, 'phone-turn', 'How is our workshop plan?')
    repeated = room.phone.send(room.device, SECRET, room.epoch, 'phone-turn', 'How is our workshop plan?')
    assert response == repeated and response['status'] == 'completed'
    assert len(room.drafted) == 2
    conversation = room.phone.conversation(room.device, SECRET, room.epoch)
    assert [t['id'] for t in conversation['turns']] == ['desktop-turn', 'phone-turn']
    assert conversation['identity_id'] == room.core.personal_state()['identity_id']
    assert room.core.store.read('personal_record', memory)['status'] == 'active'
    assert len([r for r in room.core.personal_state()['records'] if r['kind'] == 'commitment']) == 1


def test_request_reuse_cannot_change_text_or_read_other_device(room):
    room.phone.send(room.device, SECRET, room.epoch, 'one', 'First message')
    with pytest.raises(ValueError, match='reused'):
        room.phone.send(room.device, SECRET, room.epoch, 'one', 'Different message')
    room.phone.register('reg-two', 'second-device', 'T' * 43)
    epoch = room.phone.connect('second-device', 'T' * 43, 'connect-two')['device']['epoch']
    with pytest.raises(ValueError, match='unknown_phone_request'):
        room.phone.result('second-device', 'T' * 43, epoch, 'one')


def test_secret_is_hashed_and_wrong_device_or_stale_epoch_rejected(room):
    raw = room.core.store.read('phone_device', room.device)
    assert SECRET not in json.dumps(raw)
    assert SECRET not in json.dumps(room.phone.snapshot())
    with pytest.raises(ValueError, match='authentication'):
        room.phone.state(room.device, 'X' * 43, room.epoch)
    previous = room.epoch
    room.epoch = room.phone.connect(room.device, SECRET, 'reconnect')['device']['epoch']
    assert room.epoch > previous
    with pytest.raises(ValueError, match='connection_changed'):
        room.phone.state(room.device, SECRET, previous)


def test_considered_reminder_has_actual_receipt_and_acknowledgment_ends_exchange(room):
    memory = commitment(room)
    before_sources = len(room.core.personal_state()['sources'])
    message = offer(room)
    assert room.phone.poll(room.device, SECRET, room.epoch)['message'] is None
    with pytest.raises(ValueError, match='out_of_order'):
        room.phone.receipt(room.device, SECRET, room.epoch, message['id'], 'acknowledged')
    assert room.phone.receipt(room.device, SECRET, room.epoch, message['id'], 'received')['status'] == 'received'
    result = room.phone.receipt(room.device, SECRET, room.epoch, message['id'], 'acknowledged')
    assert result == {'status': 'acknowledged', 'commitment_resolved': False}
    assert room.core.store.read('personal_record', memory)['status'] == 'active'
    room.now[0] += 301
    room.epoch = room.phone.connect(room.device, SECRET, 'after-ack')['device']['epoch']
    assert room.phone.consider_due()['status'] == 'idle'
    assert room.phone.due_candidates() == []
    assert len(room.core.personal_state()['sources']) == before_sources
    with room.core.store.transaction(room.core.token) as db:
        assert room.core.store.list(db, 'turn', None) == []


@pytest.mark.parametrize('condition', ['busy', 'quiet', 'focused', 'asleep', 'offline', 'quiet_period', 'expired_authority'])
def test_ineligible_contact_is_quiet_and_does_not_consume_commitment(room, condition):
    memory = commitment(room); allow(room)
    if condition in {'busy', 'quiet'}:
        room.phone.control(room.device, SECRET, room.epoch, 'availability', condition)
    elif condition in {'focused', 'asleep'}:
        room.core.control(condition, 'focus' if condition == 'focused' else 'sleep',
                          **({'focus': 'focused'} if condition == 'focused' else {}))
    elif condition == 'offline':
        room.phone.control(room.device, SECRET, room.epoch, 'disconnect')
    elif condition == 'quiet_period':
        allow(room, quiet_until=room.now[0] + 60)
    else:
        room.now[0] += 3601
        room.epoch = room.phone.connect(room.device, SECRET, 'late')['device']['epoch']
    assert room.phone.consider_due()['status'] == 'deferred'
    assert not room.drafted
    assert room.core.store.read('personal_record', memory)['status'] == 'active'


def test_dropped_offer_and_ack_loss_are_never_automatically_resent(room):
    commitment(room)
    message = offer(room)
    room.now[0] += 31
    state = room.phone.state(room.device, SECRET, room.epoch)
    assert state['messages'][0]['status'] == 'uncertain'
    assert 'text' not in state['messages'][0]
    room.now[0] += 301
    room.epoch = room.phone.connect(room.device, SECRET, 'retry-connection')['device']['epoch']
    assert room.phone.consider_due()['status'] == 'idle'
    assert room.phone.poll(room.device, SECRET, room.epoch)['message'] is None
    with pytest.raises(ValueError, match='message_not_available'):
        room.phone.receipt(room.device, SECRET, room.epoch, message['id'], 'received')


def test_source_removal_revokes_pending_and_received_payloads(room):
    memory = commitment(room)
    message = offer(room)
    room.phone.receipt(room.device, SECRET, room.epoch, message['id'], 'received')
    room.core.personal_control('remove-source', 'forget', {'target_id': memory})
    state = room.phone.state(room.device, SECRET, room.epoch)
    assert state['messages'][0]['status'] == 'cancelled'
    assert 'text' not in state['messages'][0]
    assert room.core.store.read('phone_message', message['id'])['text'] == ''
    assert room.phone.poll(room.device, SECRET, room.epoch)['message'] is None


def test_reply_retrieval_reads_canonical_redaction_without_stale_cache(room):
    room.phone.send(room.device, SECRET, room.epoch, 'sensitive', 'An ephemeral private note.')
    source = next(s for s in room.core.personal_state()['sources'] if s.get('turn_id') == 'sensitive')
    room.core.personal_control('forget-turn', 'forget', {'target_id': source['id']})
    result = room.phone.result(room.device, SECRET, room.epoch, 'sensitive')
    assert result['status'] == 'redacted' and 'response' not in result
    assert room.phone.conversation(room.device, SECRET, room.epoch)['turns'] == []


def test_sleep_queues_same_request_and_wake_catches_up_without_replay(room):
    room.core.control('sleep', 'sleep')
    result = room.phone.send(room.device, SECRET, room.epoch, 'sleep-message', 'Hello when you wake.')
    assert result['status'] == 'queued' and not room.drafted
    room.core.control('wake', 'wake')
    room.core.tick()
    room.core.dialogue_future.result(timeout=5)
    result = room.phone.result(room.device, SECRET, room.epoch, 'sleep-message')
    assert result['status'] == 'completed' and len(room.drafted) == 1
    assert room.phone.send(room.device, SECRET, room.epoch, 'sleep-message', 'Hello when you wake.') == result


def test_foreground_or_policy_change_during_consideration_withholds_offer(room, monkeypatch):
    memory = commitment(room); allow(room)
    def changed(context, request_id):
        room.core.control('focus-during-review', 'focus', focus='focused')
        return AssistantResponse(display_text='Never deliver this.', delivery_mode='text_only')
    monkeypatch.setattr(room.core, 'review_channel', changed)
    assert room.phone.consider_due()['status'] == 'withheld'
    assert room.core.store.read('personal_record', memory)['status'] == 'active'
    with room.core.store.transaction(room.core.token) as db:
        assert room.core.store.list(db, 'phone_message', None) == []


def test_quiet_review_is_not_a_declined_commitment_or_a_timed_send(room, monkeypatch):
    commitment(room); allow(room)
    monkeypatch.setattr(room.core, 'review_channel', lambda *_: AssistantResponse(response_action='stay_silent', delivery_mode='silent'))
    assert room.phone.consider_due()['reason'] == 'shared_owner_chose_quiet'
    assert room.phone.consider_due()['reason'] == 'review_backoff'
    assert room.phone.due_candidates()


def test_connection_loss_after_input_keeps_accepted_turn_but_withholds_egress(room, monkeypatch):
    original = room.core.input
    def interrupted(*args, **kwargs):
        value = original(*args, **kwargs)
        room.phone.control(room.device, SECRET, room.epoch, 'disconnect')
        return value
    monkeypatch.setattr(room.core, 'input', interrupted)
    with pytest.raises(ValueError, match='reconnect'):
        room.phone.send(room.device, SECRET, room.epoch, 'in-flight', 'Keep the saved result.')
    assert room.core.store.read('turn', 'in-flight')['status'] == 'completed'
    room.epoch = room.phone.connect(room.device, SECRET, 'later')['device']['epoch']
    assert room.phone.result(room.device, SECRET, room.epoch, 'in-flight')['status'] == 'completed'
    assert len(room.drafted) == 1


def test_restore_invalidates_transport_and_keeps_commitment_and_ack_fingerprints(room):
    memory = commitment(room)
    message = offer(room)
    room.phone.receipt(room.device, SECRET, room.epoch, message['id'], 'received')
    room.phone.receipt(room.device, SECRET, room.epoch, message['id'], 'acknowledged')
    room.phone.send(room.device, SECRET, room.epoch, 'restore-request', 'Retain this real turn.')
    with room.core.store.transaction(room.core.token) as db:
        result = invalidate_snapshot(db)
    assert result['devices_invalidated'] == result['message_payloads_removed'] == 1
    assert result['request_fingerprints_retained'] == 1
    assert room.core.store.read('personal_record', memory)['status'] == 'active'
    assert room.core.store.read('phone_message', message['id'])['status'] == 'acknowledged'
    assert room.core.store.read('phone_message', message['id'])['text'] == ''
    with pytest.raises(ValueError, match='authentication'):
        room.phone.connect(room.device, SECRET, 'restored-old-device')
    room.phone.register('new-device', 'new-android', 'T' * 43)
    assert room.phone.connect('new-android', 'T' * 43, 'new-connect')['identity_id'] == room.core.personal_state()['identity_id']


def test_generation_fences_device_and_transport_effects(room):
    room.core.store.claim(room.core.token[0])
    with pytest.raises(OwnershipLost):
        room.phone.state(room.device, SECRET, room.epoch)
    with pytest.raises(OwnershipLost):
        room.phone.register('stale', 'stale-device', 'T' * 43)


def test_actual_owner_restart_requires_reconnect_and_keeps_uncertain_delivery_and_identity(room):
    memory = commitment(room); message = offer(room)
    identity = room.core.personal_state()['identity_id']
    room.phone.send(room.device, SECRET, room.epoch, 'saved-before-exit', 'Keep the same conversation.')
    room.core.close(); room.closed = True
    reopened = Core(Store(room.root), runner=SimpleNamespace(), draft=lambda *_:AssistantResponse(
        display_text='After restart.', delivery_mode='text_only'), clock=lambda: room.now[0])
    try:
        phone = PhoneAccess(reopened)
        with pytest.raises(ValueError, match='reconnect'):
            phone.state(room.device, SECRET, room.epoch)
        epoch = phone.connect(room.device, SECRET, 'after-owner-restart')['device']['epoch']
        state = phone.state(room.device, SECRET, epoch)
        assert state['identity_id'] == identity
        assert state['messages'][0]['status'] == 'uncertain'
        assert phone.poll(room.device, SECRET, epoch)['message'] is None
        assert reopened.store.read('personal_record', memory)['status'] == 'active'
        assert phone.result(room.device, SECRET, epoch, 'saved-before-exit')['status'] == 'completed'
        assert phone.conversation(room.device, SECRET, epoch)['turns'][0]['id'] == 'saved-before-exit'
    finally:
        reopened.close()


@pytest.mark.parametrize('value', [True, -1, float('nan'), float('inf'), '1000'])
def test_invalid_schedule_is_rejected_without_a_write(room, value):
    memory = commitment(room)
    with pytest.raises(ValueError, match='schedule'):
        room.phone.schedule(room.device, SECRET, room.epoch, memory, value, 1100)
    assert room.core.store.read('phone_schedule', memory) is None


def test_schedule_defers_consideration_until_due_without_granting_contact(room):
    memory = commitment(room)
    room.phone.schedule(room.device, SECRET, room.epoch, memory, 1060, 1600)
    assert room.phone.due_candidates() == []
    room.now[0] = 1060
    assert room.phone.due_candidates()[0]['explicit_schedule'] is True
    assert room.phone.consider_due()['status'] == 'deferred' and not room.drafted


def test_unoffered_stale_context_is_erased_and_can_be_considered_again(room):
    commitment(room); allow(room)
    ready = room.phone.consider_due()
    assert ready['status'] == 'ready'
    room.core.control('focus-after-review', 'focus', focus='focused')
    assert room.phone.poll(room.device, SECRET, room.epoch)['message'] is None
    raw = room.core.store.read('phone_message', ready['message_id'])
    assert raw['status'] == 'cancelled' and raw['text'] == '' and 'contact_at' not in raw
    room.core.control('relax-again', 'focus', focus='relaxed')
    room.now[0] += 301
    room.epoch = room.phone.connect(room.device, SECRET, 'fresh-context')['device']['epoch']
    assert room.phone.consider_due()['status'] == 'ready'
    assert room.phone.poll(room.device, SECRET, room.epoch)['message']


def test_source_erasure_helper_physically_clears_derived_text_before_any_read(room):
    memory = commitment(room)
    message = offer(room)
    source = room.core.store.read('personal_record', memory)['source_id']
    with room.core.store.transaction(room.core.token) as db:
        result = redact_sources(db, source_ids={source})
    assert result['phone_payloads_redacted'] == 1
    assert room.core.store.read('phone_message', message['id'])['text'] == ''


def test_phone_request_device_capacity_honors_revoke_without_reusing_credentials(room):
    room.phone.control(room.device, SECRET, room.epoch, 'revoke')
    with pytest.raises(ValueError, match='authentication'):
        room.phone.connect(room.device, SECRET, 'old-device')
    room.phone.register('replacement', 'replacement-phone', 'N' * 43)
    assert room.phone.connect('replacement-phone', 'N' * 43, 'new-device')['device']['connected']


def test_owner_can_recover_eight_lost_device_keys_without_deleting_history(room):
    room.core.input('Keep this conversation.', 'monitor-private', 'before-lost-keys')
    for number in range(7):
        room.phone.register('register-' + str(number), 'lost-' + str(number), 'L' * 43)
    with pytest.raises(ValueError, match='phone_device_limit'):
        room.phone.register('ninth', 'new-phone', 'N' * 43)
    listing = room.phone.owner_devices()
    assert len(listing['devices']) == 8
    assert all(set(row) == {'id', 'label', 'last_seen_at', 'connected', 'revoked'} for row in listing['devices'])
    assert not any('secret' in key or 'hash' in key for row in listing['devices'] for key in row)
    result = room.phone.owner_revoke('owner-lost-key-revoke', 'lost-0')
    epoch_after_revoke = room.core.store.read('phone_device', 'lost-0')['epoch']
    assert room.phone.owner_revoke('owner-lost-key-revoke', 'lost-0') == result
    assert room.core.store.read('phone_device', 'lost-0')['epoch'] == epoch_after_revoke
    with pytest.raises(ValueError, match='request_id_reused'):
        room.phone.owner_revoke('owner-lost-key-revoke', 'lost-1')
    room.phone.register('ninth', 'new-phone', 'N' * 43)
    epoch = room.phone.connect('new-phone', 'N' * 43, 'connect-new-phone')['device']['epoch']
    assert room.phone.conversation('new-phone', 'N' * 43, epoch)['turns'][0]['id'] == 'before-lost-keys'


def test_owner_revocation_fences_inflight_receipt_and_erases_transport_payload(room):
    memory = commitment(room); message = offer(room)
    result = room.phone.owner_revoke('owner-device-revocation', room.device)
    assert result['canonical_history_changed'] is False
    assert room.core.store.read('phone_message', message['id'])['text'] == ''
    assert room.core.store.read('phone_message', message['id'])['status'] == 'uncertain'
    assert room.core.store.read('personal_record', memory)['status'] == 'active'
    with pytest.raises(ValueError, match='authentication'):
        room.phone.receipt(room.device, SECRET, room.epoch, message['id'], 'received')
    with pytest.raises(ValueError, match='authentication'):
        room.phone.connect(room.device, SECRET, 'lost-key-reconnect')


def rehearsal(room):
    from gamma.coordination.stream_session import StreamSession
    service = StreamSession(room.core, clock=lambda: room.now[0])
    session = service.start('private-rehearsal', {'audience': 'owner_private', 'format': 'coding',
        'broad_plan': 'Review one disposable workshop tool and talk about it.', 'duration_seconds': 900})
    return service, session['id']


@pytest.mark.parametrize('activity_state', ['unknown', 'active', 'stopped', 'deadline_passed'])
def test_phone_context_keeps_owner_commitment_timing_and_controller_facts_distinct(room, monkeypatch, activity_state):
    memory = commitment(room, text='Review workshop notes after this rehearsal.')
    if activity_state != 'unknown':
        stream, session_id = rehearsal(room)
        if activity_state == 'stopped':
            stream.control('stop-rehearsal', session_id, 'stop')
        elif activity_state == 'deadline_passed':
            room.now[0] += 901
            room.epoch = room.phone.connect(room.device, SECRET, 'after-deadline')['device']['epoch']
    room.phone.schedule(room.device, SECRET, room.epoch, memory, room.now[0], room.now[0] + 600)
    allow(room)
    captured = []
    def review(context, _request):
        captured.append(context)
        return AssistantResponse(response_action='stay_silent', delivery_mode='silent')
    monkeypatch.setattr(room.core, 'review_channel', review)
    assert room.phone.consider_due()['reason'] == 'shared_owner_chose_quiet'
    context = captured[0]
    candidate = context['candidate']
    assert candidate['subject_id'] == 'owner' and candidate['commitment_status'] == 'unfinished'
    assert candidate['source_kind'] == 'administrative_edit'
    assert candidate['schedule_semantics'] == 'consideration_eligibility_only'
    assert candidate['scheduled_for'] == room.now[0]
    assert context['verified_prerequisite_events'] == []
    if activity_state == 'unknown':
        assert context['observed_activity'] == []
    else:
        observed = context['observed_activity'][0]
        assert observed['session_id'] == session_id
        assert observed['status'] == ('stopped' if activity_state == 'stopped' else 'active')
        assert observed['source_role'] == 'observed_controller_state'
        assert observed['successful_rehearsal_completion_verified'] is False
        assert observed['deadline_passed'] == (activity_state == 'deadline_passed')


def test_rehearsal_transition_during_contact_review_withholds_without_core_sequence_change(room, monkeypatch):
    commitment(room, text='Review workshop notes after this rehearsal.'); allow(room)
    stream, session_id = rehearsal(room)
    original_sequence = room.core.store.read('brain', 'state')['sequence']
    def review(_context, _request):
        stream.control('stop-during-review', session_id, 'stop')
        return AssistantResponse(display_text='Stale contact should not escape.', delivery_mode='text_only')
    monkeypatch.setattr(room.core, 'review_channel', review)
    assert room.phone.consider_due()['status'] == 'withheld'
    assert room.core.store.read('brain', 'state')['sequence'] == original_sequence
    with room.core.store.transaction(room.core.token) as db:
        assert room.core.store.list(db, 'phone_message', None) == []


def test_ready_contact_rechecks_activity_evidence_before_offer(room):
    commitment(room); allow(room)
    stream, session_id = rehearsal(room)
    ready = room.phone.consider_due()
    assert ready['status'] == 'ready'
    stream.control('stop-before-offer', session_id, 'stop')
    assert room.phone.poll(room.device, SECRET, room.epoch)['message'] is None
    message = room.core.store.read('phone_message', ready['message_id'])
    assert message['status'] == 'cancelled' and message['text'] == ''
    assert message.get('contact_at') is None


@pytest.mark.parametrize('changes,contacts,expected', [
    ({}, [950], 'contact_cooldown'), ({}, [940], None),
    ({}, [100, 200, 300], 'daily_contact_limit'),
    ({'quiet_until': 1001}, [], 'quiet_period'),
    ({'minimum_interval': True}, [], 'invalid_contact_state'),
    ({'maximum_per_day': 100}, [], 'invalid_contact_state'),
    ({}, [1001], 'invalid_contact_state'),
    ({}, [float('nan')], 'invalid_contact_state'),
])
def test_mechanical_rate_and_quiet_limits(changes, contacts, expected):
    policy = {'enabled': True, 'quiet_until': 0, 'minimum_interval': 60, 'maximum_per_day': 3, **changes}
    assert contact_block_reason({'awake': True, 'focus': 'relaxed'},
        {'connected': True, 'expires_at': 1100, 'availability': 'available'}, policy, 1000, contacts) == expected


@pytest.mark.parametrize('field,value', [('awake', 'true'), ('focus', []), ('focus', 'unknown')])
def test_contact_policy_invalid_brain_fails_closed(field, value):
    brain = {'awake': True, 'focus': 'relaxed', field: value}
    assert contact_block_reason(brain, {'connected': True, 'expires_at': 2000, 'availability': 'available'},
        {'enabled': True, 'quiet_until': 0, 'minimum_interval': 60, 'maximum_per_day': 3}, 1000) == 'invalid_contact_state'
