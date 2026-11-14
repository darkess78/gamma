"""Prepare or run one finite private coding/chat rehearsal on Gamma.

The four-hour run needs --run-real-endurance. Short runs validate this driver
only. All chat/owner inputs are authored test actors. HTTP text/WAV receipts do
not establish browser playback, human hearing, OSRS play or public hosting.
Preparation and --self-test do not invoke a model or start an application.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, ExitStack
import getpass
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlsplit
import uuid


REAL_SECONDS = 14400
MAX_SEMANTIC_CALLS = 160
MAX_TTS_CALLS = 24
MAX_SAVED_BYTES = 128 * 1024 * 1024
MAX_AUDIO_BYTES = 32 * 1024 * 1024
MAX_SOURCE_BYTES = 32 * 1024 * 1024
SOURCE_SUFFIXES = {'.py', '.pyw', '.html', '.js', '.cjs', '.css', '.json', '.yaml', '.yml', '.toml', '.md'}
MARKER = 'private-coding-endurance-source-v1'
PLAN = (
    'Private automated coding/chat rehearsal with a fresh test identity. Build a disposable text-metrics CLI, '
    'verify real CLI outcomes, extend it with a minimum-word-length filter measured in Unicode characters, then build a Markdown task extractor '
    'and extend it with a pending-only filter. Discuss boundary cases and the observed work outcomes. '
    'Every CLI writes JSON. Pending means an unchecked [ ] task, done=false; [x]/[X] tasks are complete. '
    'Focused stretches should be quiet. Authored viewer/owner inputs are test fixtures. The owner may step away '
    'and return inside this finite plan. Do not invent completed work, user behavior, viewers, game actions or facts. '
    'No publication, training, production activation or Minecraft activity is authorized.'
)


def digest(value):
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


def work_request_evidence(body):
    """Bind real work dispatches to their admitted goal without saving prompts."""
    try:
        packet = json.loads(body['messages'][-1]['content'])
        if isinstance(packet['goal'], str) and isinstance(packet['editable_files'], dict):
            return {'work_goal_sha256': digest(packet['goal']),
                    'work_input_files_sha256': digest(packet['editable_files'])}
    except (KeyError, IndexError, TypeError, ValueError):
        pass
    return {}


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    os.replace(temporary, path)


def claim_due(events, elapsed, cursor, maximum_lateness):
    # Reviewed local worker proposal. Claim one event; missed work is not replayed.
    missed = []
    i = cursor
    while i < len(events):
        ev = events[i]
        if elapsed < ev['at']:
            break
        if elapsed - ev['at'] > maximum_lateness:
            missed.append(ev)
            i += 1
            continue
        return ev, i + 1, missed
    return None, i, missed


def validate_duration(duration, real):
    if type(duration) is not int or not 60 <= duration <= REAL_SECONDS:
        raise ValueError('duration must be an integer from 60 to 14400 seconds')
    if real != (duration == REAL_SECONDS):
        raise ValueError('14400 seconds requires --run-real-endurance; short runs must omit it')


def schedule(duration):
    """Absolute real elapsed seconds. Short tests omit later events; no scaling."""
    rows = []
    def add(at, kind, **values):
        if at < duration - 10:
            rows.append({'at': at, 'kind': kind, **values})
    add(0, 'foreground', text='AUTHORED TEST OWNER: Let us follow this finite coding plan. Give your concise test order and flag a concrete ambiguity before the typed tasks start. This is a private automated rehearsal.\n' + PLAN)
    add(60, 'work', task='metrics')
    add(65, 'foreground', text='AUTHORED TEST OWNER: While the admitted text-metrics worker is building, give one brief practical reason to test whitespace-only input. Keep the worker scope unchanged.')
    add(150, 'activity')
    add(180, 'chat', text='AUTHORED VIEWER: What does the actually checked text-metrics artifact do with empty input? If it is unfinished, say what is still unverified.')
    questions = [
        'Would whitespace-only input count as a line, a word, both, or neither under the selected CLI contract?',
        'What does a Unicode character count tell us, and what does it leave out?',
        'How would you explain the difference between passing a protected test and proving this CLI is useful?',
        'Should the tool silently skip a file it cannot read? Give one practical reason for your answer.',
        'For Markdown tasks, why preserve the original line number?',
        'What would you test before trusting an unchecked-task filter?',
        'Which part of the observed work evidence is still uncertain?',
        'How would you present a failed check without pretending the whole tool is useless?',
        'Which boundary case would you keep in a small regression suite?',
        'What did the actual work receipts establish, and what did they leave untested?',
        'What is one reasonable next improvement inside this tiny CLI scope?',
        'Before stopping, what should an owner review in these disposable artifacts?',
    ]
    for hour in range(4):
        base = hour * 3600
        for minute, offset in ((8, 0), (28, 1), (48, 2)):
            add(base + minute * 60, 'chat', text='AUTHORED VIEWER: ' + questions[hour * 3 + offset])
        add(base + 15 * 60, 'focus')
        add(base + 18 * 60, 'quiet_lull')
        add(base + 22 * 60, 'relax')
        add(base + 25 * 60, 'activity')
        add(base + 34 * 60, 'away')
        add(base + 38 * 60, 'lull', text='Authored lull. The test owner is away. Stay inside the admitted coding plan; quiet is welcome.')
        add(base + 41 * 60, 'return')
        add(base + 44 * 60, 'foreground', text='AUTHORED TEST OWNER: Give a brief status of the admitted coding tasks from their actual checked receipts. Distinguish unfinished or failed work.')
        add(base + 52 * 60, 'reconnect')
        add(base + 56 * 60, 'lull', text='Authored quiet stretch after discussion. Only contribute if the current coding context makes it useful.')
    add(1800, 'work', task='metrics_filter')
    add(4200, 'work', task='notes')
    add(7800, 'work', task='notes_filter')
    add(14160, 'activity')
    rows.append({'at': duration - 5, 'kind': 'stop'})
    return sorted(rows, key=lambda item: item['at'])


def selected_files(source):
    paths = []
    for folder in ('src', 'config', 'scripts'):
        for path in sorted((source / folder).rglob('*')):
            if (path.is_symlink() or not path.is_file() or '__pycache__' in path.parts
                    or path.suffix.lower() not in SOURCE_SUFFIXES or '.local.' in path.name
                    or path.name.startswith('.') or any(part in {'data', 'node_modules', 'models', '.git'} for part in path.relative_to(source).parts)):
                continue
            paths.append(path.relative_to(source).as_posix())
    paths.append('pyproject.toml')
    # Exclude ignored local material without opening it. New portable source
    # files are intentionally included even before they have a Git index entry.
    result = subprocess.run(['git', '-C', str(source), 'check-ignore', '--stdin'],
                            input='\n'.join(paths) + '\n', capture_output=True, text=True, timeout=20)
    if result.returncode not in {0, 1}:
        raise RuntimeError('cannot establish ignored-source exclusions')
    ignored = set(result.stdout.splitlines())
    return [name for name in paths if name not in ignored]


def source_hashes(root, names):
    return {name: digest((root / name).read_bytes()) for name in names}


def pin_source(source, temporary_parent=Path('/tmp')):
    source = Path(source).resolve()
    names = selected_files(source)
    if not names or sum((source / name).stat().st_size for name in names) > MAX_SOURCE_BYTES:
        raise RuntimeError('source-copy quota exceeded')
    before = source_hashes(source, names)
    root = Path(tempfile.mkdtemp(prefix='shana-coding-endurance-', dir=temporary_parent))
    root.chmod(0o700)
    try:
        for name in names:
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Refuse an intervening symlink. Do not dereference private targets.
            if (source / name).is_symlink():
                raise RuntimeError('source changed to a symlink')
            with (source / name).open('rb') as incoming, target.open('xb') as outgoing:
                shutil.copyfileobj(incoming, outgoing)
            target.chmod(0o600)
        if before != source_hashes(root, names) or before != source_hashes(source, names):
            raise RuntimeError('source changed while pinning; no runtime was started')
        (root / 'data/coordination/endurance').mkdir(parents=True, mode=0o700)
        manifest = {'marker': MARKER, 'source_origin': str(source), 'files': before,
                    'source_sha256': digest(before), 'created_at': time.time(),
                    'interpreter': str(source / '.venv/bin/python'), 'fresh_data_tree': True}
        write_json(root / 'endurance-source.json', manifest)
        return root, manifest
    except BaseException:
        safe_cleanup(root, temporary_parent)
        raise


def verify_pin(root):
    root = Path(root).resolve()
    manifest = json.loads((root / 'endurance-source.json').read_text(encoding='utf-8'))
    if manifest.get('marker') != MARKER or digest(manifest['files']) != manifest['source_sha256']:
        raise RuntimeError('invalid prepared source manifest')
    if source_hashes(root, manifest['files']) != manifest['files']:
        raise RuntimeError('pinned source changed')
    for name in ('.env', 'config/app.local.toml', 'config/voices.local.toml'):
        if (root / name).exists():
            raise RuntimeError('private overrides are forbidden in the pinned copy')
    if root.stat().st_mode & 0o077:
        raise RuntimeError('pinned copy must be private')
    return manifest


def safe_cleanup(root, temporary_parent=Path('/tmp')):
    root, parent = Path(root).absolute(), Path(temporary_parent).resolve()
    if (root.is_symlink() or root.resolve().parent != parent
            or not root.name.startswith('shana-coding-endurance-')):
        raise ValueError('refuse cleanup outside the explicit disposable directory')
    if root.exists():
        shutil.rmtree(root)  # rmtree does not traverse child symlinks.


def tree_bytes(root, *, audio_only=False):
    return sum(path.stat().st_size for path in Path(root).rglob('*') if path.is_file()
               and not path.is_symlink() and (not audio_only or path.suffix == '.wav'))


class DispatchBudget:
    """Reservation survives errors; overlap fails instead of adding a new queue."""
    def __init__(self, semantic=MAX_SEMANTIC_CALLS, tts=MAX_TTS_CALLS):
        self.limits = {'semantic': semantic, 'tts': tts}
        self.counts = {'semantic': 0, 'tts': 0}
        self.active = 0
        self.maximum_active = 0
        self.uncertain = False
        self.lock = threading.Lock()

    @contextmanager
    def reserve(self, kind):
        with self.lock:
            if self.uncertain or self.active or self.counts[kind] >= self.limits[kind]:
                raise RuntimeError('endurance_dispatch_capacity_or_budget_exhausted')
            self.counts[kind] += 1
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
        try:
            yield self.counts[kind]
        finally:
            with self.lock:
                self.active -= 1

    def unknown_outcome(self):
        with self.lock:
            self.uncertain = True


def fixture(task, previous=None):
    """Authored P3 goals/checks; all implementation bytes come from real work."""
    metrics = task.startswith('metrics')
    module = 'text_metrics' if metrics else 'note_extract'
    filename = module + '.py'
    extension = task.endswith('filter')
    if extension and previous is None:
        raise ValueError('extension requires an actually verified base artifact')
    stub = "raise SystemExit('Disposable implementation has not been built yet.')\n"
    goal = (
        'Implement a Python standard-library CLI: python text_metrics.py INPUT_PATH. Read UTF-8 text, output only a JSON object '
        'with characters=len(text), lines=len(text.splitlines()), words=len(text.split()). Empty input gives zeros. '
        'Unreadable files must exit nonzero with a concise error, no traceback. Never modify inputs.' if metrics else
        'Implement a Python standard-library CLI: python note_extract.py INPUT_PATH. Read UTF-8 Markdown and output only a JSON list '
        'of task items matching leading optional whitespace, dash, space, [ ], [x] or [X], space and nonempty task text. '
        'Each item has line, its one-based source line, text, trimmed task text, and done, a boolean. Ignore other lines. '
        'Empty input gives []. Unreadable files exit nonzero with a concise error. Never modify inputs.'
    )
    if extension:
        goal += (' Preserve all existing behavior. Add --min-length N, an integer >=1 that filters only the words count to '
                 'whitespace-delimited words having at least N Unicode characters; default1. Reject invalid values.' if metrics else
                 ' Preserve all existing behavior. Add --pending-only to return only items whose done is false.')
    samples = ('', ' alpha\n\nβeta gamma\n', 'a  bb ccc\n') if metrics else (
        '', '# Agenda\n- [ ] Review logs\n- [X] Run checks\nnoise\n  - [x] Ship nothing\n', '- [ ] \n- not a task\n')
    expectations = ([{'characters': 0, 'lines': 0, 'words': 0}, {'characters': 19, 'lines': 3, 'words': 3},
                     {'characters': 10, 'lines': 1, 'words': 3}] if metrics else
                    [[], [{'line': 2, 'text': 'Review logs', 'done': False}, {'line': 3, 'text': 'Run checks', 'done': True},
                          {'line': 5, 'text': 'Ship nothing', 'done': True}], []])
    # Compute primitive fixture counts directly so check data stays inspectable.
    if metrics:
        expectations = [{'characters': len(text), 'lines': len(text.splitlines()), 'words': len(text.split())} for text in samples]
    check = '''import json, pathlib, subprocess, sys, tempfile, unittest
class ActualCLI(unittest.TestCase):
 def test_cli(self):
  samples = SAMPLES
  expected = EXPECTATIONS
  with tempfile.TemporaryDirectory() as folder:
   root=pathlib.Path(folder)
   for i,text in enumerate(samples):
    p=root/('sample'+str(i)+'.txt');p.write_text(text,encoding='utf-8')
    r=subprocess.run([sys.executable, 'FILENAME', str(p)],capture_output=True,text=True,timeout=4)
    self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads(r.stdout),expected[i]);self.assertEqual(p.read_text(encoding='utf-8'),text)
   r=subprocess.run([sys.executable,'FILENAME',str(root/'missing.txt')],capture_output=True,text=True,timeout=4)
   self.assertNotEqual(r.returncode,0)
EXTENSION
'''.replace('SAMPLES', repr(samples)).replace('EXPECTATIONS', repr(expectations)).replace('FILENAME', filename)
    extra = ''
    if extension and metrics:
        extra = '''   p=root/'filtered.txt';p.write_text('a bb ccc βeta',encoding='utf-8')
   r=subprocess.run([sys.executable,'text_metrics.py',str(p),'--min-length','3'],capture_output=True,text=True,timeout=4)
   self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads(r.stdout),{'characters':13,'lines':1,'words':2})
   r=subprocess.run([sys.executable,'text_metrics.py',str(p),'--min-length','0'],capture_output=True,text=True,timeout=4)
   self.assertNotEqual(r.returncode,0)
'''
    elif extension:
        extra = '''   p=root/'filtered.txt';p.write_text('- [x] Done\\n- [ ] Next\\n',encoding='utf-8')
   r=subprocess.run([sys.executable,'note_extract.py',str(p),'--pending-only'],capture_output=True,text=True,timeout=4)
   self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads(r.stdout),[{'line':2,'text':'Next','done':False}])
'''
    # The feature sandbox uses runpy.run_module. Its outer __main__ module is
    # the sandbox bootstrap, so unittest.main() would discover that empty
    # module. Load this actual class explicitly and return only on success.
    check = check.replace('EXTENSION', extra) + """
