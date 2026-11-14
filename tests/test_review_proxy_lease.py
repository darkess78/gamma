"""Disposable files and fake commands only; no Nginx/service/privileged calls."""
import base64
from email.message import Message
import importlib.util
import json
import io
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import wave

import pytest

SCRIPT = Path(__file__).parents[1] / 'scripts/review_proxy_lease.py'
spec = importlib.util.spec_from_file_location('review_proxy_lease', SCRIPT)
lease = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lease)


class Clock:
    def __init__(self): self.now = 0
    def __call__(self): return self.now
    def sleep(self, seconds): self.now += seconds


def prepared(root):
    root.mkdir(exist_ok=True)
    entries, items = [], []
    marker = 'a' * 32
    for name in ['one.conf', 'two.conf']:
        path = root / name
        before = b'server {\n    server_name gamma.neety.me;\n    proxy_set_header X-Forwarded-Proto https;\n    proxy_set_header X-Forwarded-Port 443;\n}\n'
        block = lease.expected_block(before, marker)
        after = before.replace(b'server_name gamma.neety.me;', b'server_name gamma.neety.me;' + block)
        path.write_bytes(before)
        a, b = root / (name + '.before'), root / (name + '.after')
        a.write_bytes(before); b.write_bytes(after)
        items.append({'live_path': str(path), 'before_path': str(a), 'after_path': str(b),
            'before_sha256': lease.sha(before), 'after_sha256': lease.sha(after), 'added_block_sha256': lease.sha(block), 'marker': marker})
    packet = root / 'packet.json'
    packet.write_text(json.dumps({'prefix': lease.PREFIX, 'private_dashboard_port': lease.PORT, 'files': items}))
    entries = lease.load_packet(packet, live_paths=tuple(Path(i['live_path']) for i in items))
    return entries, packet


def controlled(tmp_path, **changes):
    entries, packet = prepared(tmp_path)
    clock, calls = Clock(), []
    defaults = dict(probe=lambda: calls.append('probe'), staged_preflight=lambda: lambda _installed: None,
        syntax=lambda: calls.append('syntax'),
        reload=lambda: calls.append('reload'), clock=clock, sleep=clock.sleep, ttl=5)
    defaults.update(changes)
    return lease.LeaseController(entries, tmp_path / 'journal.json', **defaults), entries, clock, calls


def test_real_disposable_files_expire_and_reload_only_after_each_fresh_syntax_check(tmp_path):
    controller, entries, clock, calls = controlled(tmp_path)
    assert controller.run()
    assert clock.now == 5 and controller.record['end_reason'] == 'lease_expired'
    assert [c for c in calls if c != 'probe'] == ['syntax', 'reload', 'syntax', 'reload']
    assert all(e['path'].read_bytes() == e['before'] for e in entries)
    assert controller.record['phase'] == 'cleaned'


@pytest.mark.parametrize('ttl', [0, 1801, True])
def test_ttl_cannot_exceed_finite_limit(tmp_path, ttl):
    with pytest.raises(ValueError): controlled(tmp_path, ttl=ttl)


def test_failed_authenticated_preflight_never_installs_or_reloads(tmp_path):
    def failed(): raise ValueError('ordinary_401_is_not_readiness')
    controller, entries, _, calls = controlled(tmp_path, probe=failed)
    assert not controller.run() and calls == []
    assert all(e['path'].read_bytes() == e['before'] for e in entries)


@pytest.mark.parametrize('point', ['after_first_install', 'syntax', 'reload'])
def test_partial_install_syntax_and_reload_failures_remove_only_owned_additions(tmp_path, point):
    controller, entries, _, calls = controlled(tmp_path)
    counts = {'write': 0, 'syntax': 0, 'reload': 0}
    def writer(path, before, after):
        lease.atomic_cas(path, before, after); counts['write'] += 1
        if point == 'after_first_install' and counts['write'] == 1: raise OSError('injected_after_rename')
    def syntax():
        counts['syntax'] += 1; calls.append('syntax')
        if point == 'syntax' and counts['syntax'] == 1: raise RuntimeError('injected_syntax_failure')
    def reload():
        calls.append('reload'); counts['reload'] += 1
        assert calls[-2] == 'syntax'
        if point == 'reload' and counts['reload'] == 1: raise RuntimeError('injected_reload_failure')
    controller.writer, controller.syntax, controller.reload = writer, syntax, reload
    assert not controller.run()
    assert controller.record['phase'] == 'cleaned'
    assert all(e['path'].read_bytes() == e['before'] for e in entries)
    assert counts['reload'] == (2 if point == 'reload' else 1)


