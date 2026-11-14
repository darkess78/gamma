"""Prepare/check a finite review proxy lease; mutation requires explicit --apply.

No sudo, service stop or privileged fallback is attempted. A detached root-run
guardian owns cleanup independently of the candidate and launching SSH session.
SIGKILL/power loss and an invalid external nginx edit can prevent cleanup; the
root-owned journal records a failed cleanup for explicit recovery.
"""
import argparse
import base64
from contextlib import contextmanager
import glob
import hashlib
from http.cookies import SimpleCookie
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


LIVE_PATHS = (Path('/etc/nginx/conf.d/gamma-proxy.conf'), Path('/etc/nginx/conf.d/gamma-npm-upstream.conf'))
STATE_ROOT = Path('/run/shana-review-proxy')
PREFIX, PORT, MAX_TTL = '/review/shana', 18708, 1800


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read_regular(path, maximum=2_000_000):
    path = Path(path)
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ValueError('symlink_not_allowed')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > maximum or info.st_nlink != 1:
        raise ValueError('invalid_regular_file')
    raw = path.read_bytes()
    if len(raw) > maximum:
        raise ValueError('file_exceeds_limit')
    return raw


def expected_block(before, marker):
    text = before.decode('utf-8')
    if text.count('server_name gamma.neety.me;') != 1 or '# SHANA PRIVATE REVIEW' in text:
        raise ValueError('unreviewed_original_scope')
    def header(name, default):
        values = set(re.findall(r'proxy_set_header\s+' + name + r'\s+([^;]+);', text))
        allowed = {'$scheme', 'https', 'http', '$server_port', '443', '8080', '$http_x_forwarded_proto', '$http_x_forwarded_port'}
        if len(values) > 1 or any(value not in allowed for value in values):
            raise ValueError('unreviewed_forwarding_header')
        return next(iter(values), default)
    proto, port = header('X-Forwarded-Proto', '$scheme'), header('X-Forwarded-Port', '$server_port')
    return (f'\n\n    # SHANA PRIVATE REVIEW {marker} BEGIN\n'
        f'    location = {PREFIX} {{ return 307 {PREFIX}/dashboard/home; }}\n'
        f'    location ^~ {PREFIX}/ {{\n'
        f'        proxy_pass http://127.0.0.1:{PORT};\n'
        '        proxy_http_version 1.1;\n'
        '        proxy_set_header Host $host;\n'
        '        proxy_set_header X-Real-IP $remote_addr;\n'
        '        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n'
        f'        proxy_set_header X-Forwarded-Proto {proto};\n'
        '        proxy_set_header X-Forwarded-Host $host;\n'
        f'        proxy_set_header X-Forwarded-Port {port};\n'
        '        proxy_read_timeout 180s;\n'
        '        proxy_send_timeout 180s;\n'
        '        client_max_body_size 4m;\n'
        '    }\n'
        f'    # SHANA PRIVATE REVIEW {marker} END\n').encode('utf-8')


def load_packet(path, *, live_paths=LIVE_PATHS):
    path = Path(path).resolve()
    packet = json.loads(read_regular(path, 64_000))
    if packet.get('prefix') != PREFIX or packet.get('private_dashboard_port') != PORT or len(packet.get('files', [])) != 2:
        raise ValueError('unexpected_review_packet')
    entries = []
    for item, target in zip(packet['files'], live_paths):
        if Path(item['live_path']) != target or not re.fullmatch(r'[a-f0-9]{32}', item.get('marker', '')):
            raise ValueError('unexpected_live_path_or_marker')
        staged = []
        for key in ('before_path', 'after_path'):
            candidate = Path(item[key])
            if candidate.parent != path.parent:
                raise ValueError('staging_file_outside_packet')
            staged.append(read_regular(candidate))
        before, after = staged
        block = expected_block(before, item['marker'])
        expected = before.replace(b'server_name gamma.neety.me;', b'server_name gamma.neety.me;' + block, 1)
        superseded = block.replace(b'client_max_body_size 4m;', b'client_max_body_size 2m;')
        if after == before.replace(b'server_name gamma.neety.me;', b'server_name gamma.neety.me;' + superseded, 1):
            raise ValueError('superseded_2m_review_packet')
        if (after != expected or sha(before) != item['before_sha256'] or sha(after) != item['after_sha256']
            or sha(block) != item['added_block_sha256']):
            raise ValueError('packet_bytes_or_addition_mismatch')
        entries.append({'path': target, 'before': before, 'after': after, 'block': block,
                        'marker': ('SHANA PRIVATE REVIEW ' + item['marker']).encode()})
    return entries


