"""Actual transport admission, held responses and cross-process recovery."""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from gamma.config import settings
from gamma.coding import autonomous, autonomous_store as store
from gamma.coding.learner import LocalPracticeLearner, PracticeInferenceUnavailable
from gamma.llm.local_adapter import LocalLLMAdapter
from gamma.safety.llm_reviewer import SpeechLLMReviewer
try:
    from gamma.llm import backend_admission as admission
except ImportError:
    admission = None

ENDPOINT = 'http://127.0.0.1:11434'


def until(predicate, timeout=4):
    end = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < end, 'Barrier deadline exceeded'
        time.sleep(.005)


class Response:
    def __init__(self, data, read=None):
        self.data, self.before_read = data, read
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, *args):
        if self.before_read: self.before_read()
        return json.dumps(self.data).encode()


class Transport:
    def __init__(self):
        self.posts = []
        self.holds = {}
        self.failures = {}
        self.models = []
        self.probes = 0
    def open(self, req, **kwargs):
        if req.full_url.endswith('/api/ps'):
            self.probes += 1
            return Response({'models': self.models})
        payload = json.loads(req.data)
        label = payload.get('prompt') or payload['messages'][-1]['content']
        if label.startswith('{'):
            label = json.loads(label)['label']
        self.posts.append((label, req.full_url, payload))
        def read():
            if label in self.holds: assert self.holds[label].wait(5), label
            if label in self.failures: raise self.failures[label]
        data = {'done': True, 'message': {'content': '{"ok": true}'},
                'response': json.dumps({'action': 'allow', 'confidence': 1, 'reason': 'safe'})}
        return Response(data, read)
    def hold(self, *labels):
        for label in labels: self.holds[label] = threading.Event()
    def seen(self, label): return any(p[0] == label for p in self.posts)
    def release(self):
        for event in self.holds.values(): event.set()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path / 'admission'
    if admission: monkeypatch.setattr(admission, '_coordination_root', lambda: root)
    monkeypatch.setattr(settings, 'local_llm_endpoint', ENDPOINT)
    monkeypatch.setattr(settings, 'local_llm_timeout_seconds', 2)
    monkeypatch.setattr(settings, 'speech_filter_llm_enabled', True)
    monkeypatch.setattr(settings, 'speech_filter_llm_model', 'review-model')
    transport = Transport()
    from urllib import request
    monkeypatch.setattr(request, 'urlopen', transport.open)
    monkeypatch.setattr(request, 'build_opener', lambda *a: transport)
    yield transport, root
    transport.release()


def background(label, endpoint=ENDPOINT):
    return LocalPracticeLearner(endpoint=endpoint, model='coding-model', timeout_seconds=2)._json(
        '/api/chat', {'model': 'coding-model', 'messages': [{'content': label}]})


def foreground(label, endpoint=ENDPOINT, review=False):
    if review: return SpeechLLMReviewer().review(label)
    return LocalLLMAdapter().generate_reply('unchanged persona', label,
        model_override='conversation-model', endpoint_override=endpoint)


def agent(tmp_path, label='C'):
    root = tmp_path / ('jobs-' + label)
    record = store.create_job(root, 'Synthetic admission goal')
    record.update(status='running', stage='planning')
    work = autonomous.RepositoryAgent(project=tmp_path, root=root, endpoint=ENDPOINT)
    work.record, work.path = record, store.job_path(root, record['id'])
    work.deadline = time.monotonic() + 5
    return work


def run(fn):
    results, errors = [], []
    def execute():
        try: results.append(fn())
        except BaseException as exc: errors.append(exc)
    thread = threading.Thread(target=execute, daemon=True)
    thread.start()
    return thread, results, errors


def finish(tasks):
    for thread, _, _ in tasks:
        thread.join(6)
        assert not thread.is_alive()


def registered(root, transport, label):
    if admission:
        until(lambda: bool(list(root.glob('*.wait-*'))))
    else:
        until(lambda: transport.seen(label))