if __name__ == '__main__':
 result=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(ActualCLI))
 if result.testsRun!=1 or not result.wasSuccessful(): raise SystemExit(1)
"""
    files = {filename: previous[filename] if extension else stub, 'check_endurance_cli.py': check}
    return {'mode': 'build', 'goal': goal, 'files': files, 'editable': [filename],
            'checks': [{'name': 'actual_cli_and_protected_regression', 'module': 'check_endurance_cli', 'kind': 'feature'}]}


def independent_flow(pinned, task, files):
    """A second actual CLI check inside the existing no-network P3 sandbox."""
    from gamma.coding.sandbox import run_practice_check
    metrics, extension = task.startswith('metrics'), task.endswith('filter')
    if metrics:
        command, text = 'text_metrics.py', 'one  four\n東京\n'
        expected = {'characters': len(text), 'lines': 2, 'words': 2 if extension else 3}
        options = ['--min-length', '4'] if extension else []
        if extension: expected['words'] = 1
    else:
        command, text = 'note_extract.py', '- [ ] Read receipt\n- [x] Local check\n'
        expected = [{'line': 1, 'text': 'Read receipt', 'done': False}]
        if not extension: expected.append({'line': 2, 'text': 'Local check', 'done': True})
        options = ['--pending-only'] if extension else []
    code = '''import hashlib,json,pathlib,subprocess,sys,tempfile,unittest
class IndependentCLI(unittest.TestCase):
 def test_separate_owner_flow(self):
  with tempfile.TemporaryDirectory() as folder:
   p=pathlib.Path(folder)/'new-input.txt';text=TEXT;p.write_text(text,encoding='utf-8')
   r=subprocess.run([sys.executable,COMMAND,str(p)]+OPTIONS,capture_output=True,text=True,timeout=4)
   self.assertEqual(r.returncode,0,r.stderr);actual=json.loads(r.stdout);self.assertEqual(actual,EXPECTED)
   self.assertEqual(p.read_text(encoding='utf-8'),text)
   print('ENDURANCE_CLI_PROBE '+json.dumps({'case':'new_input','input_sha256':hashlib.sha256(text.encode()).hexdigest(),'observed_output':actual}))
 def test_empty_input(self):
  with tempfile.TemporaryDirectory() as folder:
   p=pathlib.Path(folder)/'empty.txt';p.write_text('',encoding='utf-8')
   r=subprocess.run([sys.executable,COMMAND,str(p)]+OPTIONS,capture_output=True,text=True,timeout=4)
   self.assertEqual(r.returncode,0,r.stderr);actual=json.loads(r.stdout);self.assertEqual(actual,EMPTY)
   self.assertEqual(p.read_text(encoding='utf-8'),'')
   print('ENDURANCE_CLI_PROBE '+json.dumps({'case':'empty_input','input_sha256':hashlib.sha256(b'').hexdigest(),'observed_output':actual}))
'''.replace('TEXT', repr(text)).replace('COMMAND', repr(command)).replace('OPTIONS', repr(options)).replace('EXPECTED', repr(expected)).replace('EMPTY', repr({'characters': 0, 'lines': 0, 'words': 0} if metrics else []))
    code += """