def remove_owned(current: bytes, block: bytes, marker: bytes) -> bytes:
    """Reviewed worker helper; never restore an old whole configuration."""
    if marker not in current:
        return current
    if current.count(block) != 1:
        raise ValueError('exact block must occur exactly once')
    if current.count(marker) != 2:
        raise ValueError('marker must occur exactly twice (BEGIN and END)')
    return current.replace(block, b'', 1)


def atomic_cas(path, expected, replacement):
    """Compare current bytes immediately before replace; keep mode/owner."""
    path = Path(path)
    if read_regular(path) != expected:
        raise ValueError('configuration_compare_failed')
    before = path.stat()
    descriptor, temporary = tempfile.mkstemp(prefix='.shana-review-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            os.fchmod(stream.fileno(), stat.S_IMODE(before.st_mode))
            if os.geteuid() == 0:
                os.fchown(stream.fileno(), before.st_uid, before.st_gid)
            stream.write(replacement); stream.flush(); os.fsync(stream.fileno())
        if path.stat() != before or read_regular(path) != expected:
            raise ValueError('configuration_changed_before_replace')
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def journal_write(path, value):
    raw = (json.dumps(value, indent=2) + '\n').encode()
    if path.exists():
        atomic_cas(path, read_regular(path), raw)
    else:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())


def include_directives(text):
    """Reviewed helper plus strict comment/escape guards for literal includes."""
    n, i, rows = len(text), 0, []
    def whitespace(index):
        while index < n:
            if text[index].isspace(): index += 1
            elif text[index] == '#':
                while index < n and text[index] != '\n': index += 1
            else: break
        return index
    def quoted(index):
        quote = text[index]; index += 1; value = []
        while index < n:
            char = text[index]
            if char == '\\' and index + 1 < n:
                value.extend(text[index:index + 2]); index += 2
            elif char == quote:
                return ''.join(value), index + 1
            else:
                value.append(char); index += 1
        raise ValueError('unclosed_nginx_quote')
    while i < n:
        i = whitespace(i)
        if i >= n: break
        if text[i] in '{};': i += 1; continue
        if text[i] in "\"'":
            value, i = quoted(i)
            if value == 'include': raise ValueError('unsupported_quoted_include_keyword')
            continue
        start = i
        while i < n and text[i] not in " \t\r\n;{}'\"#": i += 1
        word = text[start:i]
        if word == 'include':
            j = whitespace(i)
            if j >= n or text[j] in ';{}': raise ValueError('include_requires_one_literal_path')
            if text[j] in "\"'": path, j = quoted(j)
            else:
                begin = j
                while j < n and text[j] not in " \t\r\n;{}'\"#": j += 1
                path = text[begin:j]
            if not path or '$' in path or '\\' in path:
                raise ValueError('include_requires_unescaped_literal_path')
            end = whitespace(j)
            if end >= n or text[end] != ';': raise ValueError('malformed_nginx_include')
            rows.append({'path': path, 'start': start, 'end': end + 1}); i = end + 1
        else:
            # Keep escaped delimiters and quoted include-looking text inside
            # their actual unrelated directive, rather than inventing includes.
            while i < n and text[i] not in ';{}':
                if text[i] == '\\': i += 2
                elif text[i] in "\"'": _, i = quoted(i)
                elif text[i] == '#':
                    while i < n and text[i] != '\n': i += 1
                else: i += 1
    return rows


def nginx_input(path):
    """Pin existing include symlinks by target as well as bytes and identity."""
    path = Path(path).absolute()
    resolved = path.resolve(strict=True)
    before = resolved.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > 2_000_000:
        raise ValueError('unsupported_nginx_input')
    raw = resolved.read_bytes()
    after = resolved.stat()
    attributes = ('st_dev', 'st_ino', 'st_mtime_ns', 'st_size', 'st_mode', 'st_uid', 'st_gid')
    if (any(getattr(before, key) != getattr(after, key) for key in attributes)
        or len(raw) > 2_000_000 or path.resolve(strict=True) != resolved):
        raise ValueError('nginx_input_changed_during_read')
    return raw, {'resolved': str(resolved), 'sha256': sha(raw), 'device': after.st_dev,
        'inode': after.st_ino, 'mtime_ns': after.st_mtime_ns, 'size': after.st_size,
        'mode': stat.S_IMODE(after.st_mode), 'uid': after.st_uid, 'gid': after.st_gid}