def test_cleanup_preserves_unrelated_live_edits_and_never_restores_whole_old_files(tmp_path):
    controller, entries, clock, _ = controlled(tmp_path)
    edited = False
    def sleep(seconds):
        nonlocal edited
        if not edited:
            entries[0]['path'].write_bytes(entries[0]['path'].read_bytes() + b'# unrelated edit during review\n')
            edited = True
        clock.sleep(seconds)
    controller.sleep = sleep
    assert controller.run()
    assert entries[0]['path'].read_bytes() == entries[0]['before'] + b'# unrelated edit during review\n'


def test_cleanup_conflict_is_retained_and_no_blind_restore_or_reload_is_attempted(tmp_path):
    controller, entries, clock, calls = controlled(tmp_path)
    def sleep(seconds):
        if controller.record['phase'] == 'active':
            path = entries[0]['path']
            path.write_bytes(path.read_bytes().replace(b'client_max_body_size 4m;', b'client_max_body_size 3m;'))
        clock.sleep(seconds)
    controller.sleep = sleep
    assert not controller.run()
    assert controller.record['phase'] == 'cleanup_failed' and controller.record['cleanup_attempts'] == 3
    assert b'client_max_body_size 3m;' in entries[0]['path'].read_bytes()
    assert calls.count('reload') == 1  # Initial apply only; conflicting cleanup is not reloaded.


def test_external_invalid_nginx_configuration_prevents_all_cleanup_reloads(tmp_path):
    controller, entries, _, calls = controlled(tmp_path)
    count = 0
    def syntax():
        nonlocal count
        count += 1; calls.append('syntax')
        if count > 1: raise RuntimeError('unrelated_invalid_nginx_edit')
    controller.syntax = syntax
    assert not controller.run() and controller.record['phase'] == 'cleanup_failed'
    assert calls.count('reload') == 1
    assert all(e['path'].read_bytes() == e['before'] for e in entries)


def test_original_compare_and_atomic_compare_fail_without_erasing_intervening_edits(tmp_path):
    controller, entries, _, calls = controlled(tmp_path)
    entries[1]['path'].write_bytes(entries[1]['before'] + b'# intervening owner edit\n')
    assert not controller.run() and 'reload' not in calls
    assert b'intervening owner edit' in entries[1]['path'].read_bytes()
    with pytest.raises(ValueError): lease.atomic_cas(entries[1]['path'], entries[1]['before'], b'wrong')
    assert b'intervening owner edit' in entries[1]['path'].read_bytes()


def test_second_install_compare_failure_preserves_unrelated_edit_and_rolls_back_first(tmp_path):
    controller, entries, _, calls = controlled(tmp_path)
    writes = 0
    def writer(path, expected, new):
        nonlocal writes
        lease.atomic_cas(path, expected, new)
        writes += 1
        if writes == 1:
            entries[1]['path'].write_bytes(entries[1]['before'] + b'# concurrent second-file edit\n')
    controller.writer = writer
    assert not controller.run() and controller.record['phase'] == 'cleaned'
    assert entries[0]['path'].read_bytes() == entries[0]['before']
    assert entries[1]['path'].read_bytes() == entries[1]['before'] + b'# concurrent second-file edit\n'
    assert calls.count('reload') == 1


def test_packet_rejects_arbitrary_extra_directive_or_wrong_live_destination(tmp_path):
    entries, packet = prepared(tmp_path)
    value = json.loads(packet.read_text())
    after = Path(value['files'][0]['after_path'])
    after.write_bytes(after.read_bytes() + b'include /unreviewed.conf;\n')
    value['files'][0]['after_sha256'] = lease.sha(after.read_bytes())
    packet.write_text(json.dumps(value))
    with pytest.raises(ValueError): lease.load_packet(packet, live_paths=tuple(e['path'] for e in entries))
    with pytest.raises(ValueError): lease.load_packet(packet)  # CLI's fixed privileged targets differ.


