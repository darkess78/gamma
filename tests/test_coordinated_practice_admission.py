"""Resident model compatibility does not bypass foreground admission."""
from unittest.mock import patch

import pytest

from gamma.coding.learner import LocalPracticeLearner
from gamma.coding.durable_practice import PracticeYield
from gamma.coding.curriculum import discord_foundations
from gamma.coordination.core import Core
from gamma.coordination.store import Store, OwnershipLost
from gamma.coding.durable_practice import PracticeRunner
from gamma.schemas.response import AssistantResponse


def test_only_coordinated_practice_accepts_its_resident_model():
    learner = LocalPracticeLearner(endpoint='http://127.0.0.1:11435', model='qwen3.5:4b', coordinated=True)
    with patch.object(learner, '_json', return_value={'models': [{'name': 'qwen3.5:4b'}]}):
        learner.require_idle()
        learner.coordinated = False
        with pytest.raises(RuntimeError):
            learner.require_idle()
    learner.coordinated = True
    for value in [{'models': [{'name': 'unrelated'}]}, {'models': [None]}, {}, {'models': None}]:
        with patch.object(learner, '_json', return_value=value), pytest.raises(RuntimeError):
            learner.require_idle()


def test_admission_checkpoint_can_cancel_before_http_without_replay_or_attempt(tmp_path):
    class YieldingLearner:
        def __call__(self, *args):
            raise PracticeYield('foreground')
    store = Store(tmp_path)
    runner = PracticeRunner(store, propose=YieldingLearner(), check=lambda root, spec: {
        'name': spec.name, 'kind': spec.kind, 'passed': False, 'exit_code': 1})
    core = Core(store, draft=lambda *_: AssistantResponse(), runner=runner)
    try:
        run_id = core.control('priority', 'practice', exercise_id='retry_budget')['activity']['id']
        runner.run(run_id)
        record = store.read('run', run_id)
        checkpoint = runner.artifacts.get(record['checkpoint_ref'])
        assert record['status'] == 'queued'
        assert checkpoint['tasks'][0]['stage'] == 'need_proposal'
        assert checkpoint['tasks'][0]['attempt_count'] == 0
    finally:
        core.close()


def test_shutdown_invalidates_threads_before_releasing_owner(tmp_path):
    store = Store(tmp_path)
    runner = PracticeRunner(store, propose=lambda *_: {}, check=lambda *_: {})
    core = Core(store, draft=lambda *_: AssistantResponse(), runner=runner)
    token = core.token
    core.close()
    with pytest.raises(OwnershipLost):
        with store.transaction(token):
            pass


def test_isolated_conversation_bounds_local_output_without_changing_legacy_defaults():
    import json
    from gamma.llm.local_adapter import LocalLLMAdapter
    from gamma.config import settings
    from contextlib import nullcontext
    class Reply:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'{"response":"bounded reply"}'
    with (patch.object(settings, 'local_llm_enable_routing', False),
          patch('gamma.llm.local_adapter.backend_slot', return_value=nullcontext()),
          patch('gamma.llm.local_adapter.request.urlopen', return_value=Reply()) as call):
        from gamma.llm.base import LLMCallContext
        received = []
        def builder(budget):
            received.append(budget)
            return 'bounded context', 'bounded input', {}
        LocalLLMAdapter(think=False, maximum_output_tokens=1024, maximum_context_tokens=8192, json_output=True).generate_reply(
            'private context', 'hello', call_context=LLMCallContext(prompt_builder=builder))
        bounded = json.loads(call.call_args.args[0].data)
        assert bounded['think'] is False and bounded['options']['num_predict'] == 1024
        assert bounded['format'] == 'json'
        assert bounded['options']['num_ctx'] == 8192 and received == [6144]
        LocalLLMAdapter().generate_reply('context', 'hello')
        legacy = json.loads(call.call_args.args[0].data)
        assert 'think' not in legacy and 'num_predict' not in legacy.get('options', {})
        assert 'format' not in legacy
