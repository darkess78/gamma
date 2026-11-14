"""Start/stop a finite isolated Gamma review through a hidden loopback SSH tunnel.

Uses only the Windows standard library. No Gamma runtime executes on this PC.
Fresh login details live in an ignored session file and are removed at shutdown.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import queue
import secrets
import shlex
import subprocess
import sys
import threading
import time
import uuid

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT / 'data' / 'coordination' / 'review_sessions'


def process_identity(pid):
    """Read process creation time, so a reused PID cannot confirm readiness."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
            return None
        if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel_time), ctypes.byref(user_time)):
            return None
        return str((created.dwHighDateTime << 32) | created.dwLowDateTime)
    finally:
        kernel.CloseHandle(handle)


def readiness(value, identity=process_identity, now=None):
    value = dict(value)
    if value['phase'] in {'starting', 'ready', 'stopping'}:
        observed = identity(value['controller_pid'])
        if not observed or observed != value.get('controller_creation_time'):
            value.update(recorded_phase=value['phase'], phase='unverified', reason='controller_missing_or_replaced')
        elif value['phase'] == 'ready' and (now if now is not None else time.time()) >= value.get('expires_at', 0):
            value.update(phase='stopping', reason='review_duration_elapsed')
    return value


def publish(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temporary.replace(path)


def current():
    value = json.loads((ROOT / 'latest.json').read_text(encoding='utf-8'))
    folder = Path(value['session_dir']).resolve()
    if not folder.is_relative_to(ROOT.resolve()) or folder.parent != ROOT.resolve():
        raise ValueError('invalid review session directory')
    return value, folder


@contextmanager
def controller_lock():
    import msvcrt
    ROOT.mkdir(parents=True, exist_ok=True)
    with (ROOT / 'controller.lock').open('a+b') as lock:
        if not lock.tell():
            lock.write(b'0'); lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def valid_review_duration(seconds, awake_test=False):
    if isinstance(seconds, bool) or not isinstance(seconds, int):
        return False
    if awake_test is True:
        return 5 <= seconds <= 57600
    return 5 <= seconds <= 1800


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stop', action='store_true')
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--host')
    parser.add_argument('--ssh-executable', default='ssh')
    parser.add_argument('--known-hosts-file', type=Path)
    parser.add_argument('--identity-file', type=Path)
    parser.add_argument('--remote-project')
    parser.add_argument('--model', default='qwen3.8:27b')
    parser.add_argument('--endpoint', default='http://127.0.0.1:11434')
    parser.add_argument('--port', type=int, default=18701)
    parser.add_argument('--duration', type=int, default=600)
    parser.add_argument('--awake-test', action='store_true', help='Allow a finite private test up to sixteen hours; capture and work leases keep their own limits')
    parser.add_argument('--mock', action='store_true')
    parser.add_argument('--private-voice', action='store_true')
    parser.add_argument('--voice-profile',default='qwen_shana_dataset_xvec_soft')
    parser.add_argument('--screen-awareness',action='store_true')
    parser.add_argument('--vision-model')
    parser.add_argument('--review-prefix', default='')
    args = parser.parse_args()
    if sys.platform != 'win32':
        raise RuntimeError('the review controller runs on Windows only')
    if ROOT.resolve() != PROJECT.resolve() / 'data' / 'coordination' / 'review_sessions':
        raise ValueError('review state cannot leave this checkout')
    if args.stop or args.status:
        value, folder = current()
        value = readiness(value)
        if args.stop and value['phase'] in {'starting', 'ready'}:
            (folder / 'STOP').touch()
            value['stop_requested'] = True
        publish(ROOT / 'status.json', value)
        if sys.stdout:
            print(json.dumps(value))
        return
    if (not args.host or args.host.startswith('-') or not args.identity_file or not args.identity_file.is_file()
        or not args.remote_project or not PurePosixPath(args.remote_project).is_absolute()
        or '..' in PurePosixPath(args.remote_project).parts
        or PurePosixPath(args.remote_project).name in {'gamma-main', 'gamma-modular-phase1'}
        or not 1024 <= args.port <= 65535 or args.port in {8000, 8001, 8080, 11434}
        or not valid_review_duration(args.duration, args.awake_test)):
        raise ValueError('provide a valid isolated SSH destination, key, port and duration')
    if args.ssh_executable != 'ssh' and (not Path(args.ssh_executable).is_absolute() or not Path(args.ssh_executable).is_file()):
        raise ValueError('provide an existing absolute SSH executable')
    if args.known_hosts_file is not None and not args.known_hosts_file.is_file():
        raise ValueError('known-hosts file must already exist')
    with controller_lock():
        session = uuid.uuid4().hex
        folder = ROOT / session
        folder.mkdir(mode=0o700)
        credentials = {key: secrets.token_urlsafe(32) for key in ('api_token', 'password', 'session_secret')}
        login = folder / 'credentials.json'
        login.write_text(json.dumps({'username': 'isolated-owner', 'password': credentials['password']}), encoding='utf-8')
        value = {'phase': 'starting', 'session_id': session, 'session_dir': str(folder),
                 'controller_pid': os.getpid(), 'url': f'http://127.0.0.1:{args.port}'+args.review_prefix+'/dashboard/'+('presence' if args.private_voice else 'home'),
                 'credentials_file': str(login), 'duration_seconds': args.duration, 'mock': args.mock,
                 'started_utc': datetime.now(timezone.utc).isoformat()}
        value['controller_creation_time'] = process_identity(os.getpid())
        if not value['controller_creation_time']:
            raise RuntimeError('controller identity could not be verified')
        def save():
            publish(folder / 'session.json', value)
            publish(ROOT / 'latest.json', value)
        save()
        command = ['.venv/bin/python', 'scripts/review_coordination.py', '--session-id', session,
                   '--model', args.model, '--endpoint', args.endpoint, '--dashboard-port', str(args.port),
                   '--duration', str(args.duration)] + (['--mock'] if args.mock else [])
        if args.awake_test:
            command += ['--awake-test']
        if args.private_voice:
            command += ['--private-voice','--voice-profile',args.voice_profile]
        if args.screen_awareness:
            command += ['--screen-awareness','--vision-model',args.vision_model or args.model]
        if args.review_prefix:
            command += ['--review-prefix',args.review_prefix]
        remote = 'cd ' + shlex.quote(args.remote_project) + ' && PYTHONPATH="$PWD/src" ' + shlex.join(command)
        argv = [args.ssh_executable, '-T', '-i', str(args.identity_file), '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ExitOnForwardFailure=yes',
                '-o', 'ServerAliveInterval=10', '-L', f'127.0.0.1:{args.port}:127.0.0.1:{args.port}', args.host, remote]
        if args.known_hosts_file is not None:
            argv[1:1] = ['-o', 'UserKnownHostsFile=' + str(args.known_hosts_file)]
        process = None
        messages = queue.Queue()
        reader = None
        try:
            with (folder / 'transport.log').open('w', encoding='utf-8') as errors:
                process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors,
                    text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW)
                value['ssh_pid'] = process.pid
                def read_messages():
                    for line in process.stdout:
                        messages.put(json.loads(line))
                    messages.put(None)
                reader = threading.Thread(target=read_messages, daemon=True)
                reader.start()
                process.stdin.write(json.dumps(credentials) + '\n'); process.stdin.flush()
                ready = messages.get(timeout=50)
                if isinstance(ready, dict) and ready.get('phase') == 'failed':
                    value['remote_error_code'] = ready.get('error_code', 'isolated_startup_failed')
                    value['remote_error_type'] = ready.get('error_type','RuntimeError')
                if not ready or ready.get('phase') != 'ready' or ready.get('scope') != 'isolated_owner_private' or ready.get('session_id') != session:
                    raise RuntimeError('isolated review did not become ready')
                value.update(phase='ready', remote_pid=ready['pid'], remote_state=ready['state'], source=ready['source'],
                             expires_at=time.time() + args.duration)
                if args.review_prefix:
                    value['remote_proxy_receipt'] = ready['proxy_receipt']
                    value['url_role'] = 'loopback_transport_only; HTTPS review origin is a separate validated route'
                save()
                end = time.monotonic() + args.duration + 170
                while process.poll() is None and time.monotonic() < end:
                    if (folder / 'STOP').exists():
                        value['phase'] = 'stopping'; save()
                        process.stdin.write('stop\n'); process.stdin.flush()
                        break
                    time.sleep(.2)
                process.wait(timeout=170)
                reader.join(timeout=2)
                received = []
                while not messages.empty():
                    received.append(messages.get_nowait())
                if process.returncode != 0 or not any(row and row.get('phase') == 'closed' and row.get('session_id') == session for row in received):
                    raise RuntimeError('remote review shutdown was not confirmed')
                value['phase'] = 'closed'
        except Exception as exc:
            value.update(phase='failed', error_type=type(exc).__name__)
            raise
        finally:
            if process and process.poll() is None:
                process.stdin.close()  # EOF requests bounded remote shutdown.
                try:
                    process.wait(timeout=170)
                except subprocess.TimeoutExpired:
                    process.terminate()  # This controller's SSH child only.
                    process.wait(timeout=10)
            if process:
                value['ssh_exit_code'] = process.returncode
            login.unlink(missing_ok=True)
            save()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        if ROOT.resolve() == PROJECT.resolve() / 'data' / 'coordination' / 'review_sessions':
            ROOT.mkdir(parents=True, exist_ok=True)
            publish(ROOT / 'launch-error.json', {'error_type': type(exc).__name__, 'time_utc': datetime.now(timezone.utc).isoformat()})
        sys.exit(1)