@pytest.mark.parametrize('review', [False, True])
def test_actual_transport_conversation_precedes_next_repository_dispatch(setup, tmp_path, review):
    transport, root = setup
    transport.hold('A', 'B')
    work = agent(tmp_path)
    tasks = [run(lambda: background('A'))]
    try:
        until(lambda: transport.seen('A'))
        tasks.append(run(lambda: foreground('B', review=review)))
        registered(root, transport, 'B')
        tasks.append(run(lambda: work.ask('checker-model', 'unchanged instructions', {'label': 'C'})))
        assert not transport.seen('B') and not transport.seen('C')
        foreground('D', 'http://127.0.0.2:11434')
        assert transport.seen('D') and not transport.holds['A'].is_set()
        transport.holds['A'].set()
        until(lambda: transport.seen('B'))
        assert not transport.seen('C'), 'Successful open must stay exclusive until read consumes response'
        transport.holds['B'].set()
    finally:
        transport.release(); finish(tasks)
    assert all(not e for _, _, e in tasks)
    assert [p[0] for p in transport.posts if p[0] != 'D'] == ['A', 'B', 'C']
    assert work.record['model_calls'] == 1
    c = next(p[2] for p in transport.posts if p[0] == 'C')
    assert c['model'] == 'checker-model' and c['messages'][0]['content'] == 'unchanged instructions'
    assert not list(root.glob('*.wait-*'))


@pytest.mark.parametrize('control', ['stop', 'pause', 'budget'])
def test_actual_owner_checkpoints_remain_effective_while_waiting(setup, tmp_path, control):
    transport, root = setup; transport.hold('A', 'B'); work = agent(tmp_path)
    tasks = [run(lambda: background('A'))]
    try:
        until(lambda: transport.seen('A'))
        tasks.append(run(lambda: foreground('B'))); registered(root, transport, 'B')
        tasks.append(run(lambda: work.ask('coding-model', 'goal', {'label': 'C'})))
        until(lambda: transport.probes >= 2)
        if control == 'budget': work.deadline = time.monotonic() - 1
        else: store.control_job(work.root, work.record['id'], control)
        if control == 'pause':
            until(lambda: work.record['status'] == 'paused')
            transport.holds['A'].set(); until(lambda: transport.seen('B'))
            transport.holds['B'].set(); tasks[1][0].join(3)
            assert not transport.seen('C')
            store.control_job(work.root, work.record['id'], 'resume')
        else:
            tasks[2][0].join(2)
            assert not tasks[2][0].is_alive() and not transport.seen('C')
            assert isinstance(tasks[2][2][0], autonomous.Stopped if control == 'stop' else TimeoutError)
            assert 'last_transport' not in work.record and 'model_calls' not in work.record
    finally:
        if control == 'pause': store.control_job(work.root, work.record['id'], 'resume')
        transport.release(); finish(tasks)
    if control == 'pause': assert work.record['model_calls'] == 1 and not tasks[2][2]


@pytest.mark.parametrize('failure', [OSError('synthetic transport'), TimeoutError('synthetic timeout'), KeyboardInterrupt('synthetic interruption')])
@pytest.mark.parametrize('kind', ['conversation', 'review', 'coding'])
def test_transport_failure_releases_ownership_without_duplicate_generation(setup, failure, kind):
    transport, root = setup; transport.failures['A'] = failure
    fn = (lambda: background('A')) if kind == 'coding' else (lambda: foreground('A', review=kind == 'review'))
    if kind == 'review' and isinstance(failure, Exception): assert fn().action == 'soften'
    else:
        with pytest.raises(BaseException): fn()
    foreground('B')
    assert [p[0] for p in transport.posts] == ['A', 'B']
    assert not list(root.glob('*.wait-*'))


def test_admission_deadline_does_not_cancel_active_transport_or_dispatch_waiter(setup, monkeypatch):
    transport, root = setup; transport.hold('A')
    tasks = [run(lambda: background('A'))]
    try:
        until(lambda: transport.seen('A'))
        monkeypatch.setattr(settings, 'local_llm_timeout_seconds', .12)
        tasks.append(run(lambda: foreground('B')))
        tasks[1][0].join(1)
        assert not tasks[1][0].is_alive() and tasks[1][2]
        assert not transport.seen('B') and tasks[0][0].is_alive()
        assert not list(root.glob('*.wait-*'))
    finally: transport.release(); finish(tasks)
    assert [p[0] for p in transport.posts] == ['A']


