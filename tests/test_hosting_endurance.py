"""No-inference scheduler, quotas, source pinning and owned-process lifecycle."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest


path = Path(__file__).resolve().parents[1] / 'scripts/verify_hosting_endurance.py'
spec = importlib.util.spec_from_file_location('hosting_endurance_probe', path)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_scheduler_keeps_real_seconds_and_never_replays_stale_events():
    rows = probe.schedule(14400)
    assert rows[-1] == {'at': 14395, 'kind': 'stop'}
    assert all(a['at'] <= b['at'] for a, b in zip(rows, rows[1:]))
    assert [row['at'] for row in rows if row['kind'] == 'work'] == [60, 1800, 4200, 7800]
    assert sum(row['kind'] == 'quiet_lull' for row in rows) == 4
    assert sum(row['kind'] == 'reconnect' for row in rows) == 4
    assert probe.claim_due(rows, 59, 1, 120) == (None, 1, [])
    item, cursor, missed = probe.claim_due(rows, 1000, 0, 120)
    assert item['at'] == 900 and all(row['at'] < 880 for row in missed)
    again, next_cursor, skipped = probe.claim_due(rows, 1000, cursor, 120)
    assert again is None and next_cursor == cursor and skipped == []
    short = probe.schedule(60)
    assert [row['at'] for row in short] == [0, 55]


@pytest.mark.parametrize('duration,real', [(14400, False), (60, True), (1, False), (14401, True), (True, False)])
def test_explicit_real_duration_gate(duration, real):
    with pytest.raises(ValueError): probe.validate_duration(duration, real)


def test_valid_short_run_can_never_claim_four_hour_acceptance():
    probe.validate_duration(60, False); probe.validate_duration(14400, True)
    report = {'events': [], 'work_outcomes': [], 'reconnects': [], 'samples': [], 'incidents': [],
              'identity_before': 'same', 'identity_after': 'same', 'real_elapsed_seconds': 14400}
    assert not probe.acceptance(report, 60)['real_four_hour_wall_clock']
    value = probe.acceptance(report, 14400)
    assert value['real_four_hour_wall_clock']
    assert not value['sustained_received_contributions']
    assert not value['all_four_candidate_artifacts_independently_verified']
    assert not value['meaningful_hosting_accepted']


def test_work_completion_without_actual_edit_and_matching_provider_evidence_is_not_accepted():
    report = {'events': [], 'work_outcomes': [], 'reconnects': [], 'samples': [], 'incidents': []}
    for name in ('metrics', 'metrics_filter', 'notes', 'notes_filter'):
        report['work_outcomes'].append({'task': name, 'independent_check': {'passed': True}})
    assert not probe.acceptance(report, 14400)['all_four_candidate_artifacts_independently_verified']
    for row in report['work_outcomes']:
        row['build_dispatch_evidence'] = {'actual_edit_attempt': True, 'actual_api_chat_dispatch_ids': ['actual-dispatch-id']}
    assert probe.acceptance(report, 14400)['all_four_candidate_artifacts_independently_verified']


def test_error_reservations_are_never_refunded_and_overlap_is_refused():
    budget = probe.DispatchBudget(semantic=2, tts=1)
    with pytest.raises(OSError):
        with budget.reserve('semantic'):
            with pytest.raises(RuntimeError):
                with budget.reserve('semantic'): pass
            raise OSError('unknown simulated transport result')
    assert budget.counts['semantic'] == 1 and budget.active == 0
    with budget.reserve('semantic'): pass
    with pytest.raises(RuntimeError):
        with budget.reserve('semantic'): pass
    assert budget.maximum_active == 1
    with budget.reserve('tts'): pass
    with pytest.raises(RuntimeError):
        with budget.reserve('tts'): pass


def test_unknown_outcome_disables_further_inference():
    budget = probe.DispatchBudget()
    with budget.reserve('semantic'): budget.unknown_outcome()
    for kind in ('semantic', 'tts'):
        with pytest.raises(RuntimeError):
            with budget.reserve(kind): pass


def test_work_dispatch_evidence_binds_exact_goal_and_input_hashes_without_prompt():
    packet = {'goal': 'Implement the admitted CLI.', 'editable_files': {'tool.py': 'raise SystemExit(1)'}}
    result = probe.work_request_evidence({'messages': [{'role': 'user', 'content': json.dumps(packet)}]})
    assert result == {'work_goal_sha256': probe.digest(packet['goal']), 'work_input_files_sha256': probe.digest(packet['editable_files'])}
    assert probe.work_request_evidence({'messages': []}) == {}
    assert probe.work_request_evidence({'messages': [{'content': 'not JSON'}]}) == {}


def repository(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    subprocess.run(['git', 'init', '-q', str(source)], check=True, capture_output=True)
    for name, text in {
        'src/gamma/__init__.py': '', 'src/gamma/example.py': 'VALUE=1\n',
        'scripts/verify_hosting_endurance.py': path.read_text(), 'pyproject.toml': '[project]\nname="test"\n',
        'config/app.toml': 'name="portable"\n', '.gitignore': 'config/secret.json\n',
        'config/secret.json': '{"private":true}', '.env': 'SECRET=hidden',
        'config/app.local.toml': 'private="hidden"', 'data/owner.sqlite3': 'private identity',
        'src/gamma/__pycache__/old.pyc': 'stale', 'node_modules/a.js': 'third party',
    }.items():
        target = source / name; target.parent.mkdir(parents=True, exist_ok=True); target.write_text(text)
    return source


def test_pinned_copy_contains_only_portable_source_and_fresh_state(tmp_path):
    source = repository(tmp_path)
    root, manifest = probe.pin_source(source, tmp_path)
    try:
        assert root.stat().st_mode & 0o077 == 0
        assert probe.verify_pin(root) == manifest
        assert (root / 'src/gamma/example.py').read_text() == 'VALUE=1\n'
        for forbidden in ('.env', 'config/app.local.toml', 'config/secret.json', 'data/owner.sqlite3', 'node_modules', '.git'):
            assert not (root / forbidden).exists()
        assert list((root / 'data/coordination/endurance').iterdir()) == []
        assert manifest['interpreter'] == str(source / '.venv/bin/python')
        (root / 'src/gamma/example.py').write_text('VALUE=2\n')
        with pytest.raises(RuntimeError, match='changed'): probe.verify_pin(root)
    finally:
        probe.safe_cleanup(root, tmp_path)
    assert source.exists() and not root.exists()


def test_source_copy_drift_is_detected_and_disposable_copy_removed(tmp_path, monkeypatch):
    source = repository(tmp_path)
    original = probe.source_hashes
    calls = 0
    def hashes(root, names):
        nonlocal calls
        calls += 1
        if calls == 3: (source / 'src/gamma/example.py').write_text('VALUE=2\n')
        return original(root, names)
    monkeypatch.setattr(probe, 'source_hashes', hashes)
    with pytest.raises(RuntimeError, match='changed while pinning'): probe.pin_source(source, tmp_path)
    assert not list(tmp_path.glob('shana-coding-endurance-*'))


def test_cleanup_cannot_escape_or_follow_dependency_symlinks(tmp_path):
    outside = tmp_path / 'keep'; outside.mkdir(); (outside / 'file').write_text('keep')
    root = tmp_path / 'shana-coding-endurance-test'; root.mkdir()
    (root / 'dependency').symlink_to(outside, target_is_directory=True)
    assert probe.tree_bytes(root) == 0
    probe.safe_cleanup(root, tmp_path)
    assert (outside / 'file').read_text() == 'keep'
    with pytest.raises(ValueError): probe.safe_cleanup(outside, tmp_path)


def test_source_and_audio_quota_measure_only_owned_regular_files(tmp_path):
    (tmp_path / 'a.json').write_bytes(b'1234')
    (tmp_path / 'a.wav').write_bytes(b'123456')
    assert probe.tree_bytes(tmp_path) == 10
    assert probe.tree_bytes(tmp_path, audio_only=True) == 6


def test_targeted_host_shutdown_closes_client_and_log_and_leaves_other_process(tmp_path):
    host = probe.PrivateHost(tmp_path, {}, SimpleNamespace())
    host.log = (tmp_path / 'log').open('w')
    log = host.log
    class Client:
        closed = False
        def close(self): self.closed = True
    client = Client(); host.client = client
    host.process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=log, close_fds=True)
    other = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    try:
        result = host.stop()
        assert result['owned_host_absent'] and not result['forced_owned_host_termination']
        assert client.closed and log.closed and other.poll() is None
    finally:
        if host.process.poll() is None: host.process.kill(); host.process.wait()
        other.terminate(); other.wait(timeout=10)


def test_fixture_contracts_are_bounded_and_extension_requires_real_base():
    from gamma.coordination.work_models import validate_spec
    assert len(probe.PLAN) <= 1000
    for name in ('metrics', 'notes'):
        value = probe.fixture(name)
        assert validate_spec(value) == value
        assert len(json.dumps(value)) < 9000
        with pytest.raises(ValueError): probe.fixture(name + '_filter')
        extension = probe.fixture(name + '_filter', value['files'])
        assert validate_spec(extension) == extension


def test_protected_cli_fixtures_fail_unimplemented_candidates(tmp_path):
    # The actual protected test module runs against each admitted stub.
    for name in ('metrics', 'notes'):
        value = probe.fixture(name)
        folder = tmp_path / name; folder.mkdir()
        for filename, text in value['files'].items(): (folder / filename).write_text(text, encoding='utf-8')
        from gamma.coding.sandbox import run_practice_check
        result = run_practice_check(folder, SimpleNamespace(**value['checks'][0]))
        assert result['passed'] is False and result['infrastructure_error'] is False, result
        assert 'AssertionError' in result['output_tail'], result
        independent = probe.independent_flow(path.parents[1], name, value['files'])
        assert independent['passed'] is False and independent['infrastructure_error'] is False, independent
        assert 'AssertionError' in independent['output_tail'], independent


@pytest.mark.parametrize('task', ['metrics', 'metrics_filter', 'notes', 'notes_filter'])
def test_authored_checks_are_satisfiable_and_independent_flow_uses_other_inputs(tmp_path, task):
    # Test-only reference code validates the checker. It is never present in
    # the runner's packets or the source-only copy, which excludes tests/.
    if task.startswith('metrics'):
        filename = 'text_metrics.py'
        reference = '''import argparse,json,pathlib
p=argparse.ArgumentParser();p.add_argument('path');p.add_argument('--min-length',type=int,default=1);a=p.parse_args()
if a.min_length<1:p.error('minimum length must be positive')
try:text=pathlib.Path(a.path).read_text(encoding='utf-8')
except OSError:p.error('cannot read input')
print(json.dumps({'characters':len(text),'lines':len(text.splitlines()),'words':sum(len(w)>=a.min_length for w in text.split())}))
'''
    else:
        filename = 'note_extract.py'
        reference = '''import argparse,json,pathlib,re
p=argparse.ArgumentParser();p.add_argument('path');p.add_argument('--pending-only',action='store_true');a=p.parse_args()
try:text=pathlib.Path(a.path).read_text(encoding='utf-8')
except OSError:p.error('cannot read input')
items=[]
for line,value in enumerate(text.splitlines(),1):
 m=re.fullmatch(r'\\s*- \\[([ xX])\\] (.+)',value)
 if m and m[2].strip() and not(a.pending_only and m[1].lower()=='x'):items.append({'line':line,'text':m[2].strip(),'done':m[1].lower()=='x'})
print(json.dumps(items))
'''
    value = probe.fixture(task, {filename: reference} if task.endswith('filter') else None)
    files = {**value['files'], filename: reference}
    for name, text in files.items(): (tmp_path / name).write_text(text, encoding='utf-8')
    from gamma.coding.sandbox import run_practice_check
    result = run_practice_check(tmp_path, SimpleNamespace(**value['checks'][0]))
    assert result['passed'] is True and 'Ran 1 test' in result['output_tail'], result
    independent = probe.independent_flow(path.parents[1], task, files)
    assert independent['passed'] and 'Ran 2 tests' in independent['output_tail'], independent
    cases = {row['case']: row for row in independent['actual_case_receipts']}
    assert cases['empty_input']['observed_output'] == ({'characters': 0, 'lines': 0, 'words': 0} if task.startswith('metrics') else [])


def test_audio_consumer_connects_and_disconnects_around_each_finite_render(tmp_path):
    report = {'audio': []}
    value = probe.Rehearsal(SimpleNamespace(), tmp_path, SimpleNamespace(with_tts=True), report)
    calls = []
    def post(path, **values):
        calls.append(path)
        return {'listen_enabled': False}
    value.post = post
    value._receive_audio = lambda output: calls.append('actual-http-render')
    value.audio({'id': 'one'}); value.audio({'id': 'two'})
    assert calls == ['/channel-audio/connect', '/channel-audio/enable', 'actual-http-render', '/channel-audio/disconnect'] * 2
    value._receive_audio = lambda output: (_ for _ in ()).throw(RuntimeError('simulated unknown result'))
    with pytest.raises(RuntimeError): value.audio({'id': 'three'})
    assert calls[-1] == '/channel-audio/disconnect'


def test_optional_audio_requires_actual_wave_and_canonical_chunk_evidence():
    report = {'audio': [], 'audio_renders': []}
    assert not probe.audio_transport_verified(report)
    report['audio_renders'] = [{'receipt': {'status': 'generated'}}]
    report['audio'] = [{'canonical_chunk_matches': True, 'tts_service_wave_match': False}]
    assert not probe.audio_transport_verified(report)
    report['audio'][0]['tts_service_wave_match'] = True
    assert probe.audio_transport_verified(report)
    report['audio_renders'][0]['receipt']['status'] = 'partial'
    assert not probe.audio_transport_verified(report)


def test_prepare_preserves_explicit_optional_transport_flags_without_starting_host(tmp_path, monkeypatch, capsys):
    target = tmp_path / 'data/coordination/endurance'; target.mkdir(parents=True)
    reference = tmp_path / 'reference.wav'; reference.write_bytes(b'RIFFtestWAVE')
    manifest = {'interpreter': sys.executable, 'source_sha256': 'prepared-source-hash'}
    monkeypatch.setattr(probe, 'pin_source', lambda source: (tmp_path, manifest))
    def forbidden(_): raise AssertionError('preparation started a runtime')
    monkeypatch.setattr(probe.PrivateHost, 'start', forbidden)
    probe.main(['--prepare', '--with-tts', '--qwen-reference', str(reference)])
    value = json.loads(capsys.readouterr().out)
    assert value['real_endurance_run_started'] is False
    assert value['ready_command'][-3:] == ['--with-tts', '--qwen-reference', str(reference)]


def test_existing_global_admission_origin_is_shared_across_processes(tmp_path):
    from gamma.llm import backend_admission
    # No HTTP/model call. Two processes contend for the real per-user lock
    # namespace using one unused synthetic endpoint identity.
    endpoint = 'http://127.0.0.1:39997/endurance-test-' + str(os.getpid())
    code = ("from gamma.llm.backend_admission import backend_slot; "
            "\ntry:\n with backend_slot(" + repr(endpoint) + ",foreground=True,timeout=.2): print('unexpected')"
            "\nexcept TimeoutError: print('shared-origin-blocked')")
    with backend_admission.backend_slot(endpoint, foreground=False, timeout=1):
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and result.stdout.strip() == 'shared-origin-blocked'