def include_matches(pattern):
    if not Path(pattern).is_absolute() or '$' in pattern or '\\' in pattern:
        raise ValueError('nginx_staging_requires_literal_absolute_includes')
    matches = sorted(str(Path(p).absolute()) for p in glob.glob(pattern))
    if not glob.has_magic(pattern) and not matches:
        raise ValueError('missing_nginx_include')
    return matches


class CompleteNginxStage:
    """One exact master substitution, with the complete current include graph."""
    def __init__(self, entries, folder, *, master=Path('/etc/nginx/nginx.conf')):
        self.entries = {str(e['path']): e for e in entries}
        parents = {str(e['path'].parent) for e in entries}
        if len(parents) != 1:
            raise ValueError('review_files_must_share_confd_directory')
        self.confd = next(iter(parents)) + '/*.conf'
        self.master, self.folder = Path(master).absolute(), Path(folder).absolute()
        self.inputs, self.patterns, self.staged = {}, {}, {}
        self.bytes_read = 0
        self._walk(self.master, set())
        raw, _ = nginx_input(self.master)
        text = raw.decode('utf-8')
        directives = [d for d in include_directives(text) if d['path'] == self.confd]
        if len(directives) != 1:
            raise ValueError('master_requires_exact_single_confd_wildcard')
        current = self.patterns[self.confd]
        if not set(self.entries).issubset(current):
            raise ValueError('review_files_not_in_actual_master_include')
        if self.folder.exists():
            raise ValueError('staging_requires_fresh_private_directory')
        self.folder.mkdir(mode=0o700)
        for path, entry in self.entries.items():
            staged = self.folder / (Path(path).name + '.after')
            self._write(staged, entry['after'])
        lines = []
        for path in current:
            selected = self.folder / (Path(path).name + '.after') if path in self.entries else Path(path)
            if any(c in str(selected) for c in ('"', '\\', '\n', '\r')):
                raise ValueError('unsupported_include_filename')
            lines.append('include "' + str(selected) + '";')
        chosen = directives[0]
        complete = (text[:chosen['start']] + '\n'.join(lines) + text[chosen['end']:]).encode('utf-8')
        self.complete = self.folder / 'complete-nginx.conf'
        self._write(self.complete, complete)
        self.verify(set())
        self.proof = {'complete_master': str(self.complete), 'complete_sha256': sha(complete),
            'master': str(self.master), 'input_pins': self.inputs, 'include_patterns': self.patterns,
            'staged_sha256': self.staged, 'syntax_validated': False,
            'only_master_substitution': self.confd}
        journal_write(self.folder / 'stage-proof.json', self.proof)

    def _write(self, path, raw):
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        self.staged[str(path)] = sha(raw)

    def _walk(self, path, ancestors):
        path = str(Path(path).absolute())
        if path in ancestors or len(ancestors) > 16:
            raise ValueError('nginx_include_cycle_or_depth')
        if path in self.inputs:
            return
        raw, pin = nginx_input(path)
        self.inputs[path] = pin
        self.bytes_read += len(raw)
        if len(self.inputs) > 512 or self.bytes_read > 8_000_000:
            raise ValueError('nginx_include_graph_exceeds_bound')
        if path in self.entries:
            if raw != self.entries[path]['before']:
                raise ValueError('live_review_original_changed_before_staging')
            raw = self.entries[path]['after']
        for directive in include_directives(raw.decode('utf-8')):
            pattern = directive['path']
            matches = include_matches(pattern)
            if pattern in self.patterns and self.patterns[pattern] != matches:
                raise ValueError('include_membership_changed_during_staging')
            self.patterns[pattern] = matches
            for member in matches:
                self._walk(member, ancestors | {path})

    def verify(self, installed):
        installed = set(installed)
        if not installed.issubset(self.entries):
            raise ValueError('unexpected_installed_stage_target')
        for pattern, original in self.patterns.items():
            if include_matches(pattern) != original:
                raise ValueError('nginx_include_membership_changed')
        for path, original in self.inputs.items():
            _, current = nginx_input(path)
            if path in installed:
                kept = ('resolved', 'mode', 'uid', 'gid')
                if (current['sha256'] != sha(self.entries[path]['after'])
                    or any(current[k] != original[k] for k in kept)):
                    raise ValueError('installed_nginx_config_changed')
            elif current != original:
                raise ValueError('nginx_master_or_include_changed')
        if any(sha(read_regular(path)) != expected for path, expected in self.staged.items()):
            raise ValueError('staged_nginx_config_changed')

    def preflight(self, runner):
        self.verify(set())
        argv = ['/usr/sbin/nginx', '-t', '-c', str(self.complete)]
        runner(argv)
        self.verify(set())
        self.proof.update(syntax_validated=True, syntax_command=argv)
        journal_write(self.folder / 'stage-proof.json', self.proof)
        return self.verify


