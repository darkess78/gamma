"""Trusted bounded model retention preserves actual background HTTP/admission contracts."""
import json
from types import SimpleNamespace

import pytest

from gamma.coding.learner import LocalPracticeLearner
from gamma.coding.durable_practice import PracticeYield
from gamma.coordination.work_learner import LocalWorkProposer
from gamma.llm import backend_admission


@pytest.mark.parametrize('kind', ['practice', 'work'])
@pytest.mark.parametrize('retention', ['default', 0, '0', 1800, '30m'])
def test_actual_background_payload_has_only_explicit_bounded_retention(tmp_path, monkeypatch, kind, retention):
    monkeypatch.setattr(backend_admission, '_coordination_root', lambda:tmp_path/'admission')
    klass = LocalPracticeLearner if kind == 'practice' else LocalWorkProposer
    options = {} if retention == 'default' else {'keep_alive':retention}
    learner = klass(endpoint='http://127.0.0.1:17193', model='fictional:1b', coordinated=True, **options)
    learner.admission_ready = lambda:True
    checkpoints, requests = [], []
    learner.checkpoint = lambda:checkpoints.append(True)
    proposed = {'solution.py':'pass\n'} if kind == 'practice' else {'plan':['Read the admitted document.'], 'edits':{}}
    class Response:
        def __enter__(self):return self
        def __exit__(self, *_):pass
        def read(self, _maximum):
            return json.dumps({'done':True,'message':{'content':json.dumps(proposed)}}).encode()
    class Transport:
        def open(self, request, timeout):
            requests.append(request)
            return Response()
    learner.opener = Transport()
    if kind == 'practice':
        result = learner(SimpleNamespace(instructions='Repair the admitted solution.', skill='syntax'), {'solution.py':'pass'}, [])
    else:
        spec = {'goal':'Review an admitted document.', 'mode':'research', 'files':{'notes.txt':'fictional input'},
                'editable':[], 'checks':[]}
        result = learner(spec, {}, [])
    assert result == proposed and len(requests) == 1 and checkpoints
    request = requests[0]
    payload = json.loads(request.data)
    assert request.full_url == 'http://127.0.0.1:17193/api/chat'
    assert payload['keep_alive'] == (0 if retention == 'default' else retention)
    assert payload['model'] == 'fictional:1b' and payload['stream'] is False and payload['think'] is False
    assert payload['options']['num_ctx'] == 8192
    assert payload['options']['num_predict'] == (2048 if kind == 'practice' else 3072)


@pytest.mark.parametrize('klass', [LocalPracticeLearner, LocalWorkProposer])
@pytest.mark.parametrize('retention', [None, True, 1.0, -1, 1801, '31m', 'forever', '0\n'])
def test_unbounded_or_mistyped_work_retention_is_rejected(klass, retention):
    with pytest.raises(ValueError):
        klass(endpoint='http://127.0.0.1:17193', model='fictional:1b', keep_alive=retention)


@pytest.mark.parametrize('kind', ['practice', 'work'])
def test_retained_background_model_still_obeys_checkpoint_before_http(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(backend_admission, '_coordination_root', lambda:tmp_path/'admission')
    klass = LocalPracticeLearner if kind == 'practice' else LocalWorkProposer
    learner = klass(endpoint='http://127.0.0.1:17193', model='fictional:1b', coordinated=True, keep_alive='30m')
    learner.admission_ready = lambda:True
    def checkpoint():raise PracticeYield('foreground')
    learner.checkpoint = checkpoint
    class NoHTTP:
        def open(self, *_args, **_kwargs):pytest.fail('A retained model bypassed background admission.')
    learner.opener = NoHTTP()
    with pytest.raises(PracticeYield, match='foreground'):
        if kind == 'practice':
            learner(SimpleNamespace(instructions='Repair admitted solution.', skill='syntax'), {'solution.py':'pass'}, [])
        else:
            learner({'goal':'Review.', 'mode':'research', 'files':{}, 'editable':[], 'checks':[]}, {}, [])
