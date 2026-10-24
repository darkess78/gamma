"""Actual storage and canonical prompt/status readers. No inference or output."""
import json
import os
from asyncio import CancelledError

import pytest

from gamma.config import settings
from gamma.conversation.service import ConversationService
from gamma.persona.assistant_state import AssistantStateStore
from gamma.persona.emotion_service import EmotionMemoryService
from gamma.persona import loader
from gamma.schemas.conversation import RecipientContext


@pytest.fixture
def stores(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'assistant_state_enabled', True)
    monkeypatch.setattr(settings, 'assistant_emotion_episode_threshold', 0.0)
    legacy = AssistantStateStore(tmp_path / 'legacy.json')
    canonical = EmotionMemoryService(tmp_path / 'canonical.json')
    legacy.update(emotion='neutral', user_text='previous', reply_text='previous')
    canonical.update_from_turn(emotion='neutral', user_text='previous', reply_text='previous')
    service = ConversationService.__new__(ConversationService)
    service._assistant_state, service._emotion_memory = legacy, canonical
    monkeypatch.setattr(loader, 'EmotionMemoryService', lambda: canonical)
    return service, legacy, canonical


def fail_paths(monkeypatch, paths, error=OSError):
    original = os.replace
    def replace(source, destination):
        if destination in paths or str(destination) in {str(path.resolve()) for path in paths}:
            raise error('synthetic storage interruption')
        return original(source, destination)
    monkeypatch.setattr(os, 'replace', replace)


def remember(service, session='PRIVATE_SESSION_MARKER'):
    service._remember_assistant_state(user_text='PRIVATE_CAUSE_MARKER', reply_text='useful reply',
                                      emotion='happy', session_id=session)


@pytest.mark.parametrize('private', [False, True])
def test_legacy_failure_preserves_bytes_and_updates_actual_canonical_readers(stores, monkeypatch, private):
    service, legacy, canonical = stores
    before = legacy._path.read_bytes()
    fail_paths(monkeypatch, {legacy._path})
    remember(service, 'PRIVATE_SESSION_ONE')
    remember(service, 'PRIVATE_SESSION_TWO')
    assert legacy._path.read_bytes() == before
    bundle = canonical.load_bundle()
    assert bundle['state'].current_emotion == 'happy'
    assert [episode.session_id for episode in bundle['episodes'][-2:]] == ['PRIVATE_SESSION_ONE', 'PRIVATE_SESSION_TWO']
    status = canonical.dashboard_payload()
    assert status['state']['current_emotion'] == 'happy'
    assert status['episodes'][-1]['session_id'] == 'PRIVATE_SESSION_TWO'
    recipient = RecipientContext(kind='owner_private', owner_authorized=True) if private else RecipientContext(kind='shared')
    prompt = loader.build_system_prompt(user_text='PRIVATE_CAUSE_MARKER', recipient=recipient)
    assert 'current_emotion: happy' in prompt
    assert ('PRIVATE_CAUSE_MARKER' in prompt) is private
    assert not list(legacy._path.parent.glob('*.tmp-*'))


def test_canonical_failure_preserves_its_bytes_and_compatibility_still_updates(stores, monkeypatch):
    service, legacy, canonical = stores
    before = canonical._path.read_bytes()
    fail_paths(monkeypatch, {canonical._path})
    remember(service)
    assert canonical._path.read_bytes() == before
    assert legacy.load().current_emotion == 'happy'
    assert canonical.dashboard_payload()['state']['current_emotion'] == 'neutral'
    prompt = loader.build_system_prompt(recipient=RecipientContext(kind='owner_private', owner_authorized=True))
    assert 'current_emotion: neutral' in prompt and 'PRIVATE_CAUSE_MARKER' not in prompt


def test_both_ordinary_write_failures_preserve_previous_complete_files(stores, monkeypatch):
    service, legacy, canonical = stores
    paths = [legacy._path, canonical._path]
    before = [path.read_bytes() for path in paths]
    fail_paths(monkeypatch, set(paths))
    remember(service)
    assert [path.read_bytes() for path in paths] == before
    assert all(isinstance(json.loads(path.read_text()), dict) for path in paths)
    assert not list(legacy._path.parent.glob('*.tmp-*'))


@pytest.mark.parametrize('target', ['legacy', 'canonical'])
@pytest.mark.parametrize('error', [KeyboardInterrupt, SystemExit, CancelledError])
def test_storage_interruption_propagates_and_releases_actual_locks(stores, monkeypatch, target, error):
    service, legacy, canonical = stores
    failed = legacy if target == 'legacy' else canonical
    before = failed._path.read_bytes()
    with monkeypatch.context() as patch:
        fail_paths(patch, {failed._path}, error)
        with pytest.raises(error): remember(service)
    assert failed._path.read_bytes() == before
    if target == 'canonical': assert legacy.load().current_emotion == 'happy'
    assert not list(failed._path.parent.glob('*.tmp-*'))
    # A subsequent real mutation proves the interrupted writer released its lock.
    remember(service, 'AFTER_INTERRUPTION')
    assert legacy.load().current_emotion == 'happy'
    assert canonical.dashboard_payload()['episodes'][-1]['session_id'] == 'AFTER_INTERRUPTION'


def test_disabled_state_preserves_both_persisted_files(stores, monkeypatch):
    service, legacy, canonical = stores
    before = [legacy._path.read_bytes(), canonical._path.read_bytes()]
    monkeypatch.setattr(settings, 'assistant_state_enabled', False)
    remember(service)
    assert [legacy._path.read_bytes(), canonical._path.read_bytes()] == before