class LeaseController:
    """Injectable operations allow real disposable-file failure tests."""
    def __init__(self, entries, journal, *, probe, staged_preflight, syntax, reload, writer=atomic_cas,
                 clock=time.monotonic, sleep=time.sleep, ttl=1800, stop=None):
        if type(ttl) is not int or not 1 <= ttl <= MAX_TTL:
            raise ValueError('lease_seconds_out_of_range')
        self.entries, self.journal = entries, Path(journal)
        self.probe, self.staged_preflight = probe, staged_preflight
        self.syntax, self.reload, self.writer = syntax, reload, writer
        self.clock, self.sleep, self.ttl = clock, sleep, ttl
        self.stop = stop or threading.Event()
        self.reason = None
        self.record = {'version': 1, 'pid': os.getpid(), 'phase': 'preflight', 'ttl_seconds': ttl,
            'installed': [], 'history': [], 'entries': [{'path': str(e['path']), 'before_sha256': sha(e['before']),
                'after_sha256': sha(e['after']), 'block_base64': base64.b64encode(e['block']).decode(),
                'block_sha256': sha(e['block']),
                'marker': e['marker'].decode()} for e in entries]}

    def note(self, phase, **values):
        self.record.update(phase=phase, **values)
        self.record['history'].append({'phase': phase, 'at_monotonic': self.clock()})
        journal_write(self.journal, self.record)

    def request_stop(self, reason):
        self.reason = reason
        self.stop.set()

    def cleanup(self):
        # Include all entries: a rename can succeed just before an injected crash.
        errors = []
        for attempt in range(3):
            errors = []
            for entry in self.entries:
                try:
                    current = read_regular(entry['path'])
                    without = remove_owned(current, entry['block'], entry['marker'])
                    if without != current:
                        self.writer(entry['path'], current, without)
                except Exception as error:
                    errors.append({'path': str(entry['path']), 'error_type': type(error).__name__})
            if not errors:
                try:
                    self.syntax()  # Every reload, including rollback, requires fresh syntax.
                    self.reload()
                    self.note('cleaned', cleanup_attempts=attempt + 1, cleanup_errors=[])
                    return True
                except Exception as error:
                    errors.append({'operation': 'cleanup_syntax_or_reload', 'error_type': type(error).__name__})
            self.note('cleanup_retry', cleanup_attempts=attempt + 1, cleanup_errors=errors)
            if attempt < 2: self.sleep(attempt + 1)
        self.note('cleanup_failed', cleanup_errors=errors)
        return False

    def run(self):
        changed = False
        success = False
        try:
            self.probe()
            self.note('validating_complete_staged_config')
            stage_guard = self.staged_preflight()
            if not callable(stage_guard):
                raise ValueError('staged_preflight_must_return_drift_guard')
            stage_guard(set())
            self.record['staged_preflight_completed'] = True
            # Require both exact originals before touching either configuration.
            if any(read_regular(e['path']) != e['before'] for e in self.entries):
                raise ValueError('original_configuration_changed')
            self.record['deadline_monotonic'] = self.clock() + self.ttl
            self.record['deadline_unix'] = time.time() + self.ttl
            self.note('installing')
            for entry in self.entries:
                stage_guard(set(self.record['installed']))
                if self.clock() >= self.record['deadline_monotonic']:
                    raise TimeoutError('lease_expired_during_install')
                changed = True  # An error may follow a successful atomic rename.
                self.writer(entry['path'], entry['before'], entry['after'])
                self.record['installed'].append(str(entry['path']))
                self.note('installing')
            stage_guard(set(self.record['installed']))
            self.probe()
            stage_guard(set(self.record['installed']))
            self.syntax()
            stage_guard(set(self.record['installed']))
            if self.clock() >= self.record['deadline_monotonic']:
                raise TimeoutError('lease_expired_before_reload')
            self.reload()
            self.note('active')
            while self.clock() < self.record['deadline_monotonic'] and not self.stop.is_set():
                self.sleep(min(1, self.record['deadline_monotonic'] - self.clock()))
                if self.stop.is_set(): break
                self.probe()
            self.record['end_reason'] = self.reason or 'lease_expired'
            success = True
        except Exception as error:
            self.note('failed', error_type=type(error).__name__, end_reason=self.reason or 'operation_failed')
        finally:
            if changed:
                success = self.cleanup() and success
            elif self.record['phase'] != 'failed':
                self.note('not_applied')
        return success


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_): return None


