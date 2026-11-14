"""Four finite actual-Core decisions on reconstructed historical stream context.

Default is inert. This is not a replay of the original byte-identical prompts or
a new stream transport, browser, hearing, public-hosting or four-hour result.
Read the received responses against the supplied evidence independently.
"""
import argparse
from contextlib import closing
from copy import deepcopy
import getpass
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import time
from urllib.parse import urlsplit
import urllib.request
import uuid
import zipfile


FIXTURE = 'tests/fixtures/stream_grounding_replay.json'
MAX_CALLS = 12
RULES = (
    'Your earlier commentary, intentions and promises are not execution evidence.',
    'Do not promise to run, edit, test or complete work from this observation.',
    'Limit each completion or correctness claim to the exact supplied task, artifact and executed probe result.',
    'An omitted result is unknown, not missing or failed; inspect the supplied output before claiming it is absent.',
    'Read its reported task labels and output_field_present values literally; earlier commentary cannot replace those labels or make a supplied JSON output missing.',
    'A reported status or check label is not independent verification. Distinguish supplied output from whether its correctness has been independently checked.',
    'When current task records are supplied, unbound prior commentary text is deliberately omitted for this review; its receipt metadata remains.',
)


def source_files(project):
    files = [path for path in (project / 'src/gamma').rglob('*') if path.is_file()
             and '__pycache__' not in path.parts and path.suffix in {'.py', '.js', '.html', '.css', '.json', '.md'}]
    files.extend(path for path in (project / 'config').iterdir() if path.is_file()
                 and path.suffix in {'.toml', '.yaml', '.json'} and not path.name.endswith('.local.toml'))
    files.extend(project / name for name in (FIXTURE, 'scripts/verify_stream_grounding.py'))
    return {path.relative_to(project).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(files)}


def source_rows(state):
    database = (state / 'coordination.sqlite3').resolve()
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
        return {kind: count for kind, count in db.execute('SELECT kind,COUNT(*) FROM records GROUP BY kind')}


def embedded_text_present(text, needle):
    forms = {needle}
    for _ in range(3):
        forms.update(json.dumps(value, ensure_ascii=ascii_only)[1:-1]
                     for value in tuple(forms) for ascii_only in (False, True))
    return any(value in text for value in forms)