def test_strict_4m_envelope_accepts_existing_stereo_audio_size_and_supersedes_2m_packets(tmp_path):
    entries, packet = prepared(tmp_path)
    audio = io.BytesIO()
    with wave.open(audio, 'wb') as stream:
        stream.setnchannels(2); stream.setsampwidth(2); stream.setframerate(48_000)
        stream.writeframes(b'\0' * (48_000 * 12 * 2 * 2))
    body = json.dumps({'audio_base64': base64.b64encode(audio.getvalue()).decode()}).encode()
    assert 2 * 1024**2 < len(body) < 4 * 1024**2
    assert all(b'client_max_body_size 4m;' in item['block'] for item in entries)
    value = json.loads(packet.read_text())
    for item in value['files']:
        after = Path(item['after_path'])
        after.write_bytes(after.read_bytes().replace(b'client_max_body_size 4m;', b'client_max_body_size 2m;'))
        item['after_sha256'] = lease.sha(after.read_bytes())
        item['added_block_sha256'] = lease.sha(lease.expected_block(Path(item['before_path']).read_bytes(), item['marker']).replace(
            b'client_max_body_size 4m;', b'client_max_body_size 2m;'))
    packet.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='superseded_2m'):
        lease.load_packet(packet, live_paths=tuple(e['path'] for e in entries))


@pytest.mark.parametrize('mode', [[], ['--check'], ['--prepare']])
def test_default_check_and_prepare_are_read_only_and_never_request_privilege(tmp_path, monkeypatch, capsys, mode):
    entries, packet = prepared(tmp_path)
    monkeypatch.setattr(lease, 'load_packet', lambda _: entries)
    def forbidden(*_, **__): pytest.fail('read-only mode attempted mutation or command')
    monkeypatch.setattr(lease, 'atomic_cas', forbidden)
    monkeypatch.setattr(lease, 'command', forbidden)
    monkeypatch.setattr(lease.subprocess, 'Popen', forbidden)
    monkeypatch.setattr(sys, 'argv', [str(SCRIPT), '--packet', str(packet), *mode])
    lease.main()
    assert json.loads(capsys.readouterr().out)['privileged_changes'] is False
    assert not (tmp_path / 'journal.json').exists()


def fake_candidate(root):
    cwd = root / 'gamma-candidate'; source = cwd / 'src/gamma/__init__.py'
    source.parent.mkdir(parents=True); source.write_bytes(b'# source fixture\n')
    proc = root / 'proc'; pid = proc / '41'; (pid / 'fd').mkdir(parents=True); (proc / 'net').mkdir()
    (pid / 'cwd').symlink_to(cwd)
    (pid / 'fd/3').symlink_to('socket:[17]')
    (pid / 'stat').write_text('41 (candidate) S ' + '0 ' * 18 + '12345 0\n')
    argv = b'python\0candidate.py\0'; (pid / 'cmdline').write_bytes(argv)
    (proc / 'net/tcp').write_text('header\n0: 0100007F:4914 00000000:0000 0A 0 0 0 0 0 17\n')
    receipt = {'scope': 'isolated_owner_private', 'prefix': lease.PREFIX, 'port': lease.PORT, 'pid': 41,
        'created_at': 990, 'expires_at': 2000, 'cwd': str(cwd), 'resolved_source': str(source), 'start_ticks': '12345',
        'argv_sha256': lease.sha(argv), 'source_manifest': {'src/gamma/__init__.py': lease.sha(source.read_bytes())},
        'identity_id': '1' * 32, 'generation': 3}
    api_identity, api_generation = receipt['identity_id'], receipt['generation']
    class Response:
        def __init__(self, status, headers, body): self.status, self.headers, self.body = status, headers, body
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self, _): return self.body
    class Opener:
        def open(self, request, timeout):
            headers = Message()
            assert timeout == 3 and request.full_url.startswith('http://127.0.0.1:18708/review/shana/')
            if request.get_method() == 'POST':
                headers['Location'] = lease.PREFIX + '/dashboard'
                headers['Set-Cookie'] = 'gamma_review_shana_session=fixture; Path=/review/shana; Secure; HttpOnly; SameSite=lax'
                return Response(303, headers, b'')
            assert request.get_header('Cookie') == 'gamma_review_shana_session=fixture'
            body = {'identity_id': api_identity} if '/personal?' in request.full_url else {
                'scope': 'isolated_owner_private', 'generation': api_generation}
            return Response(200, headers, json.dumps(body).encode())
    return lease.CandidateProbe(receipt, {'username': 'fictional', 'password': 'fixture-password'}, proc=proc, now=lambda: 1000, opener=Opener())