class CandidateProbe:
    """Existing authenticated APIs plus trusted launcher receipt and Linux PID bind."""
    def __init__(self, receipt, credentials, *, proc=Path('/proc'), now=time.time, opener=None):
        self.receipt, self.credentials = receipt, credentials
        self.proc, self.now = Path(proc), now
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self._times(initial=True)

    def _times(self, *, initial=False):
        created, expires, current = self.receipt.get('created_at'), self.receipt.get('expires_at'), self.now()
        if any(type(value) not in {int, float} or not math.isfinite(value) for value in (created, expires, current)):
            raise ValueError('candidate_receipt_nonfinite_time')
        if not 0 < expires - created <= MAX_TTL:
            raise ValueError('candidate_receipt_lifetime_out_of_range')
        if created > current or current >= expires or initial and current - created > 60:
            raise ValueError('candidate_receipt_not_fresh_or_expired')

    def process_binding(self):
        self._times()
        r = self.receipt
        if (r.get('scope') != 'isolated_owner_private' or r.get('prefix') != PREFIX or r.get('port') != PORT
            or type(r.get('pid')) is not int or r['pid'] <= 1 or self.now() >= r['expires_at']
            or type(r.get('generation')) is not int or r['generation'] <= 0
            or not re.fullmatch(r'[a-f0-9]{32}', r.get('identity_id', ''))):
            raise ValueError('candidate_scope_or_deadline_mismatch')
        cwd = Path(r['cwd'])
        if cwd.name in {'gamma-main', 'gamma-modular-phase1'} or Path(r['resolved_source']) != cwd / 'src/gamma/__init__.py':
            raise ValueError('candidate_import_source_mismatch')
        process = self.proc / str(r['pid'])
        if Path(os.readlink(process / 'cwd')) != cwd:
            raise ValueError('candidate_cwd_changed')
        raw = (process / 'stat').read_text()
        start = raw[raw.rfind(')') + 2:].split()[19]
        if str(r['start_ticks']) != start or sha((process / 'cmdline').read_bytes()) != r['argv_sha256']:
            raise ValueError('candidate_pid_or_command_changed')
        if not r.get('source_manifest') or 'src/gamma/__init__.py' not in r['source_manifest']:
            raise ValueError('candidate_source_manifest_missing')
        inventory = {p.relative_to(cwd).as_posix() for p in (cwd / 'src/gamma').rglob('*')
            if p.is_file() and p.suffix in {'.py', '.html', '.js', '.css'} and '__pycache__' not in p.parts}
        if set(r['source_manifest']) != inventory:
            raise ValueError('candidate_source_inventory_changed_or_incomplete')
        for relative, expected in r['source_manifest'].items():
            target = cwd / relative
            if Path(relative).is_absolute() or '..' in Path(relative).parts or sha(read_regular(target)) != expected:
                raise ValueError('candidate_source_changed')
        sockets = set()
        for fd in (process / 'fd').iterdir():
            try: sockets.add(os.readlink(fd))
            except FileNotFoundError: pass
        matches = []
        for line in (self.proc / 'net/tcp').read_text().splitlines()[1:]:
            fields = line.split()
            if fields[3] == '0A' and fields[1].split(':')[-1] == f'{PORT:04X}':
                matches.append(fields[1] == f'0100007F:{PORT:04X}' and 'socket:[' + fields[9] + ']' in sockets)
        if matches != [True]:
            raise ValueError('candidate_listener_not_owned_exact_loopback')
        tcp6 = self.proc / 'net/tcp6'
        if tcp6.exists() and any(line.split()[3] == '0A' and line.split()[1].split(':')[-1] == f'{PORT:04X}'
                                 for line in tcp6.read_text().splitlines()[1:]):
            raise ValueError('unexpected_ipv6_candidate_listener')

    def _http(self, method, path, data=None, cookie=None):
        headers = {'Host': 'gamma.neety.me', 'Accept': 'application/json'}
        if cookie: headers['Cookie'] = cookie
        if data is not None: headers['Content-Type'] = 'application/x-www-form-urlencoded'
        request = urllib.request.Request(f'http://127.0.0.1:{PORT}' + PREFIX + path, data=data, headers=headers, method=method)
        try:
            response = self.opener.open(request, timeout=3)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            body = response.read(512_001)
            if len(body) > 512_000: raise ValueError('candidate_response_too_large')
            return response.status, response.headers, body

    def __call__(self):
        self.process_binding()
        payload = urllib.parse.urlencode({'username': self.credentials['username'], 'password': self.credentials['password']}).encode()
        status, headers, _ = self._http('POST', '/login', payload)
        if status != 303 or headers.get('Location') != PREFIX + '/dashboard':
            raise ValueError('candidate_login_failed')
        cookie = SimpleCookie()
        for value in headers.get_all('Set-Cookie', []): cookie.load(value)
        name = 'gamma_review_shana_session'
        if (set(cookie) != {name} or cookie[name]['path'] != PREFIX or not cookie[name]['secure']
            or not cookie[name]['httponly'] or cookie[name]['samesite'].lower() not in {'lax', 'strict'}):
            raise ValueError('candidate_cookie_scope_mismatch')
        scoped = name + '=' + cookie[name].coded_value
        for path, identity in [('/api/coordination/status?audience=owner_private', False),
                               ('/api/coordination/personal?audience=owner_private', True)]:
            status, _, raw = self._http('GET', path, cookie=scoped)
            value = json.loads(raw)
            if status != 200: raise ValueError('candidate_authenticated_api_failed')
            if identity:
                if value.get('identity_id') != self.receipt['identity_id']: raise ValueError('candidate_identity_changed')
            elif value.get('scope') != 'isolated_owner_private' or value.get('generation') != self.receipt['generation']:
                raise ValueError('candidate_generation_or_scope_changed')
        self.process_binding()


