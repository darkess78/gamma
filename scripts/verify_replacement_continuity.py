"""Finite isolated replacement comparison using only already observed local components.

Default is inert. Preparation copies source and one already known reference WAV.
Real execution preserves latest copied state across Qwen -> Gemma -> Qwen and
renders two tracked voice presets. Mechanical continuity is not human acceptance.
"""
import sys
sys.dont_write_bytecode = True
import argparse
from contextlib import closing
from dataclasses import asdict
import getpass
import hashlib
import json
import os
import re
from pathlib import Path
import socket
import sqlite3
import subprocess
import threading
import time
import urllib.request
import uuid
import zipfile


PRIMARY = 'qwen3.8:27b'
REPLACEMENT = 'gemma4:e4b'
PROFILES = ('qwen_shana_dataset_xvec_soft', 'qwen_shana_dataset_ref_soft')
REFERENCE_SHA = 'b00ed74629d994d3b2d85185476a61827cb8ce665a6502c5a2b29c4265b1b3d5'
REFERENCE_NAME = 'assets/voice_references/shana/shana_dataset_ref_22s.wav'
MAX_GENERATE = 16
CONTROL_TEXT = 'The workshop drink is mint tea. The blueprint review is still unfinished. We can come back to it when you are ready.'
DURABLE = {'personal_identity', 'personal_record', 'personal_source', 'person', 'person_identity', 'turn'}


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if os.name == 'posix': path.chmod(0o600)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument('--prepare', action='store_true')
    value.add_argument('--run-real', action='store_true')
    value.add_argument('--qwen-reference', type=Path)
    value.add_argument('--model', choices=(PRIMARY, REPLACEMENT), default=PRIMARY)
    value.add_argument('--endpoint', choices=('http://127.0.0.1:11434',), default='http://127.0.0.1:11434')
    value.add_argument('--child-state', type=Path)
    value.add_argument('--voice-profile', choices=PROFILES)
    value.add_argument('--voice-output', type=Path)
    return value


def primary_turn_format(schema):
    """Recognize this project's actual bounded oneOf/anyOf private turn format."""
    pending = [(schema, 0)]
    for _ in range(16):
        if not pending: return False
        item, depth = pending.pop()
        if not isinstance(item, dict) or depth > 4: continue
        properties = item.get('properties')
        if isinstance(properties, dict) and {'action', 'requested_delivery', 'final_text'} <= properties.keys():
            return True
        for key in ('oneOf', 'anyOf'):
            children = item.get(key, [])
            if not isinstance(children, list) or len(children) > 8: return False
            pending.extend((child, depth + 1) for child in children)
    return False


def answer_checks(text, expected):
    """Conservative authored-fixture checks, never personality or human acceptance."""
    lowered = text.casefold()
    result = {}
    for key, word in expected.items():
        if key == 'count':
            result[key] = bool(re.fullmatch(r'\s*(?:2|two)[.!]?\s*', lowered))
        elif key == 'unfinished':
            named = bool(re.search(r'\b' + re.escape(word.casefold()) + r'\b', lowered))
            unfinished = bool(re.search(r'\b(?:unfinished|pending|outstanding|unreviewed)\b|\b(?:still needs?|need to|not yet|remains? to)\b', lowered))
            completed = bool(re.search(r'\b(?:completed|finished|done)\b', lowered))
            negated = bool(re.search(r'\bnot (?:yet )?(?:complete|completed|finished|done)\b', lowered))
            result[key] = named and unfinished and (not completed or negated)
        else:
            result[key] = bool(re.search(r'\b' + re.escape(word.casefold()) + r'\b', lowered))
    return result


