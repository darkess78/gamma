"""Actual local selected-frame perception and same-Core review, with no OSRS claim.

The PNG is authored in memory from the bitmap font below. Raw frames are not
stored. A latency/guard rejection remains a failed connected result; it is not
repaired by altering source timestamps, freshness or backend capability checks.
"""
import argparse
import base64
import getpass
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import struct
import threading
import time
import urllib.request
import uuid
import zipfile
import zlib

from growth_probe_support import Host


FONT = {
    'A': ['01110','10001','10001','11111','10001','10001','10001'],
    'B': ['11110','10001','10001','11110','10001','10001','11110'],
    'C': ['01111','10000','10000','10000','10000','10000','01111'],
    'D': ['11110','10001','10001','10001','10001','10001','11110'],
    'E': ['11111','10000','10000','11110','10000','10000','11111'],
    'I': ['11111','00100','00100','00100','00100','00100','11111'],
    'K': ['10001','10010','10100','11000','10100','10010','10001'],
    'L': ['10000','10000','10000','10000','10000','10000','11111'],
    'N': ['10001','11001','11001','10101','10011','10011','10001'],
    'O': ['01110','10001','10001','10001','10001','10001','01110'],
    'P': ['11110','10001','10001','11110','10000','10000','10000'],
    'S': ['01111','10000','10000','01110','00001','00001','11110'],
    'T': ['11111','00100','00100','00100','00100','00100','00100'],
    '2': ['01110','10001','00001','00010','00100','01000','11111'],
    '4': ['00010','00110','01010','10010','11111','00010','00010'],
    '?': ['01110','10001','00001','00010','00100','00000','00100'],
}
LABELS = ['TEST PANEL', 'BANK OPEN', 'COINS 42', 'DETAIL ?']