def command(argv):
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            timeout=12, check=False)
    if result.returncode != 0:
        raise RuntimeError('controlled_command_failed')


@contextmanager
def exclusive_guard():
    import fcntl
    descriptor = os.open(STATE_ROOT / 'lease.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != 0:
            raise ValueError('unsafe_lease_lock')
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally: os.close(descriptor)


def credentials_stdin():
    raw = sys.stdin.buffer.read(8193)
    if len(raw) > 8192: raise ValueError('credentials_input_too_large')
    value = json.loads(raw)
    if set(value) != {'username', 'password'} or not all(isinstance(v, str) and v for v in value.values()):
        raise ValueError('invalid_credentials_input')
    return value


def validate_journal(path):
    if (path.parent.parent != STATE_ROOT or not re.fullmatch(r'[a-f0-9]{32}', path.parent.name)
        or path.name != 'journal.json' or path.parent.is_symlink() or path.parent.stat().st_uid != 0
        or stat.S_IMODE(path.parent.stat().st_mode) != 0o700):
        raise ValueError('unexpected_or_unsafe_journal')


def detached_guard(argv, credentials):
    """Pipe secrets once; no log, credential file or parent lifetime dependency."""
    child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    child.stdin.write(json.dumps(credentials).encode()); child.stdin.close()
    return child


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packet', type=Path)
    parser.add_argument('--candidate-receipt', type=Path)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--lease-seconds', type=int, default=MAX_TTL)
    parser.add_argument('--cleanup', type=Path)
    parser.add_argument('--_guardian', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 1 <= args.lease_seconds <= MAX_TTL: parser.error('lease must be 1..1800 seconds')
    if not args.apply:
        if not args.packet: parser.error('--packet is required for read-only check/prepare')
        entries = load_packet(args.packet)
        print(json.dumps({'status': 'prepared_not_applied', 'live_originals_match': all(read_regular(e['path']) == e['before'] for e in entries),
            'prefix': PREFIX, 'port': PORT, 'maximum_lease_seconds': MAX_TTL, 'privileged_changes': False,
            'activation_requires': 'explicit root --apply, fresh owned candidate receipt, credentials on stdin',
            'limits': ['SIGKILL/power loss require recovery cleanup', 'conflicting nginx edits can prevent cleanup reload']}))
        return
    if args.check or args.prepare: parser.error('--apply cannot be combined with read-only modes')
    if os.name != 'posix' or os.geteuid() != 0: raise PermissionError('explicit_root_run_required_no_sudo_attempted')
    STATE_ROOT.mkdir(mode=0o700, exist_ok=True)
    if STATE_ROOT.is_symlink() or STATE_ROOT.stat().st_uid != 0 or stat.S_IMODE(STATE_ROOT.stat().st_mode) != 0o700:
        raise ValueError('unsafe_root_lease_directory')
    if args.cleanup:
        journal = args.cleanup
        validate_journal(journal)
        if journal.stat().st_uid != 0: raise ValueError('cleanup_journal_not_root_owned')
        record = json.loads(read_regular(journal, 100_000))
        if len(record.get('entries', [])) != 2: raise ValueError('incomplete_cleanup_journal')
        entries = []
        for item, target in zip(record['entries'], LIVE_PATHS):
            if item['path'] != str(target): raise ValueError('unexpected_cleanup_target')
            block = base64.b64decode(item['block_base64'], validate=True)
            if sha(block) != item['block_sha256'] or not re.fullmatch(r'SHANA PRIVATE REVIEW [a-f0-9]{32}', item['marker']):
                raise ValueError('cleanup_block_changed')
            entries.append({'path': target, 'before': b'', 'after': b'', 'block': block, 'marker': item['marker'].encode()})
        with exclusive_guard():
            controller = LeaseController(entries, journal, probe=lambda: None, syntax=lambda: command(['/usr/sbin/nginx', '-t']),
                staged_preflight=lambda: None,
                reload=lambda: command(['/usr/bin/systemctl', 'reload', 'nginx']))
            controller.record = record
            if not controller.cleanup(): raise RuntimeError('cleanup_failed_see_journal')
        return
    if not args.packet or not args.candidate_receipt: parser.error('--apply needs --packet and --candidate-receipt')
    if args._guardian:
        journal = args._guardian
        validate_journal(journal)
        try:
            entries = load_packet(args.packet)
            credentials = credentials_stdin()
            receipt = json.loads(read_regular(args.candidate_receipt, 200_000))
            probe = CandidateProbe(receipt, credentials)
            with exclusive_guard():
                def staged_preflight():
                    return CompleteNginxStage(entries, journal.parent / 'staged').preflight(command)
                controller = LeaseController(entries, journal, probe=probe, syntax=lambda: command(['/usr/sbin/nginx', '-t']),
                    staged_preflight=staged_preflight,
                    reload=lambda: command(['/usr/bin/systemctl', 'reload', 'nginx']), ttl=args.lease_seconds)
                for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                    signal.signal(signum, lambda value, _: controller.request_stop('signal_' + str(value)))
                if not controller.run(): raise RuntimeError('lease_failed_see_journal')
        except Exception as error:
            record = json.loads(read_regular(journal, 100_000)) if journal.exists() else {}
            # Preserve successful cleanup or a conflict receipt instead of hiding it.
            if record.get('phase') not in {'cleaned', 'cleanup_failed'}: record['phase'] = 'guardian_failed'
            record['guardian_error_type'] = type(error).__name__
            journal_write(journal, record)
            raise
        return
    load_packet(args.packet)
    credentials = credentials_stdin()
    folder = STATE_ROOT / uuid.uuid4().hex
    folder.mkdir(mode=0o700)
    journal = folder / 'journal.json'
    journal_write(journal, {'phase': 'guardian_starting', 'activation_claim': False})
    child = detached_guard(['/usr/bin/python3', '-I', str(Path(__file__).resolve()), '--apply', '--_guardian', str(journal),
        '--packet', str(args.packet.resolve()), '--candidate-receipt', str(args.candidate_receipt.resolve()),
        '--lease-seconds', str(args.lease_seconds)], credentials)
    print(json.dumps({'status': 'guardian_started_not_yet_verified_active', 'guardian_pid': child.pid, 'journal': str(journal),
                      'activation_claim': False, 'operator_must_read_journal': True}))


if __name__ == '__main__':
    try: main()
    except Exception as error:
        print(json.dumps({'status': 'failed', 'error_type': type(error).__name__, 'details_or_credentials_logged': False}), file=sys.stderr)
        raise SystemExit(1)