def prepare(project, output, endpoint):
    parsed = urlsplit(endpoint)
    if (os.name != 'posix' or project.name != 'gamma-coordination-core-20261023'
            or Path.cwd().resolve() != project or parsed.scheme != 'http'
            or parsed.hostname != '127.0.0.1' or parsed.port != 11434
            or parsed.path not in {'', '/'} or parsed.username or parsed.password or parsed.query or parsed.fragment
            or any((project / name).exists() for name in ('.env', 'config/app.local.toml', 'config/voices.local.toml'))):
        raise RuntimeError('Run only on the portable isolated Gamma candidate and its existing local model.')
    from gamma.coordination.store import private_path
    supplied = Path(output)
    if not supplied.is_absolute():
        raise ValueError('Use an absolute fresh candidate coordination output path.')
    root = private_path(supplied)
    if not root.is_relative_to(project / 'data/coordination') or root.exists() or not root.parent.is_dir():
        raise ValueError('Use a fresh output with an existing parent inside candidate data/coordination.')
    raw = (project / FIXTURE).read_bytes()
    fixture = json.loads(raw)
    if len(raw) > 32_000 or {case['event_index'] for case in fixture['cases']} != {15, 18, 22, 25} or len(fixture['cases']) != 4:
        raise ValueError('Unexpected bounded historical fixture.')
    for case in fixture['cases']:
        if (case['event']['kind'] not in {'activity', 'lull'} or len(case['event']['text']) > 1200
                or len(case['reconstructed_recent']) > 6 or len(fixture['broad_plan']) > 1000):
            raise ValueError('Oversized or unsupported reconstructed observation.')
    return root, fixture, hashlib.sha256(raw).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-real-model', action='store_true')
    parser.add_argument('--model', default='qwen3.8:27b')
    parser.add_argument('--endpoint', default='http://127.0.0.1:11434')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    if not args.run_real_model:
        print(json.dumps({'action': 'inert', 'real_model_calls': 0, 'cases_prepared': 4,
                          'requires': '--run-real-model and a fresh absolute --output'}))
        return 0
    if args.output is None:
        parser.error('--run-real-model requires --output')
    project = Path(__file__).resolve().parents[1]
    root, fixture, fixture_sha = prepare(project, args.output, args.endpoint)
    root.mkdir(mode=0o700)
    state = root / 'state'
    state.mkdir(mode=0o700)
    token = secrets.token_urlsafe(32)
    os.environ.update({'SHANA_PROJECT_ROOT': str(project), 'GAMMA_ISOLATED_COORDINATION': '1',
        'GAMMA_COORDINATION_STATE_DIR': str(state), 'SHANA_API_AUTH_ENABLED': 'true',
        'SHANA_API_BEARER_TOKEN': token, 'SHANA_LLM_PROVIDER': 'local', 'SHANA_LLM_ROUTER_ENABLED': 'false',
        'SHANA_LOCAL_LLM_ENDPOINT': args.endpoint, 'SHANA_LOCAL_LLM_MODEL': args.model,
        'SHANA_LOCAL_LLM_ENABLE_ROUTING': 'false', 'SHANA_LOCAL_LLM_KEEP_ALIVE': '0',
        'SHANA_LOCAL_LLM_TIMEOUT_SECONDS': '120', 'GAMMA_PRACTICE_MODEL': args.model,
        'SHANA_DATABASE_URL': 'sqlite:///' + str(state / 'conversation.sqlite3')})
    # Configuration is imported only after the fresh process environment is set.
    # No machine-local configuration, provider, safety filter or model is replaced.
    from fastapi.testclient import TestClient
    from gamma.config import settings
    from gamma.coordination.app import configured_app
    from gamma.coordination.stream_session import public_session_facts
    from gamma.coordination.stream_session_models import RehearsalEvent
    if (settings.project_root.resolve() != project or settings.llm_provider not in {'local', 'ollama'}
            or settings.llm_router_enabled or settings.local_llm_enable_routing
            or settings.local_llm_endpoint.rstrip('/') != args.endpoint.rstrip('/')
            or settings.local_llm_model != args.model):
        raise RuntimeError('Effective local configured adapter does not match the bounded replay.')
    baseline = source_files(project)
    archive = root / 'executed-source.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, expected in baseline.items():
            raw = (project / name).read_bytes()
            if hashlib.sha256(raw).hexdigest() != expected:
                raise RuntimeError('Source changed before archival.')
            bundle.writestr(name, raw)
    archive.chmod(0o600)
    report = {'kind': 'actual_configured_core_reconstructed_stream_grounding', 'collection_complete': False,
        'author': getpass.getuser() + '@' + socket.gethostname(), 'model': args.model,
        'fixture_sha256': fixture_sha, 'reconstruction_limit': fixture['note'],
        'source_hashes_before': baseline, 'executed_source_archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
        'safety_review_enabled': settings.speech_filter_llm_enabled,
        'semantic_acceptance': 'requires_independent_review', 'maximum_provider_dispatches': MAX_CALLS,
        'direct_core_review': True, 'new_stream_transport_or_browser_delivery': False,
        'human_hearing': False, 'production_activated': False, 'public_output': False,
        'meaningful_four_hour_hosting_accepted': False, 'cases': [], 'provider_dispatches': []}
    original = urllib.request.urlopen
    active = None
    started = time.monotonic()
    def save():
        target = root / 'report.json'
        target.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        target.chmod(0o600)
    def observed(request, *values, **options):
        url = urlsplit(request.full_url if isinstance(request, urllib.request.Request) else str(request))
        if (not isinstance(request, urllib.request.Request) or request.get_method() != 'POST'
                or url.scheme != 'http' or url.hostname != '127.0.0.1' or url.port != 11434
                or url.path != '/api/generate' or url.username or url.password or url.query or url.fragment
                or len(report['provider_dispatches']) >= MAX_CALLS):
            raise RuntimeError('Unexpected provider route or exhausted finite replay budget.')
        payload = json.loads(request.data)
        system = payload.get('system', '')
        primary = '# Private Coordination Context' in system
        historical = next((case for case in fixture['cases'] if case['event_index'] == active), None)
        body = json.dumps(payload, ensure_ascii=False)
        entry = {'case_event_index': active, 'primary_private_turn': primary,
            'system_sha256': hashlib.sha256(system.encode()).hexdigest(),
            'system_characters': len(system),
            'request_sha256': hashlib.sha256(request.data).hexdigest(), 'model': payload.get('model'),
            'matched_grounding_rules': [i for i, rule in enumerate(RULES) if rule in system],
            'commentary_provenance_present': 'consumer_reported_text_received_only' in system,
            'parsed_caller_data_present': '"source_kind": "caller_supplied_current_event_data"' in system,
            'provided_probe_output_field_present': '"output_field_present": true' in system,
            'current_task_labels_present': {task: '"reported_task": "' + task + '"' in system
                                            for task in ('notes', 'notes_filter')},
            'unbound_commentary_omission_present': '"reason": "unbound_to_current_reported_artifacts"' in system,
            'historical_commentary_text_present': bool(historical and any(embedded_text_present(body, row['text'])
                for row in historical['reconstructed_recent'])),
            'outcome': 'submitted', 'started_at': time.time()}
        report['provider_dispatches'].append(entry)
        save()
        try:
            result = original(request, *values, **options)
            entry['outcome'] = 'http_response_opened'
            return result
        except BaseException:
            entry['outcome'] = 'request_raised'
            raise
        finally:
            entry['elapsed_seconds'] = round(time.time() - entry['started_at'], 3)
            save()
    save()
    try:
        urllib.request.urlopen = observed
        app = configured_app(project, state, bearer_token=token, run_background=False,
                             identity_lock_root=root / 'identity-locks')
        with TestClient(app) as client:
            assert client.get('/health').status_code == 401
            assert client.get('/health', headers={'Authorization': 'Bearer ' + token}).status_code == 200
            core = app.state.core
            identity = core.personal_state()['identity_id']
            for case in fixture['cases']:
                active = case['event_index']
                now = time.time()
                recent = deepcopy(case['reconstructed_recent'])
                for row in recent:
                    row['received_at'] = now - (case['event_elapsed'] - row['received_at'])
                event = RehearsalEvent(audience='owner_private', event_id='historical-' + str(active),
                    session_id='stream-' + '1' * 32, consumer_id='historical-reconstruction',
                    kind=case['event']['kind'], source='replay', observed_at=now,
                    text=case['event']['text'], actor_alias='AuthoredTestViewer')
                selected = app.state.stream_session._sanitized(event)
                facts = public_session_facts({'plan': {'format': 'coding', 'broad_plan': fixture['broad_plan']},
                    'owner_present': case['owner_present'], 'focused': case['focused']}, selected, recent)
                before = time.monotonic()
                response = core.review_channel(facts, 'historical-grounding-' + str(active))
                row = {'event_index': active, 'facts': facts, 'response': response.model_dump(),
                       'seconds': round(time.monotonic() - before, 3)}
                report['cases'].append(row)
                assert any(entry['case_event_index'] == active and entry['primary_private_turn']
                           and entry['matched_grounding_rules'] == list(range(len(RULES)))
                           and entry['commentary_provenance_present'] for entry in report['provider_dispatches'])
                if active in {18, 25}:
                    assert any(entry['case_event_index'] == active and entry['primary_private_turn']
                               and entry['parsed_caller_data_present'] and entry['provided_probe_output_field_present']
                               and all(entry['current_task_labels_present'].values())
                               and entry['unbound_commentary_omission_present']
                               and not entry['historical_commentary_text_present']
                               for entry in report['provider_dispatches'])
                assert core.personal_state()['identity_id'] == identity
                with core.store.transaction() as db:
                    assert core.store.list(db, 'turn', None) == []
                    assert core.store.list(db, 'work_request', None) == []
                    assert core.store.list(db, 'activity', None) == []
                save()
        report['owner_closed'] = core.store.read('brain', 'state')['owner'] == ''
        assert report['owner_closed']
        report['record_counts_after_close'] = source_rows(state)
        report['source_hashes_after'] = source_files(project)
        report['source_stable'] = report['source_hashes_after'] == baseline
        assert report['source_stable']
        report['collection_complete'] = True
    except BaseException as exc:
        report['error_type'] = type(exc).__name__
        raise
    finally:
        urllib.request.urlopen = original
        report['seconds'] = round(time.monotonic() - started, 3)
        save()
        print(json.dumps({'report': str(root / 'report.json'), 'collection_complete': report['collection_complete'],
            'seconds': report['seconds'], 'semantic_acceptance': report['semantic_acceptance']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
