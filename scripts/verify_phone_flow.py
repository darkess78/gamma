"""Opt-in real local model and authenticated dashboard-to-phone transport check.

The two clients below are HTTP test clients, not a physical Android phone or a
human notification/listening acceptance. No external messages or push service.
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

import httpx

from growth_probe_support import Host


def sources(project):
    paths = sorted(p.relative_to(project).as_posix() for folder in ('src/gamma', 'scripts')
                   for p in (project / folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts
                   and p.suffix in {'.py', '.pyw', '.html', '.css', '.js', '.cjs', '.json', '.md'})
    return {p: hashlib.sha256((project / p).read_bytes()).hexdigest() for p in paths}


def record(state, kind, identifier):
    path = state / 'coordination.sqlite3'
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        row = db.execute('SELECT body FROM records WHERE kind=? AND id=?', (kind, identifier)).fetchone()
        return json.loads(row[0]) if row else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-real-model', action='store_true', help='Explicitly execute local model requests in a fresh isolated host')
    parser.add_argument('--model', default='qwen3.8:27b')
    parser.add_argument('--endpoint', default='http://127.0.0.1:11434')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not args.run_real_model:
        parser.error('Use --run-real-model for this finite actual inference and delivery test.')
    project = Path(__file__).resolve().parents[1]
    if project.name != 'gamma-coordination-core-20261023':
        raise RuntimeError('Run only on the isolated Gamma candidate, never production or Windows.')
    root = args.output or project / 'data/coordination/remaining-20261027/p8' / ('real-' + uuid.uuid4().hex)
    root = root.resolve()
    if not root.is_relative_to(project / 'data/coordination') or root.exists():
        raise ValueError('Choose a fresh output directory inside the candidate coordination data.')
    root.mkdir(parents=True, mode=0o700)
    state = root / 'state'
    before = time.monotonic()
    baseline = sources(project)
    archive = root / 'executed-source.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, digest in baseline.items():
            raw = (project / name).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == digest
            bundle.writestr(name, raw)
    report = {'kind': 'actual_dashboard_phone_flow', 'complete': False,
              'author': getpass.getuser() + '@' + socket.gethostname(),
              'physical_android': False, 'human_acknowledgment': False, 'public_output': False,
              'external_push': False, 'model': args.model, 'source_hashes_before': baseline,
              'executed_source_archive': {'path': archive.name, 'files': len(baseline),
                                          'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()},
              'limitations': ['HTTP clients represent desktop and phone; actual Android browser acceptance is separate.',
                              'Generic alerts require the page open; no background push or closed-phone delivery.',
                              'Semantic reminder choice is one observed example, not general social judgment.'], 'steps': []}
    host = None

    def save():
        (root / 'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

    def note(name, **values):
        report['steps'].append({'name': name, **values}); save()

    try:
        host = Host(project, state, args.model, args.endpoint).start()
        report['host_pid'] = host.process.pid
        with httpx.Client(base_url=host.ready['dashboard'], timeout=20) as anonymous:
            assert anonymous.get('/api/coordination/phone/summary?audience=owner_private').status_code == 401
        note('dashboard_session_authentication', unauthorized_status=401)
        prefix = '/api/coordination/phone'
        device_id, secret, epoch = 'phone-proof-' + uuid.uuid4().hex, secrets.token_urlsafe(32), None

        def post(operation, **values):
            body = {'audience': 'owner_private', 'request_id': uuid.uuid4().hex,
                    'device_id': device_id, 'device_secret': secret}
            if operation not in {'register', 'connect'}:
                body['epoch'] = epoch
            body.update(values)
            return host.request('POST', prefix + '/' + operation, json=body)

        post('register', label='Controlled Android-shaped HTTP client')
        epoch = post('connect')['device']['epoch']
        identity = post('state')['identity_id']
        page = host.client.get('/dashboard/phone')
        assert page.status_code == 200 and '/static/phone.js' in page.text
        assert host.client.get('/static/phone.js').status_code == 200
        assert host.client.get('/static/phone.css').status_code == 200
        note('phone_registration_and_page', identity_id=identity, epoch=epoch, page_status=page.status_code,
             device_secret_recorded=False)
        desktop_id, desktop_reply = host.chat('We are sorting workshop notes at the blue desk. Keep that setting in mind for this conversation.', 'monitor-private')
        input_id = uuid.uuid4().hex
        question = 'What color was the desk we said we were sorting notes at?'
        phone_reply = post('input', request_id=input_id, text=question)
        assert phone_reply['status'] == 'completed', phone_reply
        text = phone_reply.get('response', {}).get('display_text', '')
        assert 'blue' in text.lower(), text
        history = post('conversation')
        assert history['identity_id'] == identity
        assert {desktop_id, input_id} <= {r['id'] for r in history['turns']}
        same = post('input', request_id=input_id, text=question)
        assert same == phone_reply
        note('desktop_to_phone_continuity', desktop_request_id=desktop_id, phone_request_id=input_id,
             desktop_response=desktop_reply, phone_response=phone_reply,
             actual_history_request_ids=[r['id'] for r in history['turns']], duplicate_same_receipt=True)
        memory = host.request('POST', '/api/coordination/personal', json={'audience': 'owner_private',
            'request_id': uuid.uuid4().hex, 'action': 'memory', 'values': {'op': 'remember', 'kind': 'commitment',
                'subject_id': 'owner', 'topic': 'workshop-proof',
                'text': 'Review the three workshop notes after this rehearsal. This is unfinished.'}})['id']
        host.request('POST', '/api/coordination/control', json={'audience': 'owner_private',
            'request_id': uuid.uuid4().hex, 'action': 'focus', 'focus': 'relaxed'})
        now = time.time()
        post('schedule', commitment_id=memory, due_at=now + 2, expires_at=now + 600)
        post('control', action='contact_policy', value={'enabled': True, 'authority_expires_at': now + 1800,
            'quiet_until': 0, 'minimum_interval': 900, 'maximum_per_day': 3})
        deadline = time.monotonic() + 300
        offered = None
        while time.monotonic() < deadline:
            post('state')
            result = post('poll')
            if result.get('message'):
                offered = result; break
            time.sleep(2)
        assert offered is not None, 'No actual useful reminder was offered within the bounded review window.'
        message = offered['message']
        assert offered['notification'] == {'title': 'Shana', 'body': 'You have a private update.', 'content_included': False}
        assert post('poll')['message'] is None
        received = post('receipt', message_id=message['id'], status='received')
        acknowledged = post('receipt', message_id=message['id'], status='acknowledged')
        assert received['status'] == 'received' and acknowledged['status'] == 'acknowledged'
        assert record(state, 'personal_record', memory)['status'] == 'active'
        note('actual_considered_reminder_and_client_ack', commitment_id=memory, received_message=message,
             received_receipt=received, acknowledged_receipt=acknowledged, commitment_still_active=True,
             generic_notification=offered['notification'], automatic_second_offer=False)
        post('control', action='disconnect')
        old = host.client.post(prefix + '/state', json={'audience': 'owner_private', 'request_id': uuid.uuid4().hex,
                              'device_id': device_id, 'device_secret': secret, 'epoch': epoch})
        assert old.status_code == 400
        previous_epoch = epoch
        epoch = post('connect')['device']['epoch']
        assert epoch > previous_epoch and post('state')['identity_id'] == identity
        assert post('poll')['message'] is None
        assert record(state, 'phone_message', message['id'])['status'] == 'acknowledged'
        note('offline_reconnect_and_no_nag', disconnected_status=old.status_code, old_epoch=previous_epoch,
             new_epoch=epoch, identity_preserved=True, acknowledged_exchange_not_repeated=True)
        host.request('POST', '/api/coordination/control', json={'audience': 'owner_private',
            'request_id': uuid.uuid4().hex, 'action': 'sleep'})
        sleeping_id = uuid.uuid4().hex
        queued = post('input', request_id=sleeping_id, text='When you wake, what is our workshop setting?')
        assert queued['status'] == 'queued'
        host.request('POST', '/api/coordination/control', json={'audience': 'owner_private',
            'request_id': uuid.uuid4().hex, 'action': 'wake'})
        deadline = time.monotonic() + 180
        result = queued
        while time.monotonic() < deadline:
            post('state')
            result = post('result', input_request_id=sleeping_id)
            if result['status'] == 'completed':
                break
            time.sleep(2)
        assert result['status'] == 'completed', result
        note('sleep_wake_catch_up', request_id=sleeping_id, queued_receipt=queued, final_receipt=result,
             no_second_input_request=True)
        host.request('POST', '/api/coordination/personal', json={'audience': 'owner_private',
            'request_id': uuid.uuid4().hex, 'action': 'forget', 'values': {'target_id': memory}})
        raw = record(state, 'phone_message', message['id'])
        assert raw['text'] == '' and raw['status'] == 'cancelled'
        note('source_removal_physically_erases_derived_phone_text', message_id=message['id'],
             payload_erased=True, observed_before_any_phone_state_refresh=True)
        report['source_hashes_after'] = sources(project)
        report['source_stable'] = baseline == report['source_hashes_after']
        assert report['source_stable'], 'Sources changed while the host ran; preserve this diagnostic and rerun only after review.'
        report['complete'] = True
    except Exception as exc:
        report['error'] = {'type': type(exc).__name__, 'message': str(exc)[:1000]}
        raise
    finally:
        if host:
            host.stop()
            report['host_absent'] = host.process.poll() is not None
        report['seconds'] = round(time.monotonic() - before, 3)
        save()
        print(json.dumps({'report': str(root / 'report.json'), 'complete': report['complete'],
                          'physical_android': False, 'seconds': report['seconds']}))


if __name__ == '__main__':
    main()
