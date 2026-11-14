"""Finite actual-model companionship check from explicitly owner-reported scenes.

This proves the HTTP/dashboard/Core chain and observed output fences. It does not
claim a real OSRS capture, human companionship acceptance or Minecraft learning.
No catalog lookup, public output, account connection or raw screen capture occurs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid
import zipfile

from growth_probe_support import Host


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default='qwen3.8:27b')
    parser.add_argument('--endpoint', default='http://127.0.0.1:11434')
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    if os.name != 'posix' or project.name in {'gamma-main', 'gamma-modular-phase1'}:
        raise RuntimeError('Only the isolated Gamma candidate may run this check.')
    root = project / 'data/coordination/p6' / ('connected-' + uuid.uuid4().hex)
    root.mkdir(parents=True, mode=0o700)
    state = root / 'state'
    source_paths = sorted([*project.glob('src/gamma/coordination/shared_activity*.py'),
                           project / 'src/gamma/coordination/core.py',
                           project / 'src/gamma/coordination/channel_review.py',
                           project / 'src/gamma/coordination/app.py',
                           project / 'src/gamma/coordination/dashboard.py', Path(__file__)])
    def hashes():
        return {str(path.relative_to(project)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in source_paths}
    baseline = hashes()
    archive = root / 'executed-source.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as zipped:
        for path in source_paths:
            zipped.write(path, str(path.relative_to(project)))
    report = {'kind': 'p6_actual_model_owner_reported_scene_check', 'complete': False,
              'source_files': baseline, 'model': args.model, 'source_capture': 'owner_reported_fixture',
              'real_osrs_window_observed': False, 'real_game_action_assessed': False,
              'human_companionship_assessed': False, 'production_activated': False, 'cases': [],
              'executed_source_archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}
    host = None
    started = time.monotonic()
    try:
        host = Host(project, state, args.model, args.endpoint).start()
        report['host_pid'] = host.process.pid
        prefix = '/api/coordination/shared-activity'
        def get():
            return host.request('GET', prefix + '/status', params={'audience': 'owner_private'})
        def post(path, **values):
            return host.request('POST', prefix + path, json={
                'audience': 'owner_private', 'request_id': uuid.uuid4().hex, **values})
        assert host.client.get(prefix + '/status', params={'audience': 'public'}).status_code == 422
        assert host.client.post(prefix + '/minecraft/action', json={
            'audience': 'owner_private', 'request_id': 'no_world', 'name': 'attack', 'arguments': {}}).status_code == 422
        identity = host.request('GET', '/api/coordination/personal?audience=owner_private')['identity_id']
        post('/control', action='goal', values={'text': 'Reach 70 Fishing together. Relaxed company matters more than constant advice.'})
        post('/control', action='select', values={'source_id': 'reported_runelite', 'label': 'Owner-reported OSRS scene',
                                               'kind': 'osrs_screen', 'seconds': 180,
                                               'maximum_reviews': 3, 'review_interval_seconds': 10})
        epoch = get()['state']['source_epoch']
        def observe(sequence, text):
            observation = {'observation_id': 'reported_' + str(sequence), 'source_id': 'reported_runelite',
                           'source_epoch': epoch, 'sequence': sequence, 'captured_at': time.time(),
                           'kind': 'osrs_screen', 'summary': text, 'confidence': .9,
                           'content_sha256': hashlib.sha256(text.encode()).hexdigest()}
            post('/observe', observation=observation)
            return observation
        for sequence, name, text in [
                (0, 'quiet_grinding', 'The owner reports an unchanged fishing animation and no new visible event. They are focused on fishing.'),
                (1, 'shared_goal_event', 'The owner reports a Fishing level-up panel displaying level 70. This matches the previously stated shared goal.')]:
            observation = observe(sequence, text)
            rid = uuid.uuid4().hex
            before = time.monotonic()
            value = host.request('POST', prefix + '/review', json={'audience': 'owner_private', 'request_id': rid})
            case = {'kind': name, 'request_id': rid, 'observation': observation,
                    'elapsed_ms': round((time.monotonic() - before) * 1000, 3), 'receipt': value}
            with sqlite3.connect(state / 'coordination.sqlite3') as db:
                saved = db.execute("SELECT body FROM records WHERE kind='channel_review' AND id=?", (rid,)).fetchone()
            if saved:
                case['core_review_record'] = json.loads(saved[0])
            report['cases'].append(case)
            save(root / 'report.json', report)
            assert value['status'] in {'generated', 'silent', 'cancelled', 'deferred'}
            if value['status'] == 'cancelled':
                assert not value.get('text')
            assert host.request('GET', '/api/coordination/personal?audience=owner_private')['identity_id'] == identity
            # Honor the actual finite cadence; a new frame is not a budget reset.
            remaining = get()['state']['next_review_at'] - time.time()
            if remaining > 0:
                time.sleep(min(remaining + .05, 10.1))
        observation = observe(2, 'The owner reports a new bank view. The private screen source may be withdrawn at any moment.')
        rid = uuid.uuid4().hex
        results, errors = [], []
        def pending_review():
            try:
                results.append(host.request('POST', prefix + '/review', json={
                    'audience': 'owner_private', 'request_id': rid}))
            except Exception as exc:
                errors.append(type(exc).__name__)
        thread = threading.Thread(target=pending_review)
        thread.start()
        pending = False
        deadline = time.monotonic() + 10
        while thread.is_alive() and time.monotonic() < deadline:
            with sqlite3.connect(state / 'coordination.sqlite3') as db:
                row = db.execute("SELECT body FROM records WHERE kind='channel_review' AND id=?", (rid,)).fetchone()
            if row and json.loads(row[0])['status'] == 'pending':
                pending = True
                break
            time.sleep(.02)
        post('/control', action='stop', values={})
        thread.join(150)
        assert not thread.is_alive() and not errors and results
        assert results[0]['status'] in {'cancelled', 'deferred'} and not results[0].get('text')
        report['cases'].append({'kind': 'withdraw_source_during_actual_review', 'request_id': rid,
                                'observed_pending_model_request': pending, 'receipt': results[0]})
        assert not get()['state']['enabled'] and get()['context']['observation'] is None
        report.update(identity_id=identity, source_stable=hashes() == baseline,
                      complete=hashes() == baseline, semantic_review='Review recorded choices separately; no lexical oracle decides naturalness.')
    except BaseException as exc:
        report['error'] = {'type': type(exc).__name__, 'message': str(exc)[:500]}
        raise
    finally:
        if host:
            host.stop()
            report['temporary_host_absent'] = host.process.poll() is not None
        report['elapsed_seconds'] = round(time.monotonic() - started, 3)
        save(root / 'report.json', report)
        print(json.dumps({'report': str(root / 'report.json'), 'complete': report['complete'],
                          'cases': [{key: row[key] for key in ('kind', 'elapsed_ms', 'receipt') if key in row}
                                    for row in report['cases']], 'error': report.get('error')}))


if __name__ == '__main__':
    main()