def test_fresh_authenticated_candidate_requires_real_pid_listener_source_and_identity_binding(tmp_path):
    probe = fake_candidate(tmp_path)
    probe()
    (Path(probe.receipt['cwd']) / 'src/gamma/__init__.py').write_bytes(b'# changed source\n')
    with pytest.raises(ValueError): probe()


@pytest.mark.parametrize('field,value', [('start_ticks', 'reused-pid'), ('argv_sha256', 'changed'), ('port', 8001),
    ('scope', 'normal_owner'), ('expires_at', 999)])
def test_candidate_binding_changes_fail_before_authenticated_network_call(tmp_path, field, value):
    probe = fake_candidate(tmp_path); probe.receipt[field] = value
    with pytest.raises(ValueError): probe()


def test_ordinary_login_failure_or_unscoped_cookie_is_not_candidate_readiness(tmp_path):
    probe = fake_candidate(tmp_path)
    def bad(*_, **__): return 401, Message(), b'{}'
    probe._http = bad
    with pytest.raises(ValueError): probe()


@pytest.mark.parametrize('field', ['created_at', 'expires_at'])
@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf'), True, '1000', None])
def test_receipt_times_require_finite_numeric_values_on_initial_and_repeated_checks(tmp_path, field, value):
    probe = fake_candidate(tmp_path)
    probe.receipt[field] = value
    with pytest.raises(ValueError):
        lease.CandidateProbe(probe.receipt, probe.credentials, proc=probe.proc, now=lambda: 1000, opener=probe.opener)
    with pytest.raises(ValueError): probe.process_binding()


@pytest.mark.parametrize('created,expires', [(990, 2791), (990, 990), (990, 989), (1001, 1100), (990, 1000), (900, 1500)])
def test_initial_receipt_rejects_excess_lifetime_future_expired_or_stale_timestamps(tmp_path, created, expires):
    probe = fake_candidate(tmp_path)
    probe.receipt.update(created_at=created, expires_at=expires)
    with pytest.raises(ValueError):
        lease.CandidateProbe(probe.receipt, probe.credentials, proc=probe.proc, now=lambda: 1000, opener=probe.opener)


def test_repeated_receipt_check_allows_aging_valid_lease_but_never_renews_expiry(tmp_path):
    probe = fake_candidate(tmp_path)
    probe.now = lambda: 1051  # More than 60 seconds after the original fresh receipt.
    probe.process_binding()
    probe.now = lambda: 2000
    with pytest.raises(ValueError): probe.process_binding()


@pytest.mark.parametrize('same_site,accepted', [('Lax', True), ('strict', True), ('None', False), ('', False)])
def test_review_cookie_requires_lax_or_strict_samesite(tmp_path, same_site, accepted):
    probe = fake_candidate(tmp_path)
    original = probe._http
    def changed(method, *args, **kwargs):
        status, headers, body = original(method, *args, **kwargs)
        if method == 'POST':
            value = 'gamma_review_shana_session=fixture; Path=/review/shana; Secure; HttpOnly'
            if same_site: value += '; SameSite=' + same_site
            headers.replace_header('Set-Cookie', value)
        return status, headers, body
    probe._http = changed
    if accepted: probe()
    else:
        with pytest.raises(ValueError, match='cookie_scope'): probe()


def test_candidate_loss_triggers_cleanup_independently_of_ttl(tmp_path):
    controller, entries, clock, _ = controlled(tmp_path)
    count = 0
    def probe():
        nonlocal count
        count += 1
        if count > 2: raise ProcessLookupError('owned candidate exited')
    controller.probe = probe
    assert not controller.run() and clock.now == 1
    assert all(e['path'].read_bytes() == e['before'] for e in entries)