def durable(state):
    with closing(sqlite3.connect((state / 'coordination.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
        return {kind + '/' + key: digest(json.loads(body)) for kind, key, body in
            db.execute('SELECT kind,id,body FROM records') if kind in DURABLE}


def record(state, kind, key):
    with closing(sqlite3.connect((state / 'coordination.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
        row = db.execute('SELECT body FROM records WHERE kind=? AND id=?', (kind, key)).fetchone()
    return json.loads(row[0]) if row else None


def prepare(project, reference):
    if (not reference or not reference.is_absolute() or reference.is_symlink()
            or not reference.is_file() or reference.stat().st_size != 766764):
        raise ValueError('known_existing_reference_required')
    before = reference.stat()
    raw = reference.read_bytes()
    after = reference.stat()
    if hashlib.sha256(raw).hexdigest() != REFERENCE_SHA or (before.st_ino, before.st_mtime_ns, before.st_size) != (after.st_ino, after.st_mtime_ns, after.st_size):
        raise ValueError('known_reference_changed')
    from verify_hosting_endurance import pin_source, verify_pin
    pinned, manifest = pin_source(project)
    destination = pinned / REFERENCE_NAME
    destination.parent.mkdir(parents=True, mode=0o700)
    with destination.open('xb') as handle: handle.write(raw)
    destination.chmod(0o600)
    if hashlib.sha256(destination.read_bytes()).hexdigest() != REFERENCE_SHA:
        raise ValueError('reference_copy_mismatch')
    verify_pin(pinned)
    plan = {'status': 'prepared', 'pin': str(pinned), 'source_sha256': manifest['source_sha256'],
        'reference_sha256': REFERENCE_SHA, 'models': [PRIMARY, REPLACEMENT, PRIMARY], 'voice_profiles': list(PROFILES),
        'maximum_generate_calls': MAX_GENERATE, 'maximum_voice_samples': 2,
        'fresh_data_tree': True, 'model_calls': 0, 'human_acceptance': False}
    save(pinned / 'replacement-plan.json', plan)
    return plan


def verify_prepared(project):
    from verify_hosting_endurance import verify_pin
    manifest = verify_pin(project)
    plan = json.loads((project / 'replacement-plan.json').read_text())
    if (plan.get('status') != 'prepared' or plan.get('pin') != str(project)
            or plan.get('source_sha256') != manifest['source_sha256']
            or plan.get('models') != [PRIMARY, REPLACEMENT, PRIMARY]
            or plan.get('voice_profiles') != list(PROFILES)
            or plan.get('reference_sha256') != REFERENCE_SHA
            or hashlib.sha256((project / REFERENCE_NAME).read_bytes()).hexdigest() != REFERENCE_SHA):
        raise ValueError('invalid_replacement_preparation')
    return manifest


def child(project, state):
    original = urllib.request.urlopen
    lock = threading.Lock()
    def event(value):
        with lock:
            with (state / 'replacement-http.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(value) + '\n')
    class Response:
        def __init__(self, response, identifier): self.response, self.identifier = response, identifier
        def __enter__(self): self.response.__enter__(); return self
        def __exit__(self, *values): return self.response.__exit__(*values)
        def __getattr__(self, key): return getattr(self.response, key)
        def read(self, *values):
            raw = self.response.read(*values)
            event({'event': 'returned', 'id': self.identifier, 'at': time.time(), 'response_sha256': hashlib.sha256(raw).hexdigest()})
            return raw
    def observed(request, *args, **kwargs):
        if not isinstance(request, urllib.request.Request) or not request.full_url.endswith('/api/generate') or not request.data:
            return original(request, *args, **kwargs)
        body = json.loads(request.data)
        budget_path = state.parent / 'generate-budget.json'
        # Exactly one owned host/request at a time. The persistent count spans replacements.
        budget = json.loads(budget_path.read_text())
        if budget['count'] >= MAX_GENERATE or body.get('model') not in {PRIMARY, REPLACEMENT}:
            raise RuntimeError('replacement_generate_budget_or_model')
        budget['count'] += 1; save(budget_path, budget)
        identifier = uuid.uuid4().hex
        shape = body.get('format')
        event({'event': 'dispatched', 'id': identifier, 'at': time.time(), 'model': body['model'],
            'structured_primary_turn': primary_turn_format(shape)})
        try: return Response(original(request, *args, **kwargs), identifier)
        except BaseException as error:
            event({'event': 'unknown', 'id': identifier, 'at': time.time(), 'error_type': type(error).__name__})
            raise
    urllib.request.urlopen = observed
    from growth_probe_support import child as configured_child
    configured_child(project, state)


def comparison_voice_pin(profile, project):
    from gamma.voice.voice_profiles import resolve_tts_config
    from gamma.coordination.private_release_manifest import effective_voice_pin
    config = resolve_tts_config(profile_id=profile)
    reference = Path(config.qwen_tts_reference_audio or '')
    if not reference.is_absolute(): reference = project / reference
    if (profile not in PROFILES or config.profile_id != profile or config.provider.lower() != 'qwen-tts'
            or config.rvc_enabled or config.qwen_tts_endpoint != 'http://127.0.0.1:9882/tts'
            or reference.is_symlink() or reference.resolve() != (project / REFERENCE_NAME).resolve()
            or not reference.is_file() or hashlib.sha256(reference.read_bytes()).hexdigest() != REFERENCE_SHA):
        raise ValueError('tracked_voice_profile_reference_mismatch')
    pin = {'configuration_sha256': digest(asdict(config)), 'reference_audio_sha256': REFERENCE_SHA,
           'provider': config.provider.lower()}
    try:
        effective_voice_pin(profile, project)
        release = {'eligible': True}
    except ValueError as error:
        release = {'eligible': False, 'reason': str(error)}
    return config, pin, release


def voice_sample(project, profile, output):
    if profile not in PROFILES or output is None or not output.is_absolute() or not output.is_relative_to(project / 'data'):
        raise ValueError('bounded_voice_sample_target_required')
    output.mkdir(parents=True, mode=0o700)
    os.environ.update(SHANA_PROJECT_ROOT=str(project), SHANA_TTS_PROVIDER='qwen-tts', SHANA_TTS_PROFILE=profile,
        SHANA_AUDIO_OUTPUT_DIR=str(output), SHANA_DATABASE_URL='sqlite:///' + str(output / 'unused.sqlite3'))
    from gamma.voice.tts import TTSService
    from gamma.coordination.interaction import validate_wave
    config, pin, release = comparison_voice_pin(profile, project)
    original = urllib.request.urlopen
    observed = []
    class VoiceResponse:
        def __init__(self, response): self.response = response
        def __enter__(self): self.response.__enter__(); return self
        def __exit__(self, *values): return self.response.__exit__(*values)
        def __getattr__(self, key): return getattr(self.response, key)
        def read(self, *values):
            raw = self.response.read(*values)
            observed[0].update(returned=True, response_sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
            return raw
    def opened(request, *args, **kwargs):
        if (not isinstance(request, urllib.request.Request) or request.full_url != 'http://127.0.0.1:9882/tts'
                or len(observed) >= 1):
            raise RuntimeError('voice_sample_endpoint_or_call_budget')
        observed.append({'request_sha256': hashlib.sha256(request.data).hexdigest(), 'at': time.time()})
        return VoiceResponse(original(request, *args, **kwargs))
    urllib.request.urlopen = opened
    began = time.monotonic()
    result = TTSService().synthesize(CONTROL_TEXT, emotion='neutral')
    audio = Path(result.audio_path)
    if audio.is_symlink() or not audio.resolve().is_relative_to(output) or audio.stat().st_size > 4_000_000:
        raise ValueError('voice_sample_artifact_outside_private_output')
    raw = audio.read_bytes(); audio.chmod(0o600)
    seconds = validate_wave(raw, maximum_seconds=30)
    save(output / 'sample.json', {'pid': os.getpid(), 'profile': profile, 'voice_pin': pin, 'release_policy': release, 'text': CONTROL_TEXT,
        'text_sha256': hashlib.sha256(CONTROL_TEXT.encode()).hexdigest(), 'audio_path': str(audio.relative_to(project)),
        'audio_sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw), 'audio_seconds': seconds,
        'synthesis_seconds': time.monotonic() - began, 'actual_http_calls': observed,
        'provider': result.provider, 'human_hearing': False, 'selected_as_default': False})


def run(project):
    manifest = verify_prepared(project)
    from growth_probe_support import Host
    from gamma.coordination.store import Store, OwnershipLost
    from gamma.coordination.transfer import export_snapshot, restore_snapshot
    folder = project / 'data/coordination/p9' / ('replacement-' + uuid.uuid4().hex)
    private = folder / 'private-state'; private.mkdir(parents=True, mode=0o700)
    save(private / 'generate-budget.json', {'count': 0})
    archive = folder / 'executed-source.zip'
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED) as bundle:
        for name in manifest['files']: bundle.write(project / name, name)
    archive.chmod(0o600)
    report = {'kind': 'bounded_existing_local_replacement_comparison', 'complete': False,
        'author': getpass.getuser() + '@' + socket.gethostname(), 'source_sha256': manifest['source_sha256'],
        'source_files': manifest['files'], 'source_archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
        'fresh_authored_identity': True, 'production_activated': False, 'human_acceptance': False,
        'model_selection_promoted': False, 'semantic_check_scope': 'conservative authored-fixture text checks; not personality or human acceptance', 'steps': [], 'voice_samples': [], 'owned_pids': [], 'voice_pids': []}
    host = None; began = time.monotonic()
    def note(name, **values):
        report['steps'].append({'name': name, **values}); save(folder / 'report.json', report)
    def start(state, model):
        nonlocal host
        os.environ.update(SHANA_TTS_PROVIDER='stub', SHANA_TTS_PROFILE='')
        host = Host(project, state, model, 'http://127.0.0.1:11434'); host.child_script = Path(__file__)
        try: host.start()
        finally:
            if host.process: report['owned_pids'].append(host.process.pid)
        return host.request('GET', '/api/coordination/personal', params={'audience': 'owner_private'})['identity_id']
    def post(path, **values):
        return host.request('POST', '/api/coordination/' + path,
            json={'audience': 'owner_private', 'request_id': uuid.uuid4().hex, **values})
    def remember(kind, topic, text):
        return post('personal', action='memory', values={'op': 'remember', 'kind': kind,
            'subject_id': 'owner', 'topic': topic, 'text': text})['id']
    def chat(text, checks):
        identifier = uuid.uuid4().hex
        response = host.request('POST', '/api/monitor/input', json={'audience': 'owner_private', 'request_id': identifier,
            'input_mode': 'private_text', 'session_id': 'replacement-proof', 'text': text})['result']['assistant_response']
        final = response.get('display_text') or ''
        return {'request_id': identifier, 'response': response, 'checks': answer_checks(final, checks)}
    def move(source, target, label):
        nonlocal host
        token = record(source, 'brain', 'state'); host.stop(); host = None
        assert not record(source, 'brain', 'state')['owner']
        before = durable(source)
        bundle = folder / ('snapshot-' + label)
        export_snapshot(source, bundle); restore_snapshot(bundle, target)
        assert durable(source) == before == durable(target)
        note('portable_latest_' + label, records=len(before), records_sha256=digest(before), same_durable_records=True)
        return (token['generation'], token['owner']), before
    def stale_rejected(state, token):
        try:
            with Store(state).transaction(token): raise AssertionError('stale_owner_admitted')
        except OwnershipLost: return True
    try:
        first, second, rollback = private / 'original', private / 'replacement', private / 'rollback'
        identity = start(first, PRIMARY); report['identity_id'] = identity
        drink = remember('preference', 'fictional workshop drink', 'The fictional workshop drink is mint tea.')
        unfinished = remember('commitment', 'fictional workshop blueprint', 'The owner still needs to review the workshop blueprint. It remains unfinished.')
        baseline = post('maintenance', action='backup'); assert baseline['status'] == 'verified_snapshot'
        later = remember('decision', 'fictional later observatory experience', 'After the older snapshot, the owner checked the observatory map. The blueprint review remains unfinished.')
        note('original_model_familiar', model=PRIMARY, result=chat('What workshop drink is recorded, what later map did we check, and what blueprint task remains unfinished?',
            {'drink':'mint', 'later':'observatory', 'unfinished':'blueprint'}))
        old_token, original_rows = move(first, second, 'before_replacement')
        assert start(second, REPLACEMENT) == identity and stale_rejected(second, old_token)
        assert all(durable(second).get(key) == value for key, value in original_rows.items())
        assert record(second, 'personal_record', unfinished)['status'] == 'active'
        note('replacement_familiar', model=REPLACEMENT, result=chat('What workshop drink is recorded, what later map did we check, and what blueprint task remains unfinished?',
            {'drink':'mint', 'later':'observatory', 'unfinished':'blueprint'}))
        note('replacement_unfamiliar', model=REPLACEMENT, result=chat('A fresh tiny reasoning check: the only items are red, blue, and red. How many items are red? Give just the count.', {'count':'2'}))
        after_swap = remember('decision', 'fictional post replacement marker', 'After changing the local model, the owner selected amber for the workshop marker. The blueprint review remains unfinished.')
        note('replacement_later_exchange', model=REPLACEMENT, result=chat('The workshop marker is now amber. Keep that later experience distinct from finishing the blueprint review.', {'later':'amber'}))
        previous_token, replacement_rows = move(second, rollback, 'before_rollback')
        assert start(rollback, PRIMARY) == identity and stale_rejected(rollback, previous_token)
        assert all(durable(rollback).get(key) == value for key, value in replacement_rows.items())
        for memory in (drink, unfinished, later, after_swap): assert record(rollback, 'personal_record', memory)
        assert record(rollback, 'personal_record', unfinished)['status'] == 'active'
        note('original_model_rollback', model=PRIMARY, result=chat('What workshop drink is recorded, which map did we check, what color did we select later, and what blueprint review remains unfinished?',
            {'drink':'mint','prior_later':'observatory','post_replacement':'amber','unfinished':'blueprint'}))
        host.stop(); host = None
        report['mechanical_continuity_verified'] = True
        for state in (first, second, rollback):
            events_path = state / 'replacement-http.jsonl'
            events = [json.loads(line) for line in events_path.read_text().splitlines()]
            dispatched = {event['id'] for event in events if event['event'] == 'dispatched'}
            returned = {event['id'] for event in events if event['event'] == 'returned'}
            assert dispatched == returned and not any(event['event'] == 'unknown' for event in events)
            expected = REPLACEMENT if state == second else PRIMARY
            primary = [event for event in events if event.get('structured_primary_turn')]
            assert primary and all(event['model'] == expected for event in primary)
            note('actual_model_http', state=state.name, expected_primary=expected, events=events)
        report['generate_calls'] = json.loads((private / 'generate-budget.json').read_text())['count']
        assert report['generate_calls'] <= MAX_GENERATE
        report['actual_work_runs'] = {}
        for state in (first, second, rollback):
            with closing(sqlite3.connect((state / 'coordination.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
                report['actual_work_runs'][state.name] = db.execute("SELECT COUNT(*) FROM records WHERE kind='run'").fetchone()[0]
        assert not any(report['actual_work_runs'].values())
        for profile in PROFILES:
            output = folder / ('voice-' + profile)
            command = [sys.executable, str(Path(__file__)), '--voice-profile', profile, '--voice-output', str(output)]
            result = subprocess.run(command, env={**os.environ, 'PYTHONPATH': str(project / 'src'), 'SHANA_PROJECT_ROOT': str(project)},
                cwd=project, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=150)
            (folder / ('voice-' + profile + '.stdout.log')).write_bytes(result.stdout)
            (folder / ('voice-' + profile + '.stderr.log')).write_bytes(result.stderr)
            if result.returncode: raise RuntimeError('tracked_voice_sample_failed')
            sample = json.loads((output / 'sample.json').read_text())
            report['voice_pids'].append(sample['pid'])
            assert len(sample['actual_http_calls']) == 1 and sample['actual_http_calls'][0]['returned']
            report['voice_samples'].append(sample)
        assert len(report['voice_samples']) == 2
        report['semantic_checks_passed'] = all(all(step['result']['checks'].values()) for step in report['steps'] if 'result' in step)
        report['replacement_accepted'] = False
        report['source_stable'] = all(hashlib.sha256((project / name).read_bytes()).hexdigest() == sha for name, sha in manifest['files'].items())
        assert report['source_stable']
        report['complete'] = True
    except BaseException as error:
        report['error_type'] = type(error).__name__
        raise
    finally:
        if host: host.stop()
        report['owned_host_processes_absent'] = bool(report['owned_pids']) and all(not Path('/proc', str(pid)).exists() for pid in report['owned_pids'])
        report['voice_processes_absent'] = all(not Path('/proc', str(pid)).exists() for pid in report['voice_pids'])
        report['seconds'] = round(time.monotonic() - began, 3)
        save(folder / 'report.json', report)
        print(json.dumps({'report': str(folder / 'report.json'), 'complete': report['complete'],
            'mechanical_continuity_verified': report.get('mechanical_continuity_verified'),
            'semantic_checks_passed': report.get('semantic_checks_passed'), 'seconds': report['seconds'],
            'owned_host_processes_absent': report['owned_host_processes_absent']}))


def main(argv=None):
    args = parser().parse_args(argv)
    if not any((args.prepare, args.run_real, args.child_state, args.voice_profile)):
        print(json.dumps({'status': 'prepared_only', 'model_calls': 0, 'voice_calls': 0, 'production_changes': False}))
        return
    project = Path(__file__).resolve().parents[1]
    if os.name != 'posix' or project.name in {'gamma-main', 'gamma-modular-phase1'}:
        raise RuntimeError('Run only on the isolated Gamma candidate or its prepared copy.')
    if sum(bool(value) for value in (args.prepare, args.run_real, args.child_state, args.voice_profile)) != 1:
        raise ValueError('one_explicit_mode_required')
    if args.prepare: print(json.dumps(prepare(project, args.qwen_reference))); return
    verify_prepared(project)
    if args.child_state: child(project, args.child_state); return
    if args.voice_profile: voice_sample(project, args.voice_profile, args.voice_output); return
    run(project)


if __name__ == '__main__': main()