if __name__ == '__main__':
 result=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(IndependentCLI))
 if result.testsRun!=2 or not result.wasSuccessful(): raise SystemExit(1)
"""
    with tempfile.TemporaryDirectory(prefix='endurance-independent-', dir='/tmp') as folder:
        workspace = Path(folder)
        for name, content in {command: files[command], 'independent_endurance_cli.py': code}.items():
            (workspace / name).write_text(content, encoding='utf-8')
        result = run_practice_check(workspace, SimpleNamespace(name='independent_new_input', module='independent_endurance_cli', kind='feature'))
    receipts = [json.loads(line.removeprefix('ENDURANCE_CLI_PROBE ')) for line in result['output_tail'].splitlines()
                if line.startswith('ENDURANCE_CLI_PROBE ')]
    valid = result['passed'] and {row['case'] for row in receipts} == {'new_input', 'empty_input'}
    return {**result, 'passed': bool(valid), 'actual_case_receipts': receipts if valid else [],
            'artifact_sha256': digest(files), 'checker_sha256': digest(code.encode()), 'different_input_from_protected_check': True}


def check_oracles(project):
    """Retain real sandbox rejection of both unfinished CLI implementations."""
    from gamma.coding.sandbox import run_practice_check
    rows = []
    for task in ('metrics', 'notes'):
        value = fixture(task)
        with tempfile.TemporaryDirectory(prefix='endurance-negative-oracle-', dir='/tmp') as folder:
            workspace = Path(folder)
            for name, text in value['files'].items(): (workspace / name).write_text(text, encoding='utf-8')
            protected = run_practice_check(workspace, SimpleNamespace(**value['checks'][0]))
        independent = independent_flow(project, task, value['files'])
        rows.append({'task': task, 'stub_source_sha256': digest(value['files']),
                     'protected_stub_result': protected, 'independent_stub_result': independent})
    valid = all(not result['passed'] and not result['infrastructure_error'] and result['exit_code'] != 0
                and 'AssertionError' in result['output_tail'] for row in rows
                for result in (row['protected_stub_result'], row['independent_stub_result']))
    report = {'kind': 'actual_no_inference_sandbox_negative_oracles', 'all_stubs_rejected': valid,
              'model_calls': 0, 'script_sha256': digest(Path(__file__).read_bytes()), 'rows': rows}
    root = project / 'data/coordination/endurance'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = root / ('negative-oracles-' + uuid.uuid4().hex + '.json')
    write_json(target, report)
    print(json.dumps({'report': str(target), 'all_stubs_rejected': valid, 'model_calls': 0}))
    if not valid: raise RuntimeError('sandbox oracle did not reject the actual unfinished stub')


def child(pinned, state, args):
    import resource
    import urllib.request
    from gamma.llm import backend_admission
    import gamma
    from gamma.config import settings
    from gamma.coordination.app import create_app
    from gamma.coordination.dashboard import create_app as create_dashboard
    from gamma.coordination.hosting import loopback_server
    if Path(gamma.__file__).resolve() != pinned / 'src/gamma/__init__.py':
        raise RuntimeError('runtime import did not resolve to pinned source')
    manifest = verify_pin(pinned)
    halt, budget, event_lock = threading.Event(), DispatchBudget(), threading.Lock()
    parent_pid, child_started = os.getppid(), time.monotonic()
    affinity = sorted(os.sched_getaffinity(0))[:2]
    os.sched_setaffinity(0, affinity)
    signal.signal(signal.SIGTERM, lambda *_: halt.set())
    signal.signal(signal.SIGINT, lambda *_: halt.set())
    signal.signal(signal.SIGHUP, lambda *_: halt.set())
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024, 16 * 1024 * 1024))
    def event(value):
        with event_lock:
            with (state / 'provider-events.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(value) + '\n')
    original = urllib.request.OpenerDirector.open
    class Response:
        def __init__(self, response, lease, observation):
            self.response, self.lease, self.observation = response, lease, observation
        def __getattr__(self, name): return getattr(self.response, name)
        def __enter__(self): self.response.__enter__(); return self
        def read(self, *values):
            raw = self.response.read(*values)
            if self.observation['kind'] == 'semantic':
                try:
                    data = json.loads(raw)
                    self.observation['provider_metrics'] = {key: data[key] for key in
                        ('total_duration', 'load_duration', 'prompt_eval_count', 'eval_count', 'done_reason') if key in data}
                except (ValueError, TypeError):
                    self.observation['invalid_provider_json'] = True
            return raw
        def __exit__(self, *values):
            try:
                return self.response.__exit__(*values)
            finally:
                if values[0] is not None:
                    self.observation['outcome'] = 'uncertain_no_replay'
                    budget.unknown_outcome()
                self.observation.update(finished_at=time.time(), elapsed_seconds=time.monotonic() - self.observation.pop('began'))
                event(self.observation)
                self.lease.__exit__(*values)
    def opened(opener, request, *values, **kwargs):
        url = request.full_url if isinstance(request, urllib.request.Request) else str(request)
        parsed = urlsplit(url)
        if parsed.hostname not in {'127.0.0.1', '::1', 'localhost'}:
            raise RuntimeError('endurance_network_scope_denied')
        if parsed.path in {'/api/tags', '/api/show', '/api/pull', '/api/create', '/api/delete', '/api/copy', '/api/push'}:
            raise RuntimeError('endurance_model_inventory_or_mutation_denied')
        kind = ('semantic' if parsed.port == 11434 and parsed.path in {'/api/chat', '/api/generate'} else
                'tts' if args.with_tts and parsed.port == 9882 and parsed.path == '/tts' else None)
        if not kind:
            return original(opener, request, *values, **kwargs)
        if tree_bytes(pinned) >= MAX_SAVED_BYTES or tree_bytes(pinned, audio_only=True) >= MAX_AUDIO_BYTES:
            raise RuntimeError('endurance_saved_byte_budget_exhausted')
        lease = ExitStack()
        # LLM callers already hold this global origin lock. Optional Qwen
        # transport also takes that existing resource lock before dispatch, so
        # this runner never overlaps its TTS with another cooperative LLM call.
        if kind == 'tts':
            lease.enter_context(backend_admission.backend_slot(args.endpoint, foreground=False, timeout=120))
        try:
            number = lease.enter_context(budget.reserve(kind))
        except BaseException:
            lease.close()
            raise
        try:
            body = json.loads(request.data) if isinstance(request, urllib.request.Request) and request.data else {}
            observation = {'id': uuid.uuid4().hex, 'kind': kind, 'number': number, 'started_at': time.time(),
                           'began': time.monotonic(), 'path': parsed.path, 'request_sha256': digest(request.data or b''),
                           'model': body.get('model'), 'active': budget.active}
            if parsed.path == '/api/chat': observation.update(work_request_evidence(body))
            event({key: value for key, value in observation.items() if key != 'began'} | {'event': 'dispatched'})
        except BaseException:
            lease.close()
            raise
        try:
            response = original(opener, request, *values, **kwargs)
            return Response(response, lease, observation)
        except BaseException as exc:
            budget.unknown_outcome()
            observation.update(error_type=type(exc).__name__, finished_at=time.time(),
                               elapsed_seconds=time.monotonic() - observation.pop('began'), outcome='uncertain_no_replay')
            event(observation)
            lease.__exit__(*sys.exc_info())
            raise
    urllib.request.OpenerDirector.open = opened
    if args.with_tts:
        from gamma.voice.tts import TTSService
        original_synthesize = TTSService.synthesize
        def observed_synthesize(service, text, *values, **kwargs):
            result = original_synthesize(service, text, *values, **kwargs)
            raw = Path(result.audio_path).read_bytes()
            event({'event': 'tts_service_output', 'id': uuid.uuid4().hex, 'kind': 'tts_service', 'at': time.time(),
                   'speech_input_sha256': digest(text.encode()), 'wave_sha256': digest(raw),
                   'bytes': len(raw), 'provider': str(result.provider)})
            return result
        TTSService.synthesize = observed_synthesize
    app = create_app()
    with loopback_server(app) as api:
        settings.shana_port = int(api.rsplit(':', 1)[1])
        os.environ['GAMMA_COORDINATION_API_ORIGIN'] = api
        with loopback_server(create_dashboard()) as dashboard:
            write_json(state / 'ready.json', {'pid': os.getpid(), 'api': api, 'dashboard': dashboard,
                'gamma_source': str(Path(gamma.__file__).resolve()), 'source_sha256': manifest['source_sha256'],
                'cpu_affinity': affinity,
                'admission_root': str(backend_admission._coordination_root()),
                'admission_resource_key': backend_admission._resource_key(args.endpoint),
                'admission_source_sha256': digest(Path(backend_admission.__file__).read_bytes())})
            while not halt.wait(.25):
                if os.getppid() != parent_pid or time.monotonic() - child_started >= args.duration + 300:
                    halt.set()
    write_json(state / 'child-closed.json', {'pid': os.getpid(), 'closed': True,
               'dispatch_counts': budget.counts, 'maximum_active_dispatches': budget.maximum_active})


class PrivateHost:
    def __init__(self, pinned, manifest, args):
        self.pinned, self.manifest, self.args = pinned, manifest, args
        self.state = pinned / 'data/coordination/endurance/state'
        self.process = self.client = self.log = None
    def start(self):
        import httpx
        self.state.mkdir(mode=0o700)
        token, password = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        keep = {'PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'LD_LIBRARY_PATH', 'CUDA_VISIBLE_DEVICES',
                'NVIDIA_VISIBLE_DEVICES', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS'}
        env = {key: value for key, value in os.environ.items() if key in keep}
        env.update(PYTHONPATH=str(self.pinned / 'src'), PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1', TMPDIR='/tmp',
            SHANA_PROJECT_ROOT=str(self.pinned), GAMMA_ISOLATED_COORDINATION='1',
            GAMMA_COORDINATION_STATE_DIR=str(self.state), SHANA_API_AUTH_ENABLED='true', SHANA_API_BEARER_TOKEN=token,
            SHANA_DASHBOARD_AUTH_ENABLED='true', SHANA_DASHBOARD_AUTH_USERNAME='endurance-test-owner',
            SHANA_DASHBOARD_AUTH_PASSWORD=password, SHANA_DASHBOARD_SESSION_SECRET=secrets.token_urlsafe(32),
            SHANA_DASHBOARD_COOKIE_SECURE='false', SHANA_LLM_PROVIDER='local', SHANA_LLM_ROUTER_ENABLED='false',
            SHANA_LOCAL_LLM_ENDPOINT=self.args.endpoint, SHANA_LOCAL_LLM_MODEL=self.args.model,
            SHANA_LOCAL_LLM_ENABLE_ROUTING='false', SHANA_LOCAL_LLM_KEEP_ALIVE='0', SHANA_LOCAL_LLM_TIMEOUT_SECONDS='120',
            GAMMA_PRACTICE_MODEL=self.args.model, SHANA_DATABASE_URL='sqlite:///' + str(self.state / 'conversation.sqlite3'),
            SHANA_AUDIO_OUTPUT_DIR=str(self.state / 'provider-audio'), SHANA_TTS_PROVIDER='qwen-tts' if self.args.with_tts else 'stub',
            SHANA_TTS_PROFILE='', SHANA_QWEN_TTS_ENDPOINT='http://127.0.0.1:9882/tts',
            SHANA_QWEN_TTS_REFERENCE_AUDIO=str(self.args.qwen_reference or ''),
            SHANA_QWEN_TTS_EXTRA_JSON='{"x_vector_only_mode":true}', SHANA_QWEN_TTS_LANGUAGE='English')
        self.log = (self.state / 'host.log').open('w', encoding='utf-8')
        command = [self.manifest['interpreter'], str(self.pinned / 'scripts/verify_hosting_endurance.py'),
                   '--child-state', str(self.state), '--model', self.args.model, '--endpoint', self.args.endpoint,
                   '--duration', str(self.args.duration)]
        if self.args.with_tts: command.append('--with-tts')
        self.process = subprocess.Popen(command, cwd=self.pinned, env=env, stdin=subprocess.DEVNULL,
                                        stdout=self.log, stderr=self.log, close_fds=True)
        deadline = time.monotonic() + 45
        while not (self.state / 'ready.json').exists():
            if self.process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('owned private host did not become ready')
            time.sleep(.1)
        self.ready = json.loads((self.state / 'ready.json').read_text(encoding='utf-8'))
        if self.ready['pid'] != self.process.pid: raise RuntimeError('owned host identity mismatch')
        self.client = httpx.Client(base_url=self.ready['dashboard'], timeout=180, trust_env=False)
        assert self.client.post('/api/coordination/stream/start', json={}).status_code == 401
        assert self.client.post('/login', data={'username': 'endurance-test-owner', 'password': password}).status_code in {200, 303}
        return self
    def stop(self):
        if self.client:
            self.client.close(); self.client = None
        forced = False
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try: self.process.wait(timeout=180)
            except subprocess.TimeoutExpired:
                forced = True; self.process.kill(); self.process.wait(timeout=15)
        if self.log:
            self.log.close(); self.log = None
        return {'owned_host_absent': self.process is not None and self.process.poll() is not None,
                'forced_owned_host_termination': forced}


class Rehearsal:
    def __init__(self, host, pinned, args, report):
        self.host, self.pinned, self.args, self.report = host, pinned, args, report
        self.consumer, self.session = 'endurance-http-test-client', None
        self.tasks, self.files, self.noticed = {}, {}, set()
        self.halt = threading.Event()
        self.heartbeat = None
        self.started = None
        self.heartbeat_errors = []
        self.consumer_active = False
    def post(self, path, **values):
        response = self.host.client.post('/api/coordination' + path, json={'audience': 'owner_private', **values})
        response.raise_for_status(); return response.json()
    def get(self, path, **values):
        response = self.host.client.get('/api/coordination' + path, params={'audience': 'owner_private', **values})
        response.raise_for_status(); return response.json()
    def keepalive(self):
        while not self.halt.wait(10):
            if self.session and self.consumer_active:
                try: self.post('/stream/heartbeat', session_id=self.session, consumer_id=self.consumer)
                except Exception as exc:
                    if self.consumer_active:
                        self.heartbeat_errors.append({'at': time.time(), 'type': type(exc).__name__})
    def scope(self): return {'session_id': self.session, 'consumer_id': self.consumer}
    def control(self, action):
        if action == 'stop': self.consumer_active = False
        result = self.post('/stream/control', **{'session_id': self.session}, request_id=uuid.uuid4().hex, action=action)
        self.report['controls'].append({'at': time.time(), 'action': action, 'result': result})
        return result
    def foreground(self, text):
        path = self.host.state / 'provider-events.jsonl'
        before_rows = [json.loads(row) for row in path.read_text().splitlines()] if path.exists() else []
        finished = {row['id'] for row in before_rows if row.get('event') != 'dispatched'}
        active = [row['id'] for row in before_rows if row.get('event') == 'dispatched'
                  and row.get('path') == '/api/chat' and row['id'] not in finished]
        requested_at, began = time.time(), time.monotonic()
        value = self.host.client.post('/api/monitor/input', json={'audience': 'owner_private', 'request_id': uuid.uuid4().hex,
            'input_mode': 'private_text', 'session_id': 'authored-endurance-owner', 'text': text})
        value.raise_for_status()
        actual = value.json()['result']['assistant_response']
        self.report['foreground'].append({'at': time.time(), 'request_started_at': requested_at,
            'response_received_at': time.time(), 'elapsed_seconds': time.monotonic() - began,
            'actual_work_dispatches_in_flight_at_start': active, 'authored_text': text, 'response': actual})
    def facts(self):
        results = []
        for name, task_id in self.tasks.items():
            detail = self.get('/work', task_id=task_id)
            run, checkpoint = detail.get('run') or {}, detail.get('checkpoint') or {}
            results.append({'task': name, 'status': run.get('status'),
                            'reason': run.get('reason'), 'attempts': checkpoint.get('attempt_count'),
                            'source_sha256': checkpoint.get('source_sha256'),
                            'protected_checks_passed': sum(row.get('passed') is True for row in checkpoint.get('checks', [])),
                            'protected_checks_total': len(checkpoint.get('checks', []))})
            if run.get('status') == 'completed' and name not in self.noticed:
                artifact = self.get('/work/artifact', task_id=task_id)
                files = artifact['files']
                receipt = independent_flow(self.pinned, name, files)
                self.report['work_outcomes'].append({'task': name, 'task_id': task_id,
                    'source_sha256': digest(files), 'checkpoint': checkpoint, 'independent_check': receipt,
                    'files': files, 'observed_at': time.time()})
                edits = checkpoint.get('attempt_count', 0) > 0 and bool(checkpoint.get('attempts'))
                provider_path = self.host.state / 'provider-events.jsonl'
                provider_rows = [json.loads(row) for row in provider_path.read_text().splitlines()] if provider_path.exists() else []
                finished_ids = {row['id'] for row in provider_rows if row.get('event') != 'dispatched' and not row.get('error_type')}
                work_calls = [row for row in provider_rows if row.get('event') == 'dispatched' and row.get('path') == '/api/chat'
                              and row.get('work_goal_sha256') == digest(checkpoint['spec']['goal'])
                              and row['id'] in finished_ids and run.get('created_at', 0) <= row['started_at'] <= time.time()]
                evidence = {'actual_edit_attempt': edits, 'actual_api_chat_dispatch_ids': [row['id'] for row in work_calls]}
                self.report['work_outcomes'][-1]['build_dispatch_evidence'] = evidence
                if receipt.get('passed') is True and edits and work_calls:
                    self.files[name] = files
                else:
                    self.report['incidents'].append({'at': time.time(), 'reason': 'build_missing_actual_edit_or_dispatch_evidence', 'task': name})
                self.noticed.add(name)
            outcome = next((row for row in reversed(self.report['work_outcomes']) if row['task'] == name), None)
            if outcome and name in self.files:
                results[-1]['independent_probes'] = [{key: row[key] for key in ('case', 'observed_output')}
                    for row in outcome['independent_check']['actual_case_receipts']]
        return results
    def work(self, name):
        base = {'metrics_filter': 'metrics', 'notes_filter': 'notes'}.get(name)
        if base and base not in self.files:
            self.report['incidents'].append({'at': time.time(), 'reason': 'extension_base_not_verified', 'task': name})
            return
        value = self.post('/work', request_id=uuid.uuid4().hex, action='prepare', spec=fixture(name, self.files.get(base)),
                          maximum_attempts=3, budget_seconds=900)
        task_id = value['activity']['id']; self.tasks[name] = task_id
        self.post('/work', request_id=uuid.uuid4().hex, action='start', task_id=task_id)
        self.report['work_starts'].append({'task': name, 'task_id': task_id, 'at': time.time()})
    def audio(self, output):
        # Optional real transport, never playback or human-hearing evidence.
        if not self.args.with_tts or len(self.report['audio']) >= 8:
            return
        # P5 and stream consumers have separate leases. Connect only when this
        # explicit HTTP consumer is about to receive one finite utterance.
        connected = self.post('/channel-audio/connect', consumer_id=self.consumer)
        assert connected['listen_enabled'] is False
        self.post('/channel-audio/enable', consumer_id=self.consumer, enabled=True)
        try:
            self._receive_audio(output)
        finally:
            self.post('/channel-audio/disconnect', consumer_id=self.consumer)

    def _receive_audio(self, output):
        from gamma.coordination.audio_policy import speech_chunks
        expected = speech_chunks(output['text'])
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.post, '/channel-audio/render', stream_output_id=output['id'], consumer_id=self.consumer)
            received, deadline = set(), time.monotonic() + 180
            while time.monotonic() < deadline:
                item = self.get('/interaction/poll', consumer_id=self.consumer)['output']
                if item and item['id'] not in received:
                    params = {'audience': 'owner_private', 'consumer_id': self.consumer,
                              'generation': item['generation'], 'output_epoch': item['output_epoch']}
                    response = self.host.client.get('/api/coordination/interaction/audio/' + item['id'], params=params)
                    response.raise_for_status(); raw = response.content
                    from gamma.coordination.interaction import validate_wave
                    seconds = validate_wave(raw, maximum_seconds=90)
                    matches = (item['chunk_count'] == len(expected) and 0 <= item['chunk_index'] < len(expected)
                               and item['speech_text'] == expected[item['chunk_index']])
                    if not matches: raise RuntimeError('received audio chunk text does not match canonical checked text')
                    observations = [json.loads(row) for row in (self.host.state / 'provider-events.jsonl').read_text().splitlines()]
                    service_matches = [row for row in observations if row.get('event') == 'tts_service_output'
                        and row.get('speech_input_sha256') == digest(item['speech_text'].encode()) and row.get('wave_sha256') == digest(raw)]
                    if not service_matches: raise RuntimeError('received WAV has no matching actual TTS service result')
                    if tree_bytes(self.pinned, audio_only=True) + len(raw) > MAX_AUDIO_BYTES:
                        raise RuntimeError('audio receipt quota exceeded')
                    path = self.pinned / 'data/coordination/endurance' / (item['id'] + '.wav')
                    path.write_bytes(raw)
                    self.report['audio'].append({'at': time.time(), 'output_id': item['id'], 'bytes': len(raw),
                        'stream_output_id': output['id'], 'chunk_index': item['chunk_index'], 'chunk_count': item['chunk_count'],
                        'canonical_text_sha256': digest(output['text'].encode()), 'canonical_chunk_matches': matches,
                        'tts_service_wave_match': True, 'tts_service_receipt_ids': [row['id'] for row in service_matches],
                        'seconds': seconds, 'sha256': digest(raw), 'text': item['speech_text'], 'receipt': 'HTTP WAV bytes received'})
                    self.post('/interaction/ack', consumer_id=self.consumer, generation=item['generation'],
                              output_epoch=item['output_epoch'], output_id=item['id'], status='delivered')
                    received.add(item['id'])
                if future.done():
                    result = future.result()
                    if received == set(result.get('output_ids', [])): break
                if self.halt.wait(.1): break
            self.report['audio_renders'].append({'stream_output_id': output['id'], 'receipt': future.result(timeout=180)})
    def event(self, kind, text='', expected_quiet=False):
        if kind == 'activity':
            facts = self.facts()
            text = 'Most recent two actual disposable work receipts, including executed independent CLI probes: ' + json.dumps(facts[-2:], sort_keys=True, separators=(',', ':'))
        started = time.monotonic()
        result = self.post('/stream/event', **self.scope(), event_id=uuid.uuid4().hex, kind=kind,
            source='replay', observed_at=time.time(), text=text[:1200], actor_alias='AuthoredTestViewer')
        row = {'at': time.time(), 'elapsed': time.monotonic() - self.started, 'kind': kind,
               'authored_text_or_actual_work_facts': text[:1200], 'result': result,
               'duration_seconds': time.monotonic() - started}
        self.report['events'].append(row)
        if expected_quiet and result.get('reason') != 'focused_quiet':
            self.report['incidents'].append({'at': time.time(), 'reason': 'focused_lull_was_not_mechanically_quiet'})
        if result.get('status') == 'ready':
            output = self.get('/stream/next', **self.scope())['output']
            if not output or output['id'] != result['output_id']:
                raise RuntimeError('uncertain reserved contribution; no replay')
            if output['publication_authorized'] or output['audience'] != 'owner_private':
                raise RuntimeError('output escaped private audience')
            row['canonical_text_received'] = output['text']
            row['text_sha256'] = digest(output['text'].encode())
            row['receipt'] = self.post('/stream/ack', **self.scope(), output_id=output['id'], generation=output['generation'],
                revision=output['revision'], status='delivered', receipt_id=uuid.uuid4().hex)
            self.audio(output)
    def execute(self, item):
        kind = item['kind']
        if kind == 'work': self.work(item['task'])
        elif kind == 'foreground': self.foreground(item['text'])
        elif kind in {'chat', 'activity', 'lull'}: self.event(kind, item.get('text', ''))
        elif kind == 'quiet_lull': self.event('lull', expected_quiet=True)
        elif kind in {'focus', 'relax'}:
            self.post('/control', request_id=uuid.uuid4().hex, action='focus', focus='focused' if kind == 'focus' else 'relaxed')
            self.control(kind)
        elif kind in {'away', 'return'}: self.control('owner_' + kind)
        elif kind == 'reconnect':
            before = self.get('/personal')['identity_id']
            self.consumer_active = False
            self.post('/stream/disconnect', **self.scope()); self.post('/stream/connect', **self.scope())
            self.consumer_active = True
            pending = self.get('/stream/next', **self.scope())['output']
            after = self.get('/personal')['identity_id']
            self.report['reconnects'].append({'at': time.time(), 'same_identity': before == after, 'no_output_replay': pending is None})
            if pending is not None: raise RuntimeError('reconnect unexpectedly replayed output')
        elif kind == 'stop':
            self.control('stop'); self.report['explicit_owner_stop'] = True
        else: raise ValueError('unknown authored schedule event')
    def sample(self):
        import psutil
        process = psutil.Process(self.host.process.pid)
        memory = psutil.virtual_memory()
        value = {'at': time.time(), 'elapsed': time.monotonic() - self.started,
                 'host_rss': process.memory_info().rss, 'host_cpu_seconds': sum(process.cpu_times()[:2]),
                 'host_fds': process.num_fds(), 'host_threads': process.num_threads(),
                 'system_available_memory': memory.available, 'saved_bytes': tree_bytes(self.pinned),
                 'audio_bytes': tree_bytes(self.pinned, audio_only=True),
                 'tmp_free_bytes': shutil.disk_usage(self.pinned).free,
                 'source_stable': source_hashes(self.pinned, self.host.manifest['files']) == self.host.manifest['files']}
        self.report['samples'].append(value)
        if (not value['source_stable'] or value['saved_bytes'] > MAX_SAVED_BYTES
                or value['audio_bytes'] > MAX_AUDIO_BYTES or value['host_rss'] > 2 * 1024**3
                or value['tmp_free_bytes'] < 512 * 1024**2 or memory.available < 512 * 1024**2):
            raise RuntimeError('source or runtime resource admission failed')
    def run(self):
        self.report['identity_before'] = self.get('/personal')['identity_id']
        plan = self.post('/stream/start', request_id=uuid.uuid4().hex, format='coding', broad_plan=PLAN,
            duration_seconds=self.args.duration, maximum_decisions=96, minimum_gap_seconds=8,
            maximum_speech_seconds_per_minute=20)
        self.session = plan['id']; self.report['session_id'] = self.session
        self.post('/stream/connect', **self.scope())
        self.consumer_active = True
        self.started = time.monotonic(); self.report['wall_clock_started_at'] = time.time()
        self.heartbeat = threading.Thread(target=self.keepalive, name='endurance-heartbeat')
        self.heartbeat.start()
        rows, cursor, next_sample = schedule(self.args.duration), 0, 0
        while not self.halt.is_set():
            elapsed = time.monotonic() - self.started
            if elapsed >= self.args.duration: break
            if elapsed >= next_sample:
                self.sample(); self.facts(); next_sample = elapsed + 60
                write_json(self.pinned / 'data/coordination/endurance/report.json', self.report)
            item, cursor, missed = claim_due(rows, elapsed, cursor, 120)
            for late in missed:
                self.report['missed_schedule'].append(late | {'observed_elapsed': elapsed})
            if item:
                self.execute(item)
                write_json(self.pinned / 'data/coordination/endurance/report.json', self.report)
            else:
                self.halt.wait(.5)
        if not self.report.get('explicit_owner_stop'):
            self.control('stop'); self.report['explicit_owner_stop'] = True
        self.facts()
        self.report['identity_after'] = self.get('/personal')['identity_id']
        self.report['no_active_session_after_stop'] = self.get('/stream/status')['active'] is None
        self.report['real_elapsed_seconds'] = time.monotonic() - self.started
    def close(self):
        self.consumer_active = False
        self.halt.set()
        if self.heartbeat: self.heartbeat.join(timeout=15)
        if self.session:
            try: self.control('stop')
            except Exception: pass
            if self.args.with_tts:
                try: self.post('/channel-audio/disconnect', consumer_id=self.consumer)
                except Exception: pass
            try: self.post('/stream/disconnect', **self.scope())
            except Exception: pass
        for task_id in self.tasks.values():
            try:
                self.post('/control', request_id=uuid.uuid4().hex, action='stop', run_id=task_id)
            except Exception: pass
        self.report['heartbeat_errors'] = self.heartbeat_errors


def acceptance(report, duration):
    received = [row for row in report['events'] if row.get('canonical_text_received')]
    quarters = [sum(hour * 3600 <= row['elapsed'] < (hour + 1) * 3600 for row in received) for hour in range(4)]
    verified = {row['task'] for row in report['work_outcomes'] if row['independent_check'].get('passed') is True
                and row.get('build_dispatch_evidence', {}).get('actual_edit_attempt') is True
                and row.get('build_dispatch_evidence', {}).get('actual_api_chat_dispatch_ids')}
    return {'real_four_hour_wall_clock': duration == REAL_SECONDS and report.get('real_elapsed_seconds', 0) >= REAL_SECONDS,
        'received_contributions': len(received), 'received_contributions_per_hour': quarters,
        'all_four_candidate_artifacts_independently_verified': verified == {'metrics', 'metrics_filter', 'notes', 'notes_filter'},
        'sustained_received_contributions': all(count >= 2 for count in quarters),
        'identity_preserved': report.get('identity_before') == report.get('identity_after') and bool(report.get('identity_before')),
        'owner_stop_observed': report.get('explicit_owner_stop') is True and report.get('no_active_session_after_stop') is True,
        'reconnects_without_replay': len(report['reconnects']) >= 2 and all(row['same_identity'] and row['no_output_replay'] for row in report['reconnects']),
        'source_stable': bool(report['samples']) and all(row['source_stable'] for row in report['samples']),
        'no_recorded_incidents': not report['incidents'] and not report.get('heartbeat_errors'),
        'no_missed_required_events': not [row for row in report.get('missed_schedule', []) if row['kind'] not in {'lull', 'activity'}],
        'meaningful_hosting_accepted': False, 'semantic_review': 'pending parent review of actual contributions and artifacts'}


def audio_transport_verified(report):
    return (bool(report['audio']) and bool(report['audio_renders'])
            and all(row.get('canonical_chunk_matches') and row.get('tts_service_wave_match') for row in report['audio'])
            and all(row['receipt'].get('status') == 'generated' for row in report['audio_renders']))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--prepared-root', type=Path)
    parser.add_argument('--run-real-endurance', action='store_true')
    parser.add_argument('--duration', type=int, default=REAL_SECONDS)
    parser.add_argument('--model', default='qwen3.8:27b')
    parser.add_argument('--endpoint', default='http://127.0.0.1:11434')
    parser.add_argument('--with-tts', action='store_true')
    parser.add_argument('--qwen-reference', type=Path)
    parser.add_argument('--child-state', type=Path)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--check-oracles', action='store_true')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.self_test:
        assert claim_due([{'at': 100}], 99, 0, 30) == (None, 0, [])
        assert len(schedule(60)) == 2 and schedule(60)[-1]['kind'] == 'stop'
        print(json.dumps({'self_test': True, 'real_endurance_run': False, 'model_calls': 0}))
        return
    project = Path(__file__).resolve().parents[1]
    if os.name != 'posix' or project.name in {'gamma-main', 'gamma-modular-phase1'}:
        raise RuntimeError('run only on the isolated Gamma candidate or its prepared copy')
    if args.check_oracles:
        check_oracles(project)
        return
    if args.endpoint != 'http://127.0.0.1:11434' or args.model != 'qwen3.8:27b':
        raise RuntimeError('only the already configured existing local primary endpoint/model is admitted')
    if args.child_state:
        verify_pin(project)
        child(project, args.child_state, args)
        return
    if not args.prepare:
        validate_duration(args.duration, args.run_real_endurance)
    if args.with_tts and (not args.qwen_reference or not args.qwen_reference.is_absolute()
                          or not args.qwen_reference.is_file() or args.qwen_reference.is_symlink()
                          or args.qwen_reference.stat().st_size > 8 * 1024**2):
        raise RuntimeError('optional Qwen speech requires an explicit existing readonly reference WAV, no asset copy')
    if args.prepared_root:
        pinned = args.prepared_root.resolve(); manifest = verify_pin(pinned)
    else:
        pinned, manifest = pin_source(project)
    plan = {'marker': MARKER, 'format': 'coding', 'broad_plan': PLAN, 'duration_seconds': args.duration,
            'authored_schedule': schedule(args.duration), 'semantic_call_limit': MAX_SEMANTIC_CALLS,
            'tts_call_limit': MAX_TTS_CALLS, 'saved_bytes_limit': MAX_SAVED_BYTES, 'audio_bytes_limit': MAX_AUDIO_BYTES,
            'source_sha256': manifest['source_sha256'], 'prepared_root': str(pinned),
            'real_endurance_run_started': False, 'meaningful_hosting_accepted': False,
            'no_browser_or_human_hearing': True,
            'ready_command': [manifest['interpreter'], str(pinned / 'scripts/verify_hosting_endurance.py'),
                              '--prepared-root', str(pinned), '--run-real-endurance', '--duration', '14400']}
    if args.with_tts:
        plan['ready_command'].extend(['--with-tts', '--qwen-reference', str(args.qwen_reference)])
    plan['optional_qwen_transport'] = args.with_tts
    write_json(pinned / 'data/coordination/endurance/plan.json', plan)
    if args.prepare:
        print(json.dumps({'prepared_root': str(pinned), 'source_sha256': manifest['source_sha256'],
            'plan': str(pinned / 'data/coordination/endurance/plan.json'), 'scheduled_events': len(plan['authored_schedule']),
            'real_endurance_run_started': False, 'ready_command': plan['ready_command']}))
        return
    # The controller also imports only pinned source for independent checks.
    sys.path.insert(0, str(pinned / 'src'))
    sys.dont_write_bytecode = True
    os.environ.update(SHANA_PROJECT_ROOT=str(pinned), PYTHONDONTWRITEBYTECODE='1', TMPDIR='/tmp')
    report = {'kind': 'real_four_hour_private_coding_rehearsal' if args.run_real_endurance else 'short_driver_validation_only',
        'author': getpass.getuser() + '@' + socket.gethostname(), 'complete': False, 'meaningful_hosting_accepted': False,
        'source_sha256': manifest['source_sha256'], 'prepared_root': str(pinned), 'duration_requested': args.duration,
        'authored_test_actors': True, 'browser_control': False, 'human_hearing': False, 'public_output': False,
        'production_activated': False, 'minecraft_activity': False, 'model_downloads': False,
        'events': [], 'foreground': [], 'controls': [], 'reconnects': [], 'work_starts': [], 'work_outcomes': [],
        'samples': [], 'audio': [], 'audio_renders': [], 'incidents': [], 'missed_schedule': []}
    if args.with_tts:
        report['reference_audio_sha256'] = digest(args.qwen_reference.read_bytes())
    host = PrivateHost(pinned, manifest, args)
    rehearsal = None
    try:
        host.start(); report['host_ready'] = host.ready
        print(json.dumps({'running_root': str(pinned), 'report': str(pinned / 'data/coordination/endurance/report.json'),
                          'owned_host_pid': host.process.pid, 'requested_seconds': args.duration}), flush=True)
        rehearsal = Rehearsal(host, pinned, args, report)
        def interrupted(*_):
            report['operator_interrupted'] = True
            rehearsal.halt.set()
        signal.signal(signal.SIGTERM, interrupted); signal.signal(signal.SIGINT, interrupted); signal.signal(signal.SIGHUP, interrupted)
        rehearsal.run()
    except BaseException as exc:
        report['incidents'].append({'at': time.time(), 'reason': 'driver_stopped', 'type': type(exc).__name__})
        raise
    finally:
        if rehearsal: rehearsal.close()
        report.update(host.stop())
        events = host.state / 'provider-events.jsonl'
        report['provider_events'] = [json.loads(row) for row in events.read_text().splitlines()] if events.exists() else []
        dispatched = [row for row in report['provider_events'] if row.get('event') == 'dispatched']
        finished = {row['id'] for row in report['provider_events'] if row.get('event') != 'dispatched'}
        report['dispatch_summary'] = {
            'semantic_calls': sum(row['kind'] == 'semantic' for row in dispatched),
            'tts_calls': sum(row['kind'] == 'tts' for row in dispatched),
            'maximum_own_dispatches_in_flight': max([row['active'] for row in dispatched] or [0]),
            'all_dispatched_calls_have_terminal_observations': all(row['id'] in finished for row in dispatched),
            'unknown_outcomes': sum(row.get('outcome') == 'uncertain_no_replay' for row in report['provider_events'])}
        report['source_stable_after_shutdown'] = source_hashes(pinned, manifest['files']) == manifest['files']
        report['saved_bytes'] = tree_bytes(pinned)
        report['criteria'] = acceptance(report, args.duration)
        report['actual_optional_audio_transport_verified'] = audio_transport_verified(report)
        # Mechanical evidence never grades its own conversational quality.
        report['resource_and_dispatch_limits_respected'] = (
            report['dispatch_summary']['semantic_calls'] <= MAX_SEMANTIC_CALLS
            and report['dispatch_summary']['tts_calls'] <= MAX_TTS_CALLS
            and report['dispatch_summary']['maximum_own_dispatches_in_flight'] <= 1
            and report['dispatch_summary']['all_dispatched_calls_have_terminal_observations']
            and report['dispatch_summary']['unknown_outcomes'] == 0
            and report['saved_bytes'] <= MAX_SAVED_BYTES)
        report['technical_four_hour_criteria_passed'] = all(report['criteria'][key] for key in (
            'real_four_hour_wall_clock', 'all_four_candidate_artifacts_independently_verified',
            'sustained_received_contributions', 'identity_preserved', 'owner_stop_observed',
            'reconnects_without_replay', 'source_stable', 'no_recorded_incidents', 'no_missed_required_events')) \
            and report['resource_and_dispatch_limits_respected'] and report['owned_host_absent'] \
            and report['source_stable_after_shutdown'] \
            and (not args.with_tts or report['actual_optional_audio_transport_verified'])
        report['short_driver_validation_passed'] = (args.duration < REAL_SECONDS and report['owned_host_absent']
            and report.get('real_elapsed_seconds', 0) >= args.duration and not report.get('operator_interrupted')
            and report['source_stable_after_shutdown'] and report['resource_and_dispatch_limits_respected']
            and report['criteria']['identity_preserved'] and report['criteria']['owner_stop_observed']
            and report['criteria']['received_contributions'] >= 1
            and (not args.with_tts or report['actual_optional_audio_transport_verified'])
            and any(row['independent_check'].get('passed') is True
                    and row.get('build_dispatch_evidence', {}).get('actual_edit_attempt') is True
                    and row.get('build_dispatch_evidence', {}).get('actual_api_chat_dispatch_ids') for row in report['work_outcomes'])
            and not report['incidents'])
        write_json(pinned / 'data/coordination/endurance/report.json', report)
        print(json.dumps({'report': str(pinned / 'data/coordination/endurance/report.json'),
                          'technical_four_hour_criteria_passed': report['technical_four_hour_criteria_passed'],
                          'meaningful_hosting_accepted': False, 'owned_host_absent': report['owned_host_absent']}))


if __name__ == '__main__':
    main()
