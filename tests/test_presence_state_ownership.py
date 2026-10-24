"""Backend Presence state only. No generation, dispatch, consumer or UI execution."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from gamma.config import settings
from gamma import presence
from gamma.conversation.service import ConversationService
from gamma.persona import state_io
from gamma.proactive import ProactiveScheduler


@pytest.fixture
def backend(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'data_dir', tmp_path / 'data')
    service = presence.PresenceService.__new__(presence.PresenceService)
    service._conversation = SimpleNamespace(continuity_snapshot=lambda _: None, durable_output_state=lambda _: None)
    service._bus = SimpleNamespace(stats=lambda: {})
    initial = service.transition('wake', updated_by='owner-first')['state']
    return service, initial


def newest(service, mode='sleep'):
    value = service.transition(mode, confirm_public_output=mode == 'go_live', updated_by='owner-new-command')['state']
    value['audience'] = {'kind': 'unknown', 'display_name': 'new selected audience'}
    value['activity'].update(current_turn_id='new-active-turn', session_id='new-active-session', stream_ready_mode='new-ready')
    value['wake']['enabled'] = False
    value['autonomy']['scheduler']['reason'] = 'new-owner-bookkeeping'
    return presence.save_presence_state(value)


@pytest.mark.parametrize('mode', ['sleep', 'break', 'wake', 'go_live'])
def test_late_wake_bookkeeping_preserves_new_owner_control_and_activity(backend, mode):
    service, old = backend
    expected = newest(service, mode)
    result = service._record_wake_result(deepcopy(old), status='suppressed', reason='old-completion',
                                         session_id='old-session', turn_id='old-turn')
    assert result['ok']
    assert presence.load_presence_state() == expected
    assert result['state'] == expected


def test_late_wake_opening_is_history_only_not_current_activity(backend):
    service, old = backend
    expected = newest(service)
    service._record_wake_result(deepcopy(old), status='text_only', opening='OLD_COMPLETION',
                              session_id='old-session', turn_id='old-turn')
    saved = presence.load_presence_state()
    history = saved['wake'].pop('recent_openings')
    baseline = deepcopy(expected); baseline['wake'].pop('recent_openings')
    assert saved == baseline
    assert history[-1]['opening'] == 'OLD_COMPLETION' and history[-1]['status'] == 'stale'


def test_late_wake_cannot_overwrite_new_work_in_same_owner_mode(backend):
    service, old = backend
    current = deepcopy(old); current['activity'].update(current_turn_id='new-work', session_id='new-session')
    expected = presence.save_presence_state(current)
    service._record_wake_result(deepcopy(old), status='suppressed', reason='old-completion')
    assert presence.load_presence_state() == expected


def test_unchanged_wake_bookkeeping_keeps_control_and_records_result(backend):
    service, old = backend
    result = service._record_wake_result(deepcopy(old), status='suppressed', reason='opening-disabled')
    saved = presence.load_presence_state()
    assert result['ok'] and saved['mode'] == 'wake' and saved['updated_by'] == old['updated_by']
    assert saved['wake']['last_status'] == 'suppressed' and saved['wake']['suppression_reason'] == 'opening-disabled'


@pytest.mark.parametrize('status', ['suppressed', 'emitted'])
def test_scheduler_late_bookkeeping_retains_new_owner_state(backend, status):
    service, old = backend
    expected = newest(service)
    scheduler = ProactiveScheduler.__new__(ProactiveScheduler)
    scheduler._record_status(status, 'old-check', state=old,
                             extra={'event_id': 'old-event', 'trace_id': 'old-trace', 'target': 'dashboard_monitor'})
    assert presence.load_presence_state() == expected


def test_scheduler_unchanged_state_records_bounded_status(backend):
    _, old = backend
    scheduler = ProactiveScheduler.__new__(ProactiveScheduler)
    scheduler._record_status('suppressed', 'resources_unavailable', state=old)
    saved = presence.load_presence_state()
    assert saved['updated_at'] == old['updated_at'] and saved['mode'] == 'wake'
    assert saved['autonomy']['scheduler']['reason'] == 'resources_unavailable'
    assert saved['autonomy']['scheduler']['next_check_at']


def test_scheduler_success_bookkeeping_updates_current_counters_without_generation(backend):
    _, old = backend
    scheduler = ProactiveScheduler.__new__(ProactiveScheduler)
    scheduler._record_status('emitted', 'eligible', state=old,
                             extra={'event_id': 'event', 'trace_id': 'trace', 'target': 'dashboard_monitor'})
    saved = presence.load_presence_state()
    assert saved['autonomy']['scheduler']['attempts_for_topic'] == 1
    assert saved['autonomy']['scheduler']['last_emitted_at']
    assert saved['activity']['last_autonomous_action']['event_id'] == 'event'


def test_status_poll_does_not_save_its_snapshot_over_a_new_owner_command(backend, monkeypatch):
    service, _ = backend
    original = presence.load_presence_state
    changed = False
    def read_then_owner(**kwargs):
        nonlocal changed
        result = original(**kwargs)
        if not changed and kwargs.get('downgrade_stale_live'):
            changed = True
            service.transition('sleep', updated_by='owner-during-poll')
        return result
    monkeypatch.setattr(presence, 'load_presence_state', read_then_owner)
    service.status()
    saved = original()
    assert saved['mode'] == 'sleep' and saved['updated_by'] == 'owner-during-poll'


def test_restart_downgrade_is_atomic_and_fresh_live_confirmation_is_preserved(backend):
    service, _ = backend
    old = presence.presence_state_for_mode('go_live', confirmed_live_at='2000-01-01T00:00:00Z')
    presence.save_presence_state(old)
    downgraded = presence.load_presence_state(downgrade_stale_live=True)
    assert presence.load_presence_state() == downgraded
    assert downgraded['mode'] == 'wake' and downgraded['desired_mode'] == 'go_live'
    assert downgraded['requires_confirmation'] and not downgraded['outputs']['voice']
    current = service.transition('go_live', confirm_public_output=True)['state']
    assert presence.load_presence_state(downgrade_stale_live=True) == current
    with pytest.raises(ValueError): service.transition('go_live')


def test_conversation_late_interaction_does_not_replace_owner_or_new_active_session(backend):
    service, old = backend
    expected = newest(service)
    ConversationService._record_presence_interaction('old-session', expected_state=old)
    assert presence.load_presence_state() == expected


def test_conversation_current_interaction_retains_unrelated_fields(backend):
    _, old = backend
    old['activity']['stream_ready_mode'] = 'keep'
    old = presence.save_presence_state(old)
    ConversationService._record_presence_interaction('current-session', expected_state=old)
    saved = presence.load_presence_state()
    assert saved['activity']['session_id'] == 'current-session'
    assert saved['activity']['stream_ready_mode'] == 'keep'
    assert saved['mode'] == old['mode'] and saved['updated_by'] == old['updated_by']


def test_first_interaction_handles_missing_state_without_overwriting_a_new_owner(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'data_dir', tmp_path / 'fresh-data')
    snapshot = presence.load_presence_state()
    assert not presence.presence_state_path().exists()
    ConversationService._record_presence_interaction('first-session', expected_state=snapshot)
    assert presence.load_presence_state()['activity']['session_id'] == 'first-session'
    snapshot = presence.load_presence_state()
    service = presence.PresenceService.__new__(presence.PresenceService)
    expected = newest(service)
    ConversationService._record_presence_interaction('old-first-session', expected_state=snapshot)
    assert presence.load_presence_state() == expected


@pytest.mark.parametrize('kind', ['wake', 'scheduler'])
@pytest.mark.parametrize('owner_command', [False, True])
def test_missing_file_bookkeeping_bootstrap_keeps_new_owner_race_protection(monkeypatch, tmp_path, kind, owner_command):
    monkeypatch.setattr(settings, 'data_dir', tmp_path / 'bootstrap-data')
    old = presence.load_presence_state()
    service = presence.PresenceService.__new__(presence.PresenceService)
    expected = service.transition('wake', updated_by='new-owner')['state'] if owner_command else None
    if kind == 'wake': service._record_wake_result(old, status='suppressed', reason='bootstrap-reason')
    else: ProactiveScheduler.__new__(ProactiveScheduler)._record_status('suppressed', 'bootstrap-reason', state=old)
    current = presence.load_presence_state()
    if owner_command: assert current == expected
    elif kind == 'wake': assert current['wake']['last_status'] == 'suppressed' and current['wake']['suppression_reason'] == 'bootstrap-reason'
    else: assert current['autonomy']['scheduler']['status'] == 'suppressed' and current['autonomy']['scheduler']['reason'] == 'bootstrap-reason'


@pytest.mark.skipif(os.name != 'posix', reason='Gamma Linux lock-boundary proof')
def test_external_status_and_audience_callbacks_run_outside_the_file_lock(backend):
    import fcntl
    from gamma.schemas.presence import AudienceSelection
    service, _ = backend
    called = []
    def unlocked(label):
        path = presence.presence_state_path().with_name('state.json.lock')
        with path.open('a+b') as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        called.append(label)
    service._bus = SimpleNamespace(stats=lambda: (unlocked('status') or {}))
    service._audience_payload = lambda _: (unlocked('audience') or {'kind': 'unknown'})
    service.status()
    service.transition('wake', audience=AudienceSelection(kind='unknown'))
    assert called == ['status', 'audience']


@pytest.mark.parametrize('evaluation', [False, True])
def test_actual_conversation_facade_keeps_captured_context_without_generation(backend, evaluation):
    from gamma.identity.profile import SpeakerProfile
    from gamma.schemas.response import AssistantResponse
    service, _ = backend
    conversation = ConversationService.__new__(ConversationService)
    conversation._identity = SimpleNamespace(resolve=lambda _: SpeakerProfile(name='FixtureOwner', trust='owner', is_owner=True))
    conversation._continuity = SimpleNamespace(begin_exchange=lambda **_: 'synthetic-exchange',
        complete_exchange=lambda **_: None, fail_exchange=lambda *_, **__: None)
    expected = []
    def state_only_callback(**kwargs):
        expected.append(newest(service))
        return AssistantResponse(display_text='SYNTHETIC_BACKEND_REPLY')
    conversation._respond = state_only_callback
    conversation.respond('SYNTHETIC_BACKEND_INPUT', session_id='old-session', evaluation_mode=evaluation)
    assert presence.load_presence_state() == expected[0]


def test_stale_history_is_bounded_and_retains_newer_history(backend):
    service, old = backend
    current = newest(service)
    current['wake']['recent_openings'] = [{'occurred_at':'synthetic', 'opening':str(i), 'status':'text_only'} for i in range(10)]
    presence.save_presence_state(current)
    service._record_wake_result(old, status='text_only', opening='old-delayed')
    saved = presence.load_presence_state()
    assert len(saved['wake']['recent_openings']) == 10
    assert [item['opening'] for item in saved['wake']['recent_openings'][:-1]] == [str(i) for i in range(1,10)]
    assert saved['wake']['recent_openings'][-1]['status'] == 'stale'
    assert saved['activity'] == current['activity'] and saved['lifecycle'] == current['lifecycle']


def test_concurrent_whole_state_saves_publish_only_complete_json(backend):
    _, initial = backend
    path = presence.presence_state_path()
    errors = []
    def writer(i):
        value = deepcopy(initial)
        value['activity'].update(session_id=str(i), payload=str(i) + ':' + 'x' * 32000)
        try: presence.save_presence_state(value)
        except BaseException as exc: errors.append(type(exc).__name__)
    observations = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(writer, i) for i in range(60)]
        while not all(f.done() for f in futures):
            raw = json.loads(path.read_text())
            if 'payload' in raw['activity']:
                assert raw['activity']['payload'].startswith(raw['activity']['session_id'] + ':')
            observations.append(raw['mode'])
        for future in futures: future.result()
    assert not errors and observations and set(observations) == {'wake'}
    assert not list(path.parent.glob('state.json.tmp*'))


@pytest.mark.parametrize('alias', [False, True])
def test_concurrent_read_modify_write_keeps_all_changes(backend, alias):
    _, initial = backend
    path = presence.presence_state_path()
    alternate = path.with_name('alias.json')
    if alias: alternate.symlink_to(path)
    def increment(i):
        def mutate(state):
            state['activity']['counter'] = int(state['activity'].get('counter', 0)) + 1
        presence.update_presence_state(mutate, path=alternate if alias and i % 2 else path)
    with ThreadPoolExecutor(max_workers=6) as pool: list(pool.map(increment, range(60)))
    saved = presence.load_presence_state()
    assert saved['activity']['counter'] == 60 and saved['updated_by'] == initial['updated_by']
    if alias: assert alternate.is_symlink()


@pytest.mark.parametrize('relative', [False, True])
def test_canonical_presence_reads_and_updates_retain_state_through_lexical_alias(backend, monkeypatch, relative):
    _, initial = backend
    path = presence.presence_state_path()
    monkeypatch.chdir(path.parent)
    alias = Path('missing-component/../state.json') if relative else path.parent / 'missing-component' / '..' / 'state.json'
    assert alias.resolve() == path
    assert presence.load_presence_state(path=alias) == initial
    presence.update_presence_state(lambda state: state['activity'].update(counter=1), path=alias)
    current = presence.load_presence_state()
    assert current['mode'] == initial['mode'] and current['updated_by'] == initial['updated_by']
    assert current['activity']['counter'] == 1


CHILD = '''
from pathlib import Path
import sys,time
from gamma.presence import update_presence_state
path,mode,marker=sys.argv[1:]
def mutate(state):
 state['activity']['counter']=int(state['activity'].get('counter',0))+1
 if mode=='hold':
  Path(marker).write_text('locked')
  time.sleep(60)
for _ in range(12 if mode=='increment' else 1):update_presence_state(mutate,path=Path(path))
'''


@pytest.mark.skipif(os.name != 'posix', reason='Gamma Linux process/lock proof')
def test_separate_process_updates_keep_all_changes(backend):
    _, _ = backend
    path = presence.presence_state_path()
    children = [subprocess.Popen([sys.executable, '-B', '-c', CHILD, str(path), 'increment', '-'],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(4)]
    for child in children:
        stdout, stderr = child.communicate(timeout=20)
        assert child.returncode == 0, stdout + stderr
    assert presence.load_presence_state()['activity']['counter'] == 48


@pytest.mark.skipif(os.name != 'posix', reason='Gamma Linux process/lock proof')
def test_interrupted_process_preserves_file_and_releases_mutation_lock(backend, tmp_path):
    _, _ = backend
    path = presence.presence_state_path(); before = path.read_bytes(); marker = tmp_path / 'held'
    child = subprocess.Popen([sys.executable, '-B', '-c', CHILD, str(path), 'hold', str(marker)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and child.poll() is None and time.monotonic() < deadline: time.sleep(.02)
        assert marker.exists()
    finally:
        if child.poll() is None: child.kill()
        child.communicate(timeout=10)
    assert path.read_bytes() == before
    presence.update_presence_state(lambda state: state['activity'].update(counter=1))
    assert presence.load_presence_state()['activity']['counter'] == 1


@pytest.mark.parametrize('failure', ['callback', 'interrupt', 'serialization', 'replace'])
def test_failed_mutation_keeps_canonical_bytes_and_releases_lock(backend, monkeypatch, failure):
    _, _ = backend
    path = presence.presence_state_path(); before = path.read_bytes()
    def mutate(state):
        state['activity']['counter'] = 9
        if failure == 'callback': raise ValueError('synthetic')
        if failure == 'interrupt': raise KeyboardInterrupt()
        if failure == 'serialization': state['activity']['invalid'] = object()
    with monkeypatch.context() as patch:
        if failure == 'replace': patch.setattr(state_io.os, 'replace', lambda *_: (_ for _ in ()).throw(OSError('synthetic')))
        with pytest.raises((ValueError, KeyboardInterrupt, TypeError, OSError)): presence.update_presence_state(mutate)
    assert path.read_bytes() == before
    assert not list(path.parent.glob('state.json.tmp*'))
    presence.update_presence_state(lambda state: state['activity'].update(counter=1))
    assert presence.load_presence_state()['activity']['counter'] == 1
