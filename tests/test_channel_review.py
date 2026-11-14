"""Actual shared owner fences, no fictitious owner turn and no channel tool authority."""
import json
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from gamma.schemas.response import AssistantResponse, BoundedStateUpdates, ToolCall
from test_coordination_core import make_core


def event(**values):
    return {'channel': 'shared_activity', 'audience': 'owner_private', 'summary': 'A bank interface is visible.', **values}


def test_channel_draft_cannot_create_owner_turn_or_start_work(tmp_path):
    captured = []
    def draft(text, context):
        captured.append((text, json.loads(context)))
        return AssistantResponse(display_text='That looks like the bank.', audio_path='/private.wav',
            tool_calls=[ToolCall(tool='shell', args={'command': 'bad'})],
            state_updates=BoundedStateUpdates(activity_action='practice', practice_exercise='retry_budget'))
    core = make_core(tmp_path, draft=draft)
    try:
        before = core.store.read('brain', 'state')
        reply = core.review_channel(event(), 'screen-1')
        assert reply.display_text == 'That looks like the bank.'
        assert reply.tool_calls == [] and reply.audio_path is None
        assert reply.speech_text == '' and reply.state_updates == BoundedStateUpdates()
        assert core.store.read('brain', 'state') == before
        with core.store.transaction() as db:
            assert not core.store.list(db, 'turn', None)
            assert not core.store.list(db, 'run', None)
        assert captured[0][1]['channel_event'] == event()
        assert captured[0][1]['personal']['recall'] == []
        assert core.review_channel(event(), 'screen-1').display_text == reply.display_text
        assert len(captured) == 1
        with pytest.raises(ValueError, match='reused'):
            core.review_channel(event(summary='different'), 'screen-1')
    finally:
        core.close()


@pytest.mark.parametrize('action', ['stay_silent', 'tool_first', 'defer'])
def test_channel_preserves_non_contribution_choice(tmp_path, action):
    core = make_core(tmp_path, draft=lambda *_: AssistantResponse(display_text='Do not deliver', response_action=action))
    try:
        response = core.review_channel(event(), 'quiet')
        assert not response.display_text and not response.speech_text
    finally:
        core.close()


def test_foreground_change_during_channel_draft_withholds_stale_result(tmp_path):
    entered, release = threading.Event(), threading.Event()
    def draft(*_):
        entered.set()
        assert release.wait(5)
        return AssistantResponse(display_text='Late comment')
    core = make_core(tmp_path, draft=draft)
    try:
        with ThreadPoolExecutor() as pool:
            result = pool.submit(core.review_channel, event(), 'late')
            assert entered.wait(5)
            core.control('sleep', 'sleep')
            release.set()
            assert result.result(5).reason_code == 'channel_context_changed'
        assert core.store.read('channel_review', 'late')['status'] == 'stale'
        core.control('wake', 'wake')
        assert core.review_channel(event(), 'late').reason_code == 'channel_review_not_replayed'
    finally:
        release.set()
        core.close()


def test_failed_channel_is_not_automatically_retried(tmp_path):
    calls = []
    def draft(*_):
        calls.append(1)
        raise TimeoutError('uncertain backend')
    core = make_core(tmp_path, draft=draft)
    try:
        with pytest.raises(TimeoutError):
            core.review_channel(event(), 'failed')
        assert core.review_channel(event(), 'failed').reason_code == 'channel_review_not_replayed'
        assert len(calls) == 1
    finally:
        core.close()


def test_public_audience_is_not_routed_into_private_channel(tmp_path):
    core = make_core(tmp_path)
    try:
        with pytest.raises(ValueError):
            core.review_channel(event(audience='public_stream'), 'public')
    finally:
        core.close()
