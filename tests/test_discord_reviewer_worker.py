"""Bounded safety-review contract with the real backend admission boundary."""
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
from gamma.llm.backend_admission import backend_slot
from gamma.llm.local_adapter import current_local_admission

def _load_reviewer():
    """Load the real reviewer with isolated settings and restore every alias."""
    prefix = '_review_config'
    aliases = {}
    for name in (prefix, prefix + '.llm', prefix + '.safety'):
        package = ModuleType(name)
        package.__path__ = []
        aliases[name] = package
    fake = ModuleType(prefix + '.config')
    fake.settings = SimpleNamespace(speech_filter_llm_model='local-review',local_llm_tagging_model='',local_llm_light_model='',speech_filter_llm_enabled=True,speech_filter_llm_temperature=0,local_llm_endpoint='http://localhost:11434',local_llm_timeout_seconds=20)
    aliases[fake.__name__] = fake
    admission = ModuleType(prefix + '.llm.backend_admission')
    admission.backend_slot = backend_slot
    aliases[admission.__name__] = admission
    adapter = ModuleType(prefix + '.llm.local_adapter')
    adapter.current_local_admission = current_local_admission
    aliases[adapter.__name__] = adapter
    source = Path(__file__).parents[1] / 'src/gamma'
    with patch.dict(sys.modules, aliases):
        loaded = None
        for suffix, relative in (
            ('llm.local_metrics', 'llm/local_metrics.py'),
            ('llm.local_runtime', 'llm/local_runtime.py'),
            ('safety.llm_reviewer', 'safety/llm_reviewer.py'),
        ):
            spec = importlib.util.spec_from_file_location(prefix + '.' + suffix, source / relative)
            loaded = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = loaded
            spec.loader.exec_module(loaded)
        return loaded


module = _load_reviewer()


class ReviewTests(unittest.TestCase):
    def test_loader_uses_real_helpers_and_restores_aliases(self):
        before = {name: value for name, value in sys.modules.items()
                  if name == '_review_config' or name.startswith('_review_config.')}
        sentinel = ModuleType('_review_config.llm.local_metrics')
        with patch.dict(sys.modules, {'_review_config.llm.local_metrics': sentinel}):
            loaded = _load_reviewer()
            self.assertIs(sys.modules['_review_config.llm.local_metrics'], sentinel)
            self.assertEqual(loaded.local_generation_metrics({'load_duration': 1000000}), {'load_ms': 1.0})
            self.assertEqual(loaded.current_local_runtime_profile(), {})
            with self.assertRaises(ValueError):
                loaded.validate_review_num_gpu(True)
        after = {name: value for name, value in sys.modules.items()
                 if name == '_review_config' or name.startswith('_review_config.')}
        self.assertEqual(after, before)

    def review(self, raw):
        self.requests=[]
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return json.dumps({'response':raw}).encode()
        def open_request(request,**kwargs):self.requests.append(json.loads(request.data));return Response()
        with patch.object(module.request,'urlopen',side_effect=open_request):
            return module.SpeechLLMReviewer().review('Hello there')

    def test_explicit_valid_allow(self):
        result=self.review('{"action":"allow","reason":"ordinary greeting","confidence":0.9}')
        self.assertEqual(result.action,'allow')
        self.assertEqual(self.requests[0]['think'],False)
        self.assertEqual(self.requests[0]['format'],'json')
        self.assertLessEqual(self.requests[0]['options']['num_ctx'],8192)
        self.assertLessEqual(self.requests[0]['options']['num_predict'],256)
        self.assertGreaterEqual(self.requests[0]['options']['num_predict'],64)

    def test_missing_invalid_and_unparseable_never_allow(self):
        for raw in ['not JSON','{}','{"action":"invented"}','{"action":"allow","confidence":"invalid"}','{"action":"allow"}','{"action":"allow","confidence":true}','{"action":"allow","confidence":NaN}','{"action":"allow","confidence":2}','[]']:
            self.assertNotEqual(self.review(raw).action,'allow')

    def test_preserves_block_and_soften(self):
        for action in ['block','soften']:
            self.assertEqual(self.review(json.dumps({'action':action,'reason':'test','confidence':1})).action,action)

    def test_oversize_fails_without_truncating(self):
        with patch.object(module.request,'urlopen') as request:
            result=module.SpeechLLMReviewer().review('x'*100000)
            request.assert_not_called()
            self.assertNotEqual(result.action,'allow')

    def test_transport_failure_closed(self):
        with patch.object(module.request,'urlopen',side_effect=RuntimeError('private details')):
            result=module.SpeechLLMReviewer().review('hello')
        self.assertNotEqual(result.action,'allow')
        self.assertNotIn('private',result.reason)


if __name__=='__main__':unittest.main()