def test_interrupted_foreground_waiter_cleans_registration_and_preserves_exception(setup, monkeypatch):
    transport, root = setup; transport.hold('A')
    tasks = [run(lambda: background('A'))]
    interruption = KeyboardInterrupt('wait interrupted')
    original_sleep = admission.time.sleep
    def sleep(seconds):
        if threading.current_thread().name == 'interrupted-waiter': raise interruption
        original_sleep(seconds)
    try:
        until(lambda: transport.seen('A'))
        monkeypatch.setattr(admission.time, 'sleep', sleep)
        def wait():
            threading.current_thread().name = 'interrupted-waiter'
            foreground('B')
        tasks.append(run(wait)); tasks[1][0].join(2)
        assert tasks[1][2] == [interruption] and tasks[0][0].is_alive()
        assert not transport.seen('B') and not list(root.glob('*.wait-*'))
    finally: transport.release(); finish(tasks)
    background('C')
    assert [p[0] for p in transport.posts] == ['A', 'C']


def test_lock_records_and_admission_errors_contain_no_endpoint_or_prompt(setup):
    _, root = setup
    endpoint = 'http://secret-user:secret-password@private.invalid:11434/'
    with admission.backend_slot(endpoint, foreground=True, timeout=1):
        assert root.stat().st_mode & 0o077 == 0
        for path in root.iterdir():
            assert path.read_bytes() == b'0'
            assert path.stat().st_mode & 0o077 == 0
            assert 'private' not in path.name and 'secret' not in path.name
    assert admission._resource_key(endpoint) == admission._resource_key('HTTP://PRIVATE.INVALID:11434')
    assert admission._resource_key('http://private.invalid') == admission._resource_key('http://private.invalid:80/')
    assert admission._resource_key('http://private.invalid:11434') != admission._resource_key('http://other.invalid:11434')
    with pytest.raises(TimeoutError, match='^Inference admission deadline exceeded$'):
        with admission.backend_slot(endpoint, foreground=True, timeout=0): pass


def test_default_root_is_shared_across_disposable_checkouts_and_tmpdirs(tmp_path):
    outputs = []
    for name in ['first', 'second']:
        directory = tmp_path / name; directory.mkdir()
        env = dict(os.environ, TMPDIR=str(directory), SHANA_DATA_DIR=str(directory/'data'),
                   SHANA_PROJECT_ROOT=str(directory))
        outputs.append(subprocess.check_output([sys.executable, '-B', '-c',
            'from gamma.llm.backend_admission import _coordination_root; print(_coordination_root())'],
            env=env, text=True).strip())
    assert outputs[0] == outputs[1] == str(Path.home()/'.cache/gamma/inference-admission')


def test_uncertain_upstream_work_is_not_reported_complete_or_replayed(setup, tmp_path):
    transport, _ = setup; work = agent(tmp_path, 'A')
    transport.failures['A'] = TimeoutError('transport ended, upstream remains uncertain')
    with pytest.raises(TimeoutError): work.ask('coding-model', 'goal', {'label': 'A'})
    assert 'last_transport' not in work.record and 'model_calls' not in work.record
    transport.models = [{'name': 'still resident upstream'}]
    with pytest.raises(PracticeInferenceUnavailable):
        LocalPracticeLearner(endpoint=ENDPOINT, model='coding-model')(
            type('Task', (), {'instructions': 'synthetic goal', 'skill': 'test'})(), {}, [])
    assert [p[0] for p in transport.posts] == ['A']


def test_slow_actual_idle_probe_cannot_dispatch_after_admission_deadline(setup, monkeypatch):
    transport, _ = setup; clock = [0.0]
    monkeypatch.setattr(admission.time, 'monotonic', lambda: clock[0])
    original = transport.open
    def open(req, **kwargs):
        if req.full_url.endswith('/api/ps'):
            return Response({'models': []}, lambda: clock.__setitem__(0, 1.0))
        return original(req, **kwargs)
    monkeypatch.setattr(transport, 'open', open)
    learner = LocalPracticeLearner(endpoint=ENDPOINT, model='coding-model')
    learner.timeout_seconds = .1
    with pytest.raises(TimeoutError, match='Inference admission deadline exceeded'):
        learner._json('/api/chat', {'messages': [{'content': 'A'}]})
    assert transport.posts == []


def test_cleanup_open_marker_race_cannot_replace_interruption_or_leave_exclusion(setup, monkeypatch):
    _, root = setup; original_unlink = Path.unlink; interruption = KeyboardInterrupt('original')
    def unlink(path, *args, **kwargs):
        if '.wait-' in path.name: raise PermissionError('synthetic open-handle race')
        return original_unlink(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'unlink', unlink)
        with pytest.raises(KeyboardInterrupt) as caught:
            with admission.backend_slot(ENDPOINT, foreground=True, timeout=1): raise interruption
    assert caught.value is interruption and list(root.glob('*.wait-*'))
    background('C')
    assert not list(root.glob('*.wait-*'))


