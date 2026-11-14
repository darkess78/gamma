"""Inert release preparation or a finite loopback rehearsal; never changes services."""
import argparse
import json
import os
from pathlib import Path
import select
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action')
    prepare = sub.add_parser('prepare')
    for name in ('source', 'state', 'baseline', 'reader', 'lock-root', 'output'):
        prepare.add_argument('--' + name, required=True, type=Path)
    prepare.add_argument('--model', required=True)
    prepare.add_argument('--voice', required=True)
    prepare.add_argument('--original-lock-root',required=True,type=Path,action='append')
    validate = sub.add_parser('validate')
    validate.add_argument('--manifest', required=True, type=Path)
    validate.add_argument('--purpose', choices=('permanent', 'test'), default='permanent')
    serve = sub.add_parser('serve-test')
    serve.add_argument('--manifest', required=True, type=Path)
    serve.add_argument('--duration', type=int, default=300)
    serve.add_argument('--api-port', type=int, default=0)
    serve.add_argument('--dashboard-port', type=int, default=0)
    serve.add_argument('--mock', action='store_true', help='Explicit controlled provider; no inference is claimed')
    serve.add_argument('--endpoint', help='Explicit loopback model endpoint; mock default is unused discard port')
    args = parser.parse_args()
    if not args.action:
        print(json.dumps({'status':'inert', 'activation_performed':False,
                          'actions':['prepare','validate','serve-test']}))
        return
    project = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project / 'src'))
    if args.action in {'prepare', 'validate'}:
        from gamma.coordination.private_release_manifest import prepare_manifest, load_manifest, preflight, absolute_path
        if args.action == 'prepare':
            value = prepare_manifest(args.source, args.state, args.baseline, args.reader,
                                     args.lock_root, model=args.model, voice=args.voice,original_lock_roots=args.original_lock_root)
            target = absolute_path(str(args.output))
            target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            with target.open('x', encoding='utf-8') as stream:
                json.dump(value, stream, indent=2); stream.write('\n')
            target.chmod(0o600)
            print(json.dumps({'status':'prepared_not_approved','manifest':str(target),'activation_performed':False}))
        else:
            result = preflight(load_manifest(args.manifest), purpose=args.purpose)
            print(json.dumps(result))
            if not result['ok']:
                raise SystemExit(2)
        return
    if os.name != 'posix' or not 5 <= args.duration <= 1800:
        raise ValueError('finite_Gamma_rehearsal_duration_required')
    for port in (args.api_port, args.dashboard_port):
        if port != 0 and (not 1024 <= port <= 65535 or port in {8000,8001,8080,11434}):
            raise ValueError('finite_rehearsal_cannot_bind_production_ports')
    if args.api_port and args.api_port == args.dashboard_port:
        raise ValueError('release_roles_require_separate_ports')
    from gamma.coordination.private_release_manifest import load_manifest
    manifest = load_manifest(args.manifest)
    from urllib.parse import urlsplit
    endpoint=args.endpoint or ('http://127.0.0.1:9' if args.mock else 'http://127.0.0.1:11434')
    parsed=urlsplit(endpoint)
    if (parsed.scheme!='http' or parsed.hostname not in {'127.0.0.1','::1'}
        or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'','/'}):
        raise ValueError('explicit_loopback_model_endpoint_required')
    credentials = json.loads(sys.stdin.readline())
    import re
    if (not isinstance(credentials, dict) or set(credentials) != {'api_token','password','session_secret'}
        or any(not isinstance(x,str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,100}',x) for x in credentials.values())):
        raise ValueError('fresh_ephemeral_credentials_required_on_stdin')
    os.environ.update({'SHANA_PROJECT_ROOT':str(project),
        'GAMMA_PRIVATE_RELEASE_MANIFEST':str(args.manifest.resolve()), 'GAMMA_PRIVATE_RELEASE_PURPOSE':'test',
        'SHANA_API_AUTH_ENABLED':'true','SHANA_API_BEARER_TOKEN':credentials['api_token'],
        'SHANA_DASHBOARD_AUTH_ENABLED':'true','SHANA_DASHBOARD_AUTH_USERNAME':'private-release-owner',
        'SHANA_DASHBOARD_CREDENTIALS_FILE':'', 'SHANA_DASHBOARD_AUTH_PASSWORD':credentials['password'],
        'SHANA_DASHBOARD_SESSION_SECRET':credentials['session_secret'], 'SHANA_DASHBOARD_COOKIE_SECURE':'false',
        'SHANA_DATABASE_URL':'sqlite:///' + manifest['state']['active_root'] + '/conversation.sqlite3',
        'SHANA_LLM_PROVIDER':'mock' if args.mock else 'local','SHANA_LLM_ROUTER_ENABLED':'false',
        'SHANA_LOCAL_LLM_MODEL':manifest['models']['primary'], 'GAMMA_PRACTICE_MODEL':manifest['models']['primary'],
        'SHANA_LOCAL_LLM_VISION_MODEL':manifest['models']['vision'],
        'SHANA_LOCAL_LLM_ENDPOINT':endpoint, 'SHANA_LOCAL_LLM_ENABLE_ROUTING':'false',
        'SHANA_TTS_PROFILE':manifest['models']['voice'], 'SHANA_LOCAL_LLM_KEEP_ALIVE':'0'})
    os.chdir(project)
    from gamma.coordination.private_release import create_api_app, create_dashboard_app
    from gamma.coordination.hosting import loopback_server
    started = time.monotonic()
    with loopback_server(create_api_app(), args.api_port) as api:
        os.environ['GAMMA_PRIVATE_RELEASE_API_ORIGIN'] = api
        with loopback_server(create_dashboard_app(), args.dashboard_port) as dashboard:
            print(json.dumps({'phase':'ready', 'pid':os.getpid(), 'api':api, 'dashboard':dashboard,
                              'scope':'private_release_rehearsal', 'mock':args.mock,
                              'release_id':manifest['release_id'], 'maximum_duration_seconds':args.duration}),flush=True)
            while time.monotonic() - started < args.duration:
                if select.select([sys.stdin],[],[],min(1,max(0,args.duration-(time.monotonic()-started))))[0]:
                    command = sys.stdin.readline()
                    if not command or command.strip() == 'stop':
                        break
    print(json.dumps({'phase':'closed','pid':os.getpid(),'production_activation':False}),flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'phase':'blocked','error_type':type(exc).__name__,
                          'reason':str(exc)[:240], 'activation_performed':False}),flush=True)
        raise SystemExit(1)
