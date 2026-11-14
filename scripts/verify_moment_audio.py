"""Finite actual authored image -> same Core -> optional Qwen -> authenticated WAV.

No OSRS account, browser control, microphone, public output or human-hearing claim.
The real channel may choose quiet; that is recorded rather than replaced.
"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import threading
import time
import urllib.request
import uuid
import zipfile

from growth_probe_support import Host, source_files as runtime_sources
from verify_shared_selected_image import authored_png


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def source_files(project):
    files = runtime_sources(project)
    for name in ('verify_moment_audio.py', 'verify_shared_selected_image.py', 'verify_channel_audio.py',
                 'growth_probe_support.py', 'verify_growth_flow.py', 'verify_practical_work.py',
                 'verify_coordination_foundation.py', 'verify_coordination_slice.py'):
        relative = 'scripts/' + name
        files[relative] = hashlib.sha256((project / relative).read_bytes()).hexdigest()
    return dict(sorted(files.items()))


def child(project, state):
    original = urllib.request.urlopen
    lock = threading.Lock()
    requests = [0]
    def observed(request, *args, **kwargs):
        if isinstance(request, urllib.request.Request) and request.full_url.endswith('/api/generate') and request.data:
            body = json.loads(request.data)
            with lock:
                requests[0] += 1
                if requests[0] > 5:
                    raise RuntimeError('finite_moment_model_call_budget')
                with (state / 'moment-model-events.jsonl').open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps({'event': 'actual_http_dispatch', 'at': time.time(),
                        'model': body.get('model'), 'image_hashes': [hashlib.sha256(base64.b64decode(value)).hexdigest()
                            for value in body.get('images', [])], 'call_number': requests[0]}) + '\n')
        return original(request, *args, **kwargs)
    urllib.request.urlopen = observed
    from verify_channel_audio import child as existing_audio_child
    existing_audio_child(project, state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default='qwen3.8:27b')
    parser.add_argument('--endpoint', default='http://127.0.0.1:11434')
    parser.add_argument('--profile', default='qwen_shana_dataset_xvec_soft')
    parser.add_argument('--qwen-reference', type=Path)
    parser.add_argument('--child-state', type=Path)
    parser.add_argument('--run-real', action='store_true')
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    if os.name != 'posix' or project.name in {'gamma-main', 'gamma-modular-phase1'}:
        raise RuntimeError('Run only on an isolated Gamma candidate.')
    if args.endpoint != 'http://127.0.0.1:11434':
        raise RuntimeError('Use the existing local inference endpoint only.')
    if args.child_state:
        child(project, args.child_state)
        return
    if not args.run_real:
        print(json.dumps({'status': 'prepared_only', 'model_calls': 0, 'command': '--run-real',
            'physical_browser': False, 'human_hearing': False}))
        return
    root = project / 'data/coordination/p6' / ('actual-moment-audio-' + uuid.uuid4().hex)
    root.mkdir(parents=True, mode=0o700)
    os.environ.update(SHANA_LOCAL_LLM_SUPPORTS_VISION='true', SHANA_LOCAL_LLM_VISION_MODEL=args.model,
        SHANA_TTS_PROVIDER='qwen-tts', SHANA_TTS_PROFILE=args.profile, SHANA_AUDIO_OUTPUT_DIR=str(root / 'provider-audio'))
    if (not args.qwen_reference or not args.qwen_reference.is_absolute() or not args.qwen_reference.is_file()
            or args.qwen_reference.is_symlink() or args.qwen_reference.stat().st_size > 8 * 1024**2):
        raise RuntimeError('Real proof needs the existing readonly Qwen reference WAV; no asset copy.')
    os.environ.update(SHANA_TTS_PROFILE='', SHANA_QWEN_TTS_ENDPOINT='http://127.0.0.1:9882/tts',
        SHANA_QWEN_TTS_REFERENCE_AUDIO=str(args.qwen_reference), SHANA_QWEN_TTS_EXTRA_JSON='{"x_vector_only_mode":true}',
        SHANA_QWEN_TTS_LANGUAGE='English')
    before = source_files(project)
    archive = root / 'executed-source.zip'
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in before:
            bundle.write(project / name, name)
    report = {'kind': 'actual_historical_moment_checked_audio_http', 'complete': False,
        'author': os.environ.get('USER', 'neety') + '@' + socket.gethostname(),
        'model': args.model, 'voice_profile': None, 'existing_reference_sha256': hashlib.sha256(args.qwen_reference.read_bytes()).hexdigest(), 'authored_image': True, 'actual_osrs': False,
        'browser_control': False, 'physical_microphone': False, 'human_hearing': False, 'public_delivery': False,
        'receipt_meaning': 'private HTTP consumer received WAV bytes; no browser played or human-heard claim',
        'source_sha256': before, 'source_archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
        'received_wav': []}
    host = None
    started = time.monotonic()
    try:
        host = Host(project, root / 'state', args.model, args.endpoint)
        host.child_script = Path(__file__)
        host.start()
        report['host_pid'] = host.process.pid
        prefix = '/api/coordination'
        consumer = 'moment-proof-' + uuid.uuid4().hex[:12]
        def post(path, **values):
            return host.request('POST', prefix + path, json={'audience': 'owner_private', **values})
        def get(path, **values):
            return host.request('GET', prefix + path, params={'audience': 'owner_private', **values})
        report['identity_before'] = get('/personal')['identity_id']
        assert not post('/channel-audio/connect', consumer_id=consumer)['listen_enabled']
        post('/channel-audio/enable', consumer_id=consumer, enabled=True)
        source_id = 'authored-panel-' + uuid.uuid4().hex[:12]
        selected = post('/shared-activity/control', request_id=uuid.uuid4().hex, action='select', values={
            'source_id': source_id, 'label': 'Authored diagnostic PNG, not a game session', 'kind': 'selected_application',
            'seconds': 300, 'maximum_reviews': 1, 'maximum_frames': 1, 'review_interval_seconds': 10})
        post('/shared-activity/control', request_id=uuid.uuid4().hex, action='goal', values={
            'text': 'This is an authored diagnostic image. I am checking which visible labels are readable. Keep any reflection brief, preserve uncertainty, and do not treat this as an OSRS session.'})
        image = authored_png()
        capture_time = time.time()
        moment_id = 'proof-moment-' + uuid.uuid4().hex
        result = post('/shared-activity/moment', request_id=moment_id,
            metadata={'observation_id': 'proof-frame-' + uuid.uuid4().hex, 'source_id': source_id,
                'source_epoch': selected['source_epoch'], 'sequence': 0, 'captured_at': capture_time, 'kind': 'selected_application'},
            image_base64=base64.b64encode(image).decode(), media_type='image/png')
        report['moment'] = result
        report['image_sha256'] = hashlib.sha256(image).hexdigest()
        report['capture_time'] = capture_time
        assert result.get('captured_at') == capture_time and result.get('image_sha256') == report['image_sha256'], result
        assert result['current_state'] == 'unknown' and result['action_authority'] is False
        report['semantic_choice'] = result['status']
        if result['status'] == 'generated':
            assert result['text'] and len(result['text']) <= 1600, result
            received = post('/shared-activity/moment/received', request_id=uuid.uuid4().hex,
                consumer_id=consumer, moment_id=moment_id, text_sha256=result['text_sha256'])
            assert received['audio_consumer_bound']
            report['declared_text_transport_receipt'] = received
            # This script is not a DOM. Its typed receipt is an explicit authored HTTP consumer declaration.
            fetched = set()
            began_render = time.time()
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(post, '/channel-audio/moment/render', consumer_id=consumer, moment_id=moment_id)
                deadline = time.monotonic() + 185
                while time.monotonic() < deadline:
                    output = get('/interaction/poll', consumer_id=consumer)['output']
                    if output and output['id'] not in fetched:
                        assert output['channel'] == 'shared_moment' and output['audio_available']
                        params = {'audience': 'owner_private', 'consumer_id': consumer, 'generation': output['generation'], 'output_epoch': output['output_epoch']}
                        response = host.client.get(prefix + '/interaction/audio/' + output['id'], params=params)
                        response.raise_for_status()
                        data = response.content
                        from gamma.coordination.interaction import validate_wave
                        duration = validate_wave(data, maximum_seconds=90)
                        destination = root / ('chunk-' + str(output['chunk_index']) + '.wav')
                        destination.write_bytes(data); destination.chmod(0o600)
                        report['received_wav'].append({'id': output['id'], 'chunk_index': output['chunk_index'],
                            'chunk_count': output['chunk_count'], 'speech_text': output['speech_text'],
                            'bytes': len(data), 'seconds': duration, 'sha256': hashlib.sha256(data).hexdigest(),
                            'ready_after_render_seconds': time.time() - began_render})
                        fetched.add(output['id'])
                        post('/interaction/ack', consumer_id=consumer, generation=output['generation'],
                            output_epoch=output['output_epoch'], output_id=output['id'], status='delivered')
                        save(root / 'report.json', report)
                    if future.done():
                        receipt = future.result()
                        if fetched == set(receipt.get('output_ids', [])):
                            break
                    time.sleep(.1)
                receipt = future.result(timeout=5)
            report['render_receipt'] = receipt
            assert receipt['status'] == 'generated' and fetched == set(receipt['output_ids']) and fetched, receipt
            assert ' '.join(row['speech_text'] for row in report['received_wav']) == ' '.join(result['text'].split())
            provider_path = root / 'state/actual-tts-calls.jsonl'
            events = [json.loads(line) for line in provider_path.read_text().splitlines()]
            assert 1 <= sum(event['event'] == 'start' for event in events) <= 4
            finished = [event for event in events if event['event'] == 'finish' and event.get('ok')]
            assert all(any(event['audio_sha256'] == row['sha256'] for event in finished) for row in report['received_wav'])
            report['actual_provider_events'] = events
            repeated = post('/channel-audio/moment/render', consumer_id=consumer, moment_id=moment_id)
            assert repeated['output_ids'] == receipt['output_ids']
            assert provider_path.read_text().splitlines() == [json.dumps(event) for event in events]
            report['no_duplicate_tts'] = True
            report['speech_transport_exercised'] = True
        else:
            assert result['status'] in {'silent', 'deferred'}, result
            report['speech_transport_exercised'] = False
            report['quiet_choice_preserved'] = True
        post('/shared-activity/control', request_id=uuid.uuid4().hex, action='stop', values={})
        assert get('/interaction/poll', consumer_id=consumer)['output'] is None
        assert not list((root / 'state/interaction-audio').glob('*.wav'))
        post('/channel-audio/disconnect', consumer_id=consumer)
        assert not post('/channel-audio/connect', consumer_id=consumer)['listen_enabled']
        assert get('/personal')['identity_id'] == report['identity_before']
        flags = get('/interaction/status', consumer_id=consumer)
        assert not flags['voice_enabled'] and not flags['speech_enabled']
        model_events = [json.loads(line) for line in (root / 'state/moment-model-events.jsonl').read_text().splitlines()]
        assert sum(len(event['image_hashes']) for event in model_events) == 1
        assert any(report['image_sha256'] in event['image_hashes'] for event in model_events)
        report['actual_model_events'] = model_events
        report['source_stable'] = source_files(project) == before
        assert report['source_stable'], 'Source changed during the finite proof; retain this nonfinal receipt.'
        report['complete'] = True
    except BaseException as error:
        report['error_type'] = type(error).__name__
        raise
    finally:
        if host:
            host.stop()
            report['owned_host_absent'] = host.process.poll() is not None
        database = root / 'state/coordination.sqlite3'
        if database.exists():
            with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
                report['journal_counts'] = dict(db.execute('SELECT kind,COUNT(*) FROM records GROUP BY kind'))
            assert report['journal_counts'].get('turn', 0) == 0
        report['seconds'] = round(time.monotonic() - started, 3)
        save(root / 'report.json', report)
        print(json.dumps({'report': str(root.relative_to(project) / 'report.json'), 'complete': report['complete'],
            'speech_transport_exercised': report.get('speech_transport_exercised'), 'seconds': report['seconds'],
            'owned_host_absent': report.get('owned_host_absent')}))


if __name__ == '__main__':
    main()
