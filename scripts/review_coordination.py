"""Run an authenticated, finite private review session on the isolated Gamma PC checkout.

The controller sends ephemeral credentials on stdin, followed by stop or EOF.
Stdout contains only nonsecret lifecycle metadata. No unit or configuration is edited.
"""
import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import select
import sys
import time
from urllib.parse import urlsplit
import urllib.request
import uuid


class ReviewSetupError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def write_proxy_receipt(project, state, prefix, port, core, source, created_at, expires_at):
    """Bind this actual finite process and owner for read-only proxy preflight."""
    process = Path('/proc/self')
    raw = (process / 'stat').read_text(encoding='utf-8')
    manifest = {}
    for path in sorted((project / 'src/gamma').rglob('*')):
        if path.suffix not in {'.py', '.html', '.js', '.css'} or '__pycache__' in path.parts:
            continue
        if path.is_symlink():
            raise ReviewSetupError('review_source_symlink')
        if path.is_file():
            manifest[path.relative_to(project).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    with core.store.transaction(core.token) as db:
        generation = core._brain(db)['generation']
    receipt = {'scope': 'isolated_owner_private', 'prefix': prefix, 'port': port,
        'pid': os.getpid(), 'created_at': created_at, 'expires_at': expires_at,
        'cwd': str(Path.cwd().resolve()), 'resolved_source': str(source),
        'start_ticks': raw[raw.rfind(')') + 2:].split()[19],
        'argv_sha256': hashlib.sha256((process / 'cmdline').read_bytes()).hexdigest(),
        'identity_id': core.personal_state()['identity_id'], 'generation': generation,
        'source_manifest': manifest}
    path = state / 'review-proxy-receipt.json'
    payload = (json.dumps(receipt, indent=2) + '\n').encode('utf-8')
    path.write_bytes(payload)
    path.chmod(0o600)
    return {'path': str(path), 'sha256': hashlib.sha256(payload).hexdigest(),
            'created_at': created_at, 'expires_at': expires_at}


def valid_review_duration(seconds, awake_test=False):
    if isinstance(seconds, bool) or not isinstance(seconds, int):
        return False
    if awake_test is True:
        return 5 <= seconds <= 57600
    return 5 <= seconds <= 1800


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session-id', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--endpoint', default='http://127.0.0.1:11434')
    parser.add_argument('--dashboard-port', type=int, default=18701)
    parser.add_argument('--duration', type=int, default=600)
    parser.add_argument('--awake-test', action='store_true', help='Allow a finite private test up to sixteen hours; capture and work leases keep their own limits')
    parser.add_argument('--mock', action='store_true', help='Explicit no-inference launcher verification')
    parser.add_argument('--private-voice', action='store_true', help='Prepare local private microphone review; capture remains client opt-in')
    parser.add_argument('--voice-profile', default='qwen_shana_dataset_xvec_soft')
    parser.add_argument('--screen-awareness', action='store_true', help='Enable configured local selected-frame perception; no automatic capture')
    parser.add_argument('--vision-model')
    parser.add_argument('--review-prefix', default='')
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    endpoint = urlsplit(args.endpoint)
    if (os.name != 'posix' or project.name in {'gamma-main', 'gamma-modular-phase1'}
        or not re.fullmatch('[a-f0-9]{32}', args.session_id)
        or not valid_review_duration(args.duration, args.awake_test) or not 1024 <= args.dashboard_port <= 65535
        or args.dashboard_port in {8000, 8001, 8080, 11434}
        or endpoint.scheme != 'http' or endpoint.hostname not in {'127.0.0.1', '::1'}
        or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment
        or endpoint.path not in {'', '/'}
        or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',args.voice_profile)
        or args.screen_awareness and args.mock
        or args.vision_model is not None and not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,160}',args.vision_model)):
        raise ValueError('invalid isolated review configuration')
    if args.review_prefix and not re.fullmatch(r'/review/[a-z][a-z0-9-]{0,40}', args.review_prefix):
        raise ValueError('invalid isolated review prefix')
    credentials = json.loads(sys.stdin.readline())
    if not isinstance(credentials, dict) or any(
        not isinstance(credentials.get(key), str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,100}', credentials[key])
        for key in ('api_token', 'password', 'session_secret')
    ):
        raise ValueError('invalid ephemeral credentials')
    if not args.mock:
        with urllib.request.urlopen(args.endpoint.rstrip('/') + '/api/ps', timeout=5) as response:
            if json.load(response).get('models') != []:
                raise ReviewSetupError('model_endpoint_occupied')
    os.chdir(project)
    sys.path.insert(0, str(project / 'src'))
    state = project / 'data' / 'coordination' / ('review-' + args.session_id)
    os.environ['SHANA_DASHBOARD_CREDENTIALS_FILE'] = ''
    os.environ.update({
        'SHANA_PROJECT_ROOT': str(project), 'GAMMA_ISOLATED_COORDINATION': '1',
        'GAMMA_COORDINATION_STATE_DIR': str(state),
        'GAMMA_COORDINATION_REVIEW_MODE': 'mock' if args.mock else 'real',
        'GAMMA_COORDINATION_REVIEW_PREFIX': args.review_prefix,
        'SHANA_API_AUTH_ENABLED': 'true', 'SHANA_API_BEARER_TOKEN': credentials['api_token'],
        'SHANA_DASHBOARD_AUTH_ENABLED': 'true', 'SHANA_DASHBOARD_AUTH_USERNAME': 'isolated-owner',
        'SHANA_DASHBOARD_AUTH_PASSWORD': credentials['password'],
        'SHANA_DASHBOARD_SESSION_SECRET': credentials['session_secret'],
        'SHANA_DASHBOARD_COOKIE_SECURE': 'false', 'SHANA_LLM_PROVIDER': 'mock' if args.mock else 'local',
        'SHANA_LLM_ROUTER_ENABLED': 'false', 'SHANA_LOCAL_LLM_ENDPOINT': args.endpoint,
        'SHANA_LOCAL_LLM_MODEL': args.model, 'SHANA_LOCAL_LLM_ENABLE_ROUTING': 'false',
        'SHANA_LOCAL_LLM_KEEP_ALIVE': '0', 'SHANA_LOCAL_LLM_TIMEOUT_SECONDS': '90',
        'GAMMA_PRACTICE_MODEL': args.model,
        'SHANA_DATABASE_URL': 'sqlite:///' + str(state / 'conversation.sqlite3'),
    })
    if args.private_voice:
        os.environ.update({'SHANA_STT_PROVIDER':'faster-whisper','SHANA_STT_MODEL':'base.en',
            'SHANA_STT_DEVICE':'cpu','SHANA_STT_COMPUTE_TYPE':'int8',
            'SHANA_TTS_PROVIDER':'qwen-tts','SHANA_TTS_PROFILE':args.voice_profile,
            'SHANA_AUDIO_OUTPUT_DIR':str(state/'provider-audio')})
    if args.screen_awareness:
        os.environ.update({'SHANA_LOCAL_LLM_SUPPORTS_VISION':'true',
                           'SHANA_LOCAL_LLM_VISION_MODEL':args.vision_model or args.model})
    # Configuration creates its output directories. Check freshness before an
    # explicitly session-local audio directory can create the state parent.
    from gamma.coordination.store import private_path
    private_path(state)
    if state.exists():
        raise ValueError('a review session must use fresh state')
    from gamma.config import settings
    from gamma.coordination.app import create_app
    from gamma.coordination.dashboard import create_app as dashboard_app
    from gamma.coordination.hosting import loopback_server
    from gamma.coordination.store import private_path
    import gamma
    assert Path(gamma.__file__).resolve().is_relative_to(project / 'src')
    backend = create_app()
    with loopback_server(backend) as api:
        if args.mock:
            def no_inference(*_args, **_kwargs):
                raise RuntimeError('practice inference is unavailable in mock review')
            backend.state.core.runner.propose = no_inference
        os.environ['GAMMA_COORDINATION_API_ORIGIN'] = api
        settings.shana_port = int(api.rsplit(':', 1)[1])
        with loopback_server(dashboard_app(), args.dashboard_port) as dashboard:
            ready_at = time.time()
            deadline = time.monotonic() + args.duration
            ready = {'phase': 'ready', 'scope': 'isolated_owner_private', 'session_id': args.session_id,
                              'dashboard': dashboard + args.review_prefix, 'review_prefix':args.review_prefix,
                              'pid': os.getpid(), 'source': str(Path(gamma.__file__).resolve()),
                              'state': str(state), 'model': args.model, 'mock': args.mock,
                              'private_voice':args.private_voice,'voice_profile':args.voice_profile if args.private_voice else None}
            if args.review_prefix:
                ready['proxy_receipt'] = write_proxy_receipt(project, state, args.review_prefix,
                    args.dashboard_port, backend.state.core, Path(gamma.__file__).resolve(), ready_at,
                    ready_at + args.duration)
            print(json.dumps(ready), flush=True)
            try:
                while time.monotonic() < deadline:
                    if select.select([sys.stdin], [], [], min(1, max(0, deadline-time.monotonic())))[0]:
                        command = sys.stdin.readline()
                        if not command or command.strip() == 'stop':
                            break
            finally:
                core = backend.state.core
                core.control(uuid.uuid4().hex, 'sleep')
                with core.store.transaction() as db:
                    unfinished = [a['id'] for a in core.store.list(db, 'activity', None)
                                  if a['status'] not in {'completed', 'blocked', 'needs_checker', 'stopped'}]
                for identifier in unfinished:
                    try:
                        core.control(uuid.uuid4().hex, 'pause', run_id=identifier)
                    except ValueError:
                        pass  # A worker may have just reached its terminal boundary.
    print(json.dumps({'phase': 'closed', 'session_id': args.session_id}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        code = exc.code if isinstance(exc, ReviewSetupError) else 'port_in_use' if isinstance(exc, OSError) and exc.errno == errno.EADDRINUSE else 'isolated_startup_failed'
        print(json.dumps({'phase': 'failed', 'error_type': type(exc).__name__, 'error_code': code}), flush=True)
        sys.exit(1)
