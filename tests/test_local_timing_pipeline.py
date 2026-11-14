"""Numeric-only diagnostics follow the actual checked-response pipeline."""
import json
from types import SimpleNamespace

import pytest

from gamma.config import settings
from gamma.conversation.service import ConversationService
from gamma.llm.base import LLMReply
from gamma.llm.local_metrics import allowlisted_local_timing
from gamma.safety.llm_reviewer import LLMReviewDecision
from gamma.safety.policy import SafetyPolicyResult, SpeechSafetyPolicy
from gamma.safety.speech_filter import SpeechFilterResult, SpeechSafetyFilter
from gamma.schemas.conversation import RecipientContext


def test_local_timing_allowlist_rejects_raw_and_nonfinite_data():
    values = {'review_ms': 12.5, 'load_ms': 4, 'eval_count': 18, 'tokens_per_second': 21.0,
              'prompt': 'private', 'response': 'private', 'model': 'private',
              'admission_ms': True, 'http_ms': float('inf'), 'eval_ms': -1,
              'total_ms': float('nan'), 'prompt_eval_count': 10**20}
    assert allowlisted_local_timing(values) == {'review_ms': 12.5, 'load_ms': 4,
                                                'eval_count': 18, 'tokens_per_second': 21.0}
    assert allowlisted_local_timing(None) == {}


def test_safety_result_metadata_defaults_are_not_shared():
    first = SafetyPolicyResult('hello', False, [], 'allow', [])
    second = SafetyPolicyResult('hello', False, [], 'allow', [])
    first.metadata['load_ms'] = 3
    assert second.metadata == {}
    first_filter = SpeechFilterResult('hello', False, [])
    second_filter = SpeechFilterResult('hello', False, [])
    first_filter.metadata['load_ms'] = 3
    assert second_filter.metadata == {}


def reviewer():
    return SimpleNamespace(review=lambda text: LLMReviewDecision(
        action='allow', reason='ordinary', confidence=1.0,
        metadata={'review_ms': 8.0, 'load_ms': 2.0, 'eval_count': 10,
                  'response': 'must not leak', 'http_ms': True}))


def test_allowed_speech_keeps_review_metrics_and_never_reuses_them(monkeypatch):
    monkeypatch.setattr(settings, 'speech_filter_llm_enabled', True)
    policy = SpeechSafetyPolicy('strict')
    policy._reviewer = reviewer()
    allowed = policy.apply('Hello, Neety.')
    assert allowed.action == 'allow' and allowed.layers == []
    assert allowed.metadata == {'review_ms': 8.0, 'load_ms': 2.0, 'eval_count': 10}
    deferred_review = policy.apply('Hello, Neety.', include_llm=False)
    assert deferred_review.metadata == {}
    filter = SpeechSafetyFilter('strict')
    filter._policy = policy
    assert filter.apply('Hello, Neety.').metadata == allowed.metadata
    policy._reviewer = SimpleNamespace(review=lambda text: SimpleNamespace(
        action='allow', reason='ordinary', confidence=1.0))
    assert filter.apply('Hello, Neety.').metadata == {}


@pytest.mark.parametrize('fast_mode', [True, False])
def test_actual_conversation_exposes_checked_stage_timings(monkeypatch, fast_mode):
    monkeypatch.setattr(settings, 'speech_filter_llm_enabled', True)
    monkeypatch.setattr('gamma.conversation.service.build_system_prompt', lambda **kwargs: 'test prompt')
    draft = json.dumps({'action': 'reply', 'requested_delivery': 'text_only',
                        'final_text': 'Hello, Neety.', 'reason_code': 'greeting'})
    class Adapter:
        def generate_reply(self, **kwargs):
            return LLMReply(text=draft, metadata={'local_timing': {
                'load_ms': 4.0, 'prompt_eval_ms': 8.0, 'eval_ms': 12.0,
                'admission_ms': 1.0, 'http_ms': 24.0, 'eval_count': 22,
                'prompt_eval_count': 44, 'prompt': 'must not leak', 'model': 'private'}})
    service = ConversationService()
    service._llm = Adapter()
    service._speech_filter._policy._reviewer = reviewer()
    response = service.respond('hello', fast_mode=fast_mode, evaluation_mode=True,
        recipient=RecipientContext('owner_private', 'dashboard_monitor', True), speech_allowed=False)
    assert response.display_text == 'Hello, Neety.'
    assert response.response_action == 'reply'
    assert response.timing_ms['draft_load_ms'] == 4.0
    assert response.timing_ms['draft_prompt_eval_ms'] == 8.0
    assert response.timing_ms['draft_eval_ms'] == 12.0
    assert response.timing_ms['draft_admission_ms'] == 1.0
    assert response.timing_ms['draft_http_ms'] == 24.0
    assert response.timing_ms['draft_token_count'] == 22
    assert response.timing_ms['draft_prompt_token_count'] == 44
    assert response.timing_ms['speech_check_ms'] >= 0
    assert response.tts_metadata['speech_filter']['timing_ms'] == {
        'review_ms': 8.0, 'load_ms': 2.0, 'eval_count': 10}
    assert 'must not leak' not in repr(response.timing_ms)
    assert 'must not leak' not in repr(response.tts_metadata['speech_filter'])