def authored_png():
    width, height = 640, 360
    pixels = bytearray([255] * width * height * 3)
    def fill(x, y, w, h, color):
        for row in range(y, y + h):
            start = (row * width + x) * 3
            pixels[start:start + w * 3] = bytes(color) * w
    fill(0, 0, width, 8, (30, 64, 175))
    for line, label in enumerate(LABELS):
        for index, char in enumerate(label):
            if char == ' ':
                continue
            for y, row in enumerate(FONT[char]):
                for x, value in enumerate(row):
                    if value == '1':
                        fill(35 + index * 30 + x * 4, 30 + line * 80 + y * 4, 4, 4, (17, 24, 39))
    raw = b''.join(b'\0' + pixels[y * width * 3:(y + 1) * width * 3] for y in range(height))
    def chunk(name, value):
        return struct.pack('>I', len(value)) + name + value + struct.pack('>I', zlib.crc32(name + value))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def child(project, state):
    from gamma.config import settings
    from gamma.coordination.app import create_app
    from gamma.coordination.dashboard import create_app as create_dashboard
    from gamma.coordination.hosting import loopback_server
    halt = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: halt.set())
    def event(value):
        with (state / 'selected-image-events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(value, ensure_ascii=False) + '\n')
    original_open = urllib.request.urlopen
    def opened(request, *args, **kwargs):
        if isinstance(request, urllib.request.Request) and request.full_url.endswith('/api/generate') and request.data:
            body = json.loads(request.data)
            if body.get('images'):
                event({'kind': 'actual_image_http_dispatch', 'at': time.time(), 'model': body.get('model'),
                       'image_hashes': [hashlib.sha256(base64.b64decode(value)).hexdigest() for value in body['images']],
                       'image_count': len(body['images'])})
        return original_open(request, *args, **kwargs)
    urllib.request.urlopen = opened
    app = create_app()
    with loopback_server(app) as api:
        original_analyzer = app.state.shared_activity.analyze_image
        if callable(original_analyzer):
            def analyzed(raw, media_type, kind):
                began = time.monotonic()
                try:
                    values = original_analyzer(raw, media_type, kind)
                    event({'kind': 'configured_analyzer_returned', 'elapsed_ms': round((time.monotonic() - began) * 1000, 3),
                           'image_sha256': hashlib.sha256(raw).hexdigest(), 'interpretation': values})
                    return values
                except Exception as exc:
                    event({'kind': 'configured_analyzer_failed', 'elapsed_ms': round((time.monotonic() - began) * 1000, 3),
                           'type': type(exc).__name__})
                    raise
            app.state.shared_activity.analyze_image = analyzed
        settings.shana_port = int(api.rsplit(':', 1)[1])
        os.environ['GAMMA_COORDINATION_API_ORIGIN'] = api
        with loopback_server(create_dashboard()) as dashboard:
            save(state / 'ready.json', {'pid': os.getpid(), 'api': api, 'dashboard': dashboard,
                                      'configured_analyzer_available': callable(original_analyzer)})
            while not halt.wait(.2):
                pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default='qwen3.8:27b')
    parser.add_argument('--endpoint', default='http://127.0.0.1:11434')
    parser.add_argument('--child-state', type=Path)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--renew-live-frames', action='store_true',
                        help='Actually regenerate and resend full identical PNG frames every two seconds, for at most 45 seconds.')
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    if args.self_test:
        image = authored_png()
        assert image.startswith(b'\x89PNG\r\n\x1a\n') and len(image) < 800_000
        assert struct.unpack('>II', image[16:24]) == (640, 360)
        print(json.dumps({'self_test': True, 'bytes': len(image), 'sha256': hashlib.sha256(image).hexdigest(), 'raw_frame_saved': False}))
        return
    if os.name != 'posix' or project.name in {'gamma-main', 'gamma-modular-phase1'}:
        raise RuntimeError('The isolated Gamma candidate is required.')
    if args.child_state:
        child(project, args.child_state)
        return
    root = project / 'data/coordination/p6' / ('selected-image-' + uuid.uuid4().hex)
    root.mkdir(parents=True, mode=0o700)
    state = root / 'state'
    paths = sorted([*project.glob('src/gamma/coordination/*.py'),
                    project / 'src/gamma/llm/local_adapter.py', project / 'src/gamma/llm/context_budget.py',
                    project / 'src/gamma/persona/coordination.py', project / 'src/gamma/conversation/service.py',
                    project / 'src/gamma/conversation/turn_contract.py', Path(__file__)])
    def hashes():
        return {str(path.relative_to(project)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    baseline = hashes()
    archive = root / 'executed-selected-python.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as zipped:
        for path in paths:
            zipped.write(path, str(path.relative_to(project)))
    report = {'author': getpass.getuser() + '@' + socket.gethostname(),
              'kind': 'p6_authored_selected_png_actual_local_adapter', 'complete': False,
              'source_files': baseline, 'selected_python_archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
              'model': args.model, 'vision_model': args.model, 'labels_in_authored_fixture': LABELS,
              'raw_frame_saved': False, 'actual_osrs_screen': False, 'human_assessment': False,
              'minecraft_execution': False, 'production_activated': False,
              'same_pixel_live_renewal': args.renew_live_frames, 'renewal_frames': [],
              'semantic_acceptance': 'requires_review_of_actual_output'}
    host = None
    renewal_stop = threading.Event()
    renewal = None
    began = time.monotonic()
    os.environ.update(SHANA_LOCAL_LLM_SUPPORTS_VISION='true', SHANA_LOCAL_LLM_VISION_MODEL=args.model)
    try:
        host = Host(project, state, args.model, args.endpoint)
        host.child_script = Path(__file__)
        host.start()
        report['host_pid'] = host.process.pid
        prefix = '/api/coordination/shared-activity'
        def request(path, **values):
            return host.request('POST', prefix + path, json={'audience': 'owner_private',
                                'request_id': uuid.uuid4().hex, **values})
        identity = host.request('GET', '/api/coordination/personal?audience=owner_private')['identity_id']
        request('/control', action='goal', values={'text': 'Keep me company while I inspect an authored software test panel. Its question mark means the detail is unknown.'})
        selected = request('/control', action='select', values={'source_id': 'authored_panel', 'label': 'Authored test panel',
                           'kind': 'selected_application', 'seconds': 180, 'maximum_reviews': 1, 'review_interval_seconds': 10})
        image = authored_png()
        report['image_sha256'] = hashlib.sha256(image).hexdigest()
        report['image_bytes'] = len(image)
        def packet(sequence):
            # Regenerate the actual authored pixels on every capture. The
            # capture time describes these new bytes, never the old inference.
            raw = authored_png()
            return {'audience': 'owner_private', 'request_id': uuid.uuid4().hex,
                    'metadata': {'observation_id': 'authored_frame_' + str(sequence), 'source_id': 'authored_panel',
                    'source_epoch': selected['source_epoch'], 'sequence': sequence, 'captured_at': time.time(), 'kind': 'selected_application'},
                    'media_type': 'image/png', 'image_base64': base64.b64encode(raw).decode()}
        first_packet = packet(0)
        report['original_capture'] = first_packet['metadata']
        def renew():
            deadline, sequence = time.monotonic() + 45, 1
            while not renewal_stop.wait(2) and time.monotonic() < deadline:
                value = packet(sequence)
                evidence = {'metadata': value['metadata'], 'image_bytes': len(base64.b64decode(value['image_base64'])),
                            'image_sha256': hashlib.sha256(base64.b64decode(value['image_base64'])).hexdigest(),
                            'request_started_at': time.time()}
                started = time.monotonic()
                try:
                    result = host.client.post(prefix + '/image', json=value)
                    evidence.update(status_code=result.status_code, receipt=result.json(), response_received_at=time.time(),
                                    elapsed_ms=round((time.monotonic() - started) * 1000, 3))
                    report['renewal_frames'].append(evidence)
                    if result.status_code != 200:
                        renewal_stop.set()
                except BaseException as exc:
                    evidence['error_type'] = type(exc).__name__
                    report['renewal_frames'].append(evidence)
                    renewal_stop.set()
                sequence += 1
        if args.renew_live_frames:
            renewal = threading.Thread(target=renew, name='actual-full-frame-renewal')
            renewal.start()
        before = time.monotonic()
        response = host.client.post(prefix + '/image', json=first_packet)
        report['image_http'] = {'status_code': response.status_code, 'elapsed_ms': round((time.monotonic() - before) * 1000, 3),
                                'receipt': response.json() if response.headers.get('content-type', '').startswith('application/json')
                                else response.text[:400]}
        if response.status_code == 200 and report['image_http']['receipt'].get('status') != 'unavailable':
            state_value = host.request('GET', prefix + '/status?audience=owner_private')
            report['admitted_observation'] = state_value['context']['observation']
            before = time.monotonic()
            review = request('/review')
            report['core_review'] = {'elapsed_ms': round((time.monotonic() - before) * 1000, 3), 'receipt': review}
            report['complete'] = review.get('status') in {'generated', 'silent'}
            report['context_after_review'] = host.request('GET', prefix + '/status?audience=owner_private')['context']
            report['context_checked_at'] = time.time()
        renewal_stop.set()
        if renewal:
            renewal.join(timeout=180)
            if renewal.is_alive():
                raise RuntimeError('Frame renewal did not stop.')
        report['same_identity_after'] = host.request('GET', '/api/coordination/personal?audience=owner_private')['identity_id'] == identity
        request('/control', action='stop', values={})
        report['source_stopped'] = not host.request('GET', prefix + '/status?audience=owner_private')['state']['enabled']
    except BaseException as exc:
        report['error'] = {'type': type(exc).__name__, 'message': str(exc)[:500]}
        raise
    finally:
        renewal_stop.set()
        if renewal:
            renewal.join(timeout=180)
        if host:
            host.stop()
            report['temporary_host_absent'] = host.process is not None and host.process.poll() is not None
        events = state / 'selected-image-events.jsonl'
        report['actual_events'] = [json.loads(line) for line in events.read_text().splitlines()] if events.exists() else []
        if args.renew_live_frames:
            report['complete'] = (report['complete'] and len(report['renewal_frames']) >= 2
                and all(frame.get('status_code') == 200 and frame['image_sha256'] == report.get('image_sha256')
                        for frame in report['renewal_frames'])
                and sum(event['kind'] == 'actual_image_http_dispatch' for event in report['actual_events']) == 1)
        report['source_stable'] = hashes() == baseline
        report['complete'] = report['complete'] and report['source_stable']
        report['elapsed_seconds'] = round(time.monotonic() - began, 3)
        save(root / 'report.json', report)
        print(json.dumps({'report': str(root / 'report.json'), 'complete': report['complete'],
                          'image_http': report.get('image_http'), 'core_review': report.get('core_review'),
                          'events': report['actual_events'], 'error': report.get('error')}))


if __name__ == '__main__':
    main()
