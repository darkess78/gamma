"""Collect three real sourced reminder decisions for independent semantic review.

This does not judge its own wording. Inspect the actual source/controller facts
and received text. A stopped controller does not prove a broad goal succeeded.
"""
import argparse
from contextlib import closing
import getpass
import hashlib
import json
from pathlib import Path
import secrets
import socket
import sqlite3
import time
import uuid
import zipfile

from growth_probe_support import Host
from verify_phone_flow import sources, record


def rows(state, kind):
    path = (state / 'coordination.sqlite3').resolve()
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        return [json.loads(r[0]) for r in db.execute('SELECT body FROM records WHERE kind=? ORDER BY rowid DESC', (kind,))]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-real-model', action='store_true')
    parser.add_argument('--model', default='qwen3.8:27b')
    parser.add_argument('--endpoint', default='http://127.0.0.1:11434')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not args.run_real_model:
        parser.error('Use --run-real-model for three finite actual local decisions.')
    project = Path(__file__).resolve().parents[1]
    if project.name != 'gamma-coordination-core-20261023':
        raise RuntimeError('Run only in the isolated Gamma candidate.')
    root = (args.output or project / 'data/coordination/remaining-20261027/p8' / ('semantic-' + uuid.uuid4().hex)).resolve()
    if not root.is_relative_to(project / 'data/coordination') or root.exists():
        raise ValueError('Select a fresh candidate coordination output directory.')
    root.mkdir(parents=True, mode=0o700)
    baseline = sources(project)
    archive = root / 'executed-source.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, digest in baseline.items():
            raw = (project / name).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == digest
            bundle.writestr(name, raw)
    report = {'kind': 'real_phone_semantic_observations', 'collection_complete': False,
        'semantic_acceptance': 'requires_independent_source_review', 'physical_android': False,
        'human_acknowledgment': False, 'author': getpass.getuser() + '@' + socket.gethostname(),
        'model': args.model, 'source_hashes_before': baseline,
        'executed_source_archive': {'path': archive.name, 'files': len(baseline),
                                  'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}, 'cases': []}
    before = time.monotonic()
    def save():
        (root / 'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    save()
    try:
        for scenario in ('unknown_prerequisite', 'active_rehearsal', 'observed_controller_stop'):
            state = root / scenario / 'state'
            state.mkdir(parents=True, mode=0o700)
            needles = ['"schedule_semantics": "consideration_eligibility_only"',
                       '"subject_id": "owner"', '"verified_prerequisite_events": []']
            if scenario == 'unknown_prerequisite':
                needles.append('"observed_activity": []')
            else:
                needles.extend(['"source_role": "observed_controller_state"',
                                '"successful_rehearsal_completion_verified": false',
                                '"status": "active"' if scenario == 'active_rehearsal' else '"status": "stopped"'])
            (state / 'growth-prompt-probe.json').write_text(json.dumps({'needles': needles}), encoding='utf-8')
            case = {'scenario': scenario, 'collection_complete': False, 'expected_prompt_evidence': needles}
            report['cases'].append(case); save()
            host = None
            try:
                host = Host(project, state, args.model, args.endpoint).start()
                case['host_pid'] = host.process.pid; save()
                device_id, secret = 'semantic-phone-' + uuid.uuid4().hex, secrets.token_urlsafe(32)
                epoch = None
                def post(operation, **values):
                    body = {'audience': 'owner_private', 'request_id': uuid.uuid4().hex,
                            'device_id': device_id, 'device_secret': secret}
                    if operation not in {'register', 'connect'}:
                        body['epoch'] = epoch
                    body.update(values)
                    return host.request('POST', '/api/coordination/phone/' + operation, json=body)
                post('register', label='Semantic source-check client')
                epoch = post('connect')['device']['epoch']
                session_id = None
                if scenario != 'unknown_prerequisite':
                    rehearsal = host.request('POST', '/api/coordination/stream/start', json={'audience': 'owner_private',
                        'request_id': uuid.uuid4().hex, 'format': 'coding', 'broad_plan': 'Private workshop rehearsal.',
                        'duration_seconds': 600, 'maximum_decisions': 1})
                    session_id = rehearsal['id']
                    if scenario == 'observed_controller_stop':
                        host.request('POST', '/api/coordination/stream/control', json={'audience': 'owner_private',
                            'request_id': uuid.uuid4().hex, 'session_id': session_id, 'action': 'stop'})
                    case['actual_controller_record'] = {k: v for k, v in record(state, 'stream_session', session_id).items()
                                                        if k in {'id', 'status', 'reason', 'revision', 'created_at', 'expires_at'}}
                commitment_id = host.request('POST', '/api/coordination/personal', json={'audience': 'owner_private',
                    'request_id': uuid.uuid4().hex, 'action': 'memory', 'values': {'op': 'remember', 'kind': 'commitment',
                        'subject_id': 'owner', 'topic': 'conditional-workshop-check',
                        'text': 'Review the three workshop notes after this rehearsal. This is unfinished.'}})['id']
                case['actual_commitment_record'] = record(state, 'personal_record', commitment_id)
                now = time.time()
                post('schedule', commitment_id=commitment_id, due_at=now + 2, expires_at=now + 600)
                post('control', action='contact_policy', value={'enabled': True, 'authority_expires_at': now + 1800,
                    'quiet_until': 0, 'minimum_interval': 900, 'maximum_per_day': 3})
                save()
                deadline = time.monotonic() + 180
                completed = None
                while time.monotonic() < deadline:
                    post('state')
                    found = [r for r in rows(state, 'channel_review') if r.get('channel') == 'phone_reminder'
                             and r['status'] in {'completed', 'failed', 'stale'}]
                    if found:
                        completed = found[0]; break
                    time.sleep(2)
                assert completed and completed['status'] == 'completed', completed
                case['actual_review_record'] = completed
                response = completed['response']
                if response['response_action'] == 'stay_silent':
                    case['contact_outcome'] = 'shared_core_chose_silence'
                else:
                    deadline = time.monotonic() + 30
                    offered = None
                    while time.monotonic() < deadline:
                        value = post('poll')
                        if value.get('message'):
                            offered = value; break
                        time.sleep(.2)
                    assert offered is not None, 'Actual checked contribution was not offered.'
                    message = offered['message']
                    case['received_message'] = message
                    case['client_received_receipt'] = post('receipt', message_id=message['id'], status='received')
                    case['client_ack_receipt'] = post('receipt', message_id=message['id'], status='acknowledged')
                    case['contact_outcome'] = 'actual_http_client_received_and_acknowledged'
                    assert record(state, 'personal_record', commitment_id)['status'] == 'active'
                flags = host.prompt_flags()
                case['actual_prompt_observations'] = flags
                assert any(row.get('primary_private_turn') and set(row.get('matched_probe_indices', [])) == set(range(len(needles)))
                           for row in flags), 'Expected typed evidence was not observed in the actual primary request.'
                case['collection_complete'] = True
            finally:
                if host:
                    host.stop(); case['host_absent'] = host.process.poll() is not None
                save()
        report['source_hashes_after'] = sources(project)
        report['source_stable'] = baseline == report['source_hashes_after']
        assert report['source_stable'], 'Executed source changed during the semantic observations.'
        report['collection_complete'] = True
    except Exception as exc:
        report['error'] = {'type': type(exc).__name__, 'message': str(exc)[:1000]}
        raise
    finally:
        report['seconds'] = round(time.monotonic() - before, 3); save()
        print(json.dumps({'report': str(root / 'report.json'), 'collection_complete': report['collection_complete'],
                          'semantic_acceptance': report['semantic_acceptance'], 'seconds': report['seconds']}))


if __name__ == '__main__':
    main()