def test_manifest_must_include_full_importable_and_browser_source_inventory(tmp_path):
    probe = fake_candidate(tmp_path)
    (Path(probe.receipt['cwd']) / 'src/gamma/added.py').write_text('# unexpected source')
    with pytest.raises(ValueError): probe()


@pytest.mark.parametrize('field,value', [('generation', 4), ('identity_id', '2' * 32)])
def test_authenticated_api_must_match_expected_candidate_generation_and_identity(tmp_path, field, value):
    probe = fake_candidate(tmp_path)
    probe.receipt[field] = value
    with pytest.raises(ValueError): probe()


def test_cleanup_retries_against_new_current_bytes_after_concurrent_unrelated_edit(tmp_path):
    controller, entries, _, _ = controlled(tmp_path)
    conflicted = False
    def writer(path, expected, new):
        nonlocal conflicted
        if not conflicted and entries[0]['block'] in expected and entries[0]['block'] not in new:
            path.write_bytes(expected + b'# new concurrent edit\n')
            conflicted = True
        lease.atomic_cas(path, expected, new)
    controller.writer = writer
    assert controller.run() and controller.record['cleanup_attempts'] == 2
    assert entries[0]['path'].read_bytes() == entries[0]['before'] + b'# new concurrent edit\n'


def test_detached_guard_survives_launcher_exit_and_signal_cleans_real_disposable_files(tmp_path):
    entries, _ = prepared(tmp_path)
    state = tmp_path / 'entries.json'
    state.write_text(json.dumps([{**{k: base64.b64encode(e[k]).decode() for k in ['before', 'after', 'block', 'marker']},
        'path': str(e['path'])} for e in entries]))
    guard = tmp_path / 'fake_guard.py'
    guard.write_text("""import sys,json,base64,signal
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from review_proxy_lease import LeaseController
sys.stdin.buffer.read()
rows=json.loads(Path(sys.argv[2]).read_text())
entries=[{**{k:base64.b64decode(r[k]) for k in ['before','after','block','marker']},'path':Path(r['path'])} for r in rows]
c=LeaseController(entries,Path(sys.argv[3]),probe=lambda:None,staged_preflight=lambda:lambda _installed:None,syntax=lambda:None,reload=lambda:None,ttl=30)
signal.signal(signal.SIGHUP,lambda *_:c.request_stop('signal_hup'))
signal.signal(signal.SIGTERM,lambda *_:c.request_stop('signal_term'))
c.run()
""")
    journal = tmp_path / 'child-journal.json'
    launcher = tmp_path / 'launcher.py'
    launcher.write_text("""import sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from review_proxy_lease import detached_guard
c=detached_guard([sys.executable,*sys.argv[2:]],{'username':'fixture','password':'fixture'})
print(c.pid,flush=True)
""")
    launched = subprocess.run([sys.executable, str(launcher), str(SCRIPT.parent), str(guard), str(SCRIPT.parent), str(state), str(journal)],
        capture_output=True, text=True, timeout=10, check=True)
    pid = int(launched.stdout.strip())
    def until(predicate):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if predicate(): return
            time.sleep(.025)
        pytest.fail('owned fake guardian did not reach expected state')
    try:
        until(lambda: journal.exists() and json.loads(journal.read_text()).get('phase') == 'active')
        assert os.getsid(pid) == pid  # The launching process has already exited.
        assert entries[0]['block'] in entries[0]['path'].read_bytes()
        os.kill(pid, signal.SIGHUP)
        until(lambda: json.loads(journal.read_text()).get('phase') == 'cleaned')
        assert all(e['path'].read_bytes() == e['before'] for e in entries)
        assert json.loads(journal.read_text())['end_reason'] == 'signal_hup'
    finally:
        try:
            raw = (Path('/proc') / str(pid) / 'cmdline').read_bytes()
            if str(guard).encode() in raw: os.kill(pid, signal.SIGTERM)
        except ProcessLookupError: pass
        except FileNotFoundError: pass