def test_loaded_model_and_candidate_guards_are_rechecked_after_backend_wait(setup, tmp_path):
    transport, root = setup; transport.hold('A'); work = agent(tmp_path)
    tasks = [run(lambda: foreground('A'))]
    try:
        until(lambda: transport.seen('A'))
        tasks.append(run(lambda: work.ask('coding-model', 'goal', {'label': 'C'})))
        until(lambda: transport.probes >= 1)
        transport.models = [{'name': 'resident'}]
        with store.execution_lock(work.root.parent / 'candidates'):
            transport.holds['A'].set(); tasks[0][0].join(2)
            time.sleep(.12); assert not transport.seen('C')
        time.sleep(.12); assert not transport.seen('C')
        transport.models = []
    finally: transport.release(); finish(tasks)
    assert not tasks[1][2] and work.record['model_calls'] == 1


CHILD = '''
import json,sys,time
from pathlib import Path
from urllib import request
from gamma.llm import backend_admission as m
from gamma.llm.local_adapter import LocalLLMAdapter
from gamma.coding.learner import LocalPracticeLearner
from gamma.config import settings
r=Path(sys.argv[1]);name=sys.argv[2];kind=sys.argv[3];m._coordination_root=lambda:r/'locks'
class Response:
 def __init__(self,data,post=False):self.data=data;self.post=post
 def __enter__(self):return self
 def __exit__(self,*a):return False
 def read(self,*a):
  if self.post:
   (r/('dispatch-'+name)).write_text(str(__import__('os').getpid()))
   end=time.monotonic()+8
   while not (r/('release-'+name)).exists():
    if time.monotonic()>end:raise TimeoutError('synthetic barrier')
    time.sleep(.005)
  return json.dumps(self.data).encode()
class Transport:
 def open(self,req,**kw):
  if req.full_url.endswith('/api/ps'):return Response({'models':[]})
  return Response({'response':'safe','done':True,'message':{'content':'{}'}},True)
t=Transport();request.urlopen=t.open;request.build_opener=lambda *a:t
settings.local_llm_timeout_seconds=5
if kind=='foreground':LocalLLMAdapter().generate_reply('persona',name,endpoint_override='http://127.0.0.1:11434',model_override='conversation')
else:LocalPracticeLearner(endpoint='http://127.0.0.1:11434',model='coding')._json('/api/chat',{'messages':[{'content':name}]})
'''


@pytest.mark.skipif(sys.platform != 'linux', reason='Gamma Linux separate-process acceptance')
@pytest.mark.parametrize('abandon', ['none', 'waiter', 'lease'])
def test_real_separate_process_priority_and_abandoned_process_recovery(setup, tmp_path, abandon):
    transport, _ = setup
    if admission is None: pytest.fail('No cooperating process admission boundary exists')
    root = tmp_path / 'process'; root.mkdir()
    script = root / 'child.py'; script.write_text(CHILD)
    admission._coordination_root = lambda: root / 'locks'
    children = []; tasks = []
    def start(name, kind):
        child = subprocess.Popen([sys.executable, '-B', str(script), str(root), name, kind],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        children.append(child); return child
    try:
        a = start('A', 'background'); until(lambda: (root/'dispatch-A').exists())
        b = start('B', 'foreground'); until(lambda: bool(list((root/'locks').glob('*.wait-*'))))
        tasks.append(run(lambda: background('C')))
        foreground('D', 'http://127.0.0.2:11434')
        assert transport.seen('D') and not transport.seen('C') and not (root/'dispatch-B').exists()
        if abandon == 'waiter': b.kill(); b.wait(3)
        if abandon == 'lease': a.kill(); a.wait(3)
        else: (root/'release-A').touch()
        if abandon != 'waiter':
            until(lambda: (root/'dispatch-B').exists()); assert not transport.seen('C')
            (root/'release-B').touch()
        until(lambda: transport.seen('C'))
        finish(tasks); assert not tasks[0][2]
        assert not list((root/'locks').glob('*.wait-*'))
    finally:
        for name in ['A','B']: (root/('release-'+name)).touch()
        for child in children:
            try: stdout, stderr = child.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill(); stdout, stderr = child.communicate()
            assert child.returncode in (0, -9), stderr.decode()
        finish(tasks)
