"""Independent local-worker contract for bounded safety review."""
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

fake = ModuleType('_review_config.config')
fake.settings = SimpleNamespace(speech_filter_llm_model='local-review',local_llm_tagging_model='',local_llm_light_model='',speech_filter_llm_enabled=True,speech_filter_llm_temperature=0,local_llm_endpoint='http://localhost:11434',local_llm_timeout_seconds=20)
sys.modules['_review_config.config'] = fake
spec=importlib.util.spec_from_file_location('_review_config.safety.llm_reviewer',Path(__file__).parents[1]/'src/gamma/safety/llm_reviewer.py')
module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)


class ReviewTests(unittest.TestCase):
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
