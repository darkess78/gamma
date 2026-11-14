"""Prepare, then explicitly run one finite disposable Minecraft development probe.

No download code is present. Execution needs owner-supplied approved Java 21 and
official Minecraft Java 1.21.11 server artifacts plus an owner approval receipt.
This is a new automated dev probe, not the manual local-smoke harness and not a
claim that a physical person played with Shana.
"""
import argparse
from collections import deque
import getpass
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile


VERSION = '1.21.11'
MAX_SECONDS = 600
MAX_DISK_BYTES = 1_073_741_824
OWNER, COMPANION = 'P6Owner', 'P6Shana'


def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda: stream.read(1_048_576), b''):
            value.update(part)
    return value.hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def scenarios():
    definitions = [
        ('baseline_0', 'baseline', 'clear', .5, 6.5, .5, None),
        ('baseline_1', 'baseline', 'tight', .5, 6.5, 2.5, 4),
        ('practice_0', 'practice', 'training_a', .5, 6.5, 4.5, 4),
        ('practice_1', 'practice', 'training_b', .5, 6.5, 6.5, 4),
        ('held_out_0', 'held_out', 'clear', -1.5, 5.5, 8.5, None),
        ('held_out_1', 'held_out', 'tight', -.5, 6.5, 10.5, 4),
    ]
    rows = []
    for name, partition, family, start, target, z, wall in definitions:
        layout = {'start': {'x': start, 'y': 64, 'z': z}, 'owner': {'x': target, 'y': 64, 'z': z},
                  'wall': None if wall is None else {'x': wall, 'y': 64, 'z': math.floor(z)}}
        rows.append({'id': name, 'partition': partition, 'family': family, 'layout': layout,
                     'variant_sha256': hashlib.sha256(canonical(layout)).hexdigest()})
    return rows


def packet():
    return {'author': getpass.getuser() + '@' + socket.gethostname(), 'version': VERSION, 'status': 'prepared_not_executed',
            'official_download_page': 'https://www.minecraft.net/en-us/download/server',
            'official_help': 'https://help.minecraft.net/hc/en-us/articles/360058525452',
            'license': 'https://www.minecraft.net/en-us/eula',
            'server_download_url': None, 'server_download_hash': None,
            'missing_artifact_reason': 'Metadata-only retrieval was blocked by automatic approval review; not retried.',
            'owner_decisions': ['Use existing approved disposable world or supply official 1.21.11 server JAR.',
                                'Supply approved Linux Java 21 runtime and accept applicable Minecraft EULA.',
                                'Approve one new loopback offline world and automated owner actor for this packet.'],
            'bounds': {'maximum_seconds': MAX_SECONDS, 'jvm_heap_mib': 2048, 'active_cpu_count': 2,
                       'temporary_disk_bytes': MAX_DISK_BYTES, 'node_heap_mib_each': 384,
                       'network_bind': '127.0.0.1', 'world_parent': '/tmp', 'gpu_or_model_calls': False},
            'players': {'owner': OWNER, 'companion': COMPANION, 'owner_is_automated': True},
            'scenarios': scenarios(), 'general_game_learning_claim': False,
            'manual_smoke_replayed': False, 'downloads_performed': False}


def validate_approval(path, jar, java):
    receipt = json.loads(Path(path).read_text())
    for field in ('temporary_test_owner_approved', 'minecraft_eula_owner_accepted', 'java_runtime_use_owner_approved'):
        if receipt.get(field) is not True:
            raise ValueError('Required explicit owner decision is missing: ' + field)
    if receipt.get('minecraft_version') != VERSION or receipt.get('maximum_seconds') != MAX_SECONDS:
        raise ValueError('Approval scope does not match this finite packet.')
    for name, artifact in [('server_jar', jar), ('java_executable', java)]:
        if not artifact.is_absolute() or not artifact.is_file() or artifact.is_symlink():
            raise ValueError('An exact regular approved artifact is required: ' + name)
        if receipt.get(name + '_sha256') != sha(artifact):
            raise ValueError('Approved artifact hash mismatch: ' + name)
    with zipfile.ZipFile(jar) as archive:
        versions = archive.read('META-INF/versions.list').decode()
        if '\t' + VERSION + '\t' not in versions:
            raise ValueError('Server bundler does not identify exact Minecraft 1.21.11.')
    return receipt


def command_plan(scene):
    layout = scene['layout']
    commands = ['fill -12 64 -12 12 67 12 minecraft:air',
                'fill -12 63 -12 12 63 12 minecraft:stone', 'setworldspawn 0 64 0']
    if layout['wall']:
        wall = layout['wall']
        commands.append(f"fill {wall['x']} 64 {wall['z']} {wall['x']} 65 {wall['z']} minecraft:stone")
    for name, target in ((OWNER, layout['owner']), (COMPANION, layout['start'])):
        commands.append(f"tp {name} {target['x']} {target['y']} {target['z']}")
    return commands


class Child:
    def __init__(self, argv, *, cwd, env=None, java=False):
        self.lines, self.recent = queue.Queue(), deque(maxlen=400)
        self._stop_lock = threading.Lock()
        tools = {name: shutil.which(name) for name in ('taskset', 'prlimit', 'nice')}
        if not all(tools.values()):
            raise RuntimeError('Existing taskset, prlimit and nice tools are required; no install is attempted.')
        cpus = ','.join(str(value) for value in sorted(os.sched_getaffinity(0))[:2])
        limited = [tools['taskset'], '-c', cpus, tools['prlimit'], '--cpu=540:550',
                   '--fsize=268435456:268435456', '--', tools['nice'], '-n', '10', *argv]
        self.process = subprocess.Popen(limited, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, bufsize=1)
        def read():
            for line in self.process.stdout:
                text = line.rstrip()[:2000]
                self.recent.append(text)
                if self.lines.qsize() < 500:
                    self.lines.put(text)
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()

    def send(self, value):
        self.process.stdin.write(value + '\n')
        self.process.stdin.flush()

    def json_request(self, value, timeout=10):
        self.send(json.dumps(value))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                row = json.loads(self.lines.get(timeout=min(1, deadline - time.monotonic())))
            except (queue.Empty, json.JSONDecodeError):
                continue
            if row.get('request_id') == value['request_id']:
                return row
            if row.get('event') in {'probe_failed', 'actor_error', 'actor_ended'}:
                raise RuntimeError('Automated owner actor failed.')
        raise TimeoutError('Automated owner probe did not return.')

    def stop(self, command=None):
        with self._stop_lock:
            self._stop(command)

    def _stop(self, command=None):
        if self.process.poll() is None and command:
            try:
                self.send(command)
                self.process.wait(10)
            except (OSError, subprocess.TimeoutExpired):
                pass
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(5)
        self.reader.join(2)
        for stream in (self.process.stdin, self.process.stdout):
            if stream is not None and not stream.closed:
                try:
                    stream.close()
                except OSError:
                    pass


def until(check, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(.05)
    raise TimeoutError('Finite Minecraft check did not reach its required observed state.')


def distance(left, right):
    return math.sqrt(sum((left[key] - right[key]) ** 2 for key in ('x', 'y', 'z')))


def cleanup_temp(root):
    root = Path(root)
    if (root.parent != Path('/tmp') or not root.name.startswith('shana-p6-minecraft-')
            or root.is_symlink() or root.resolve() != root
            or not getattr(shutil.rmtree, 'avoids_symlink_attacks', False)):
        raise ValueError('Refusing cleanup outside the exact owned temporary namespace.')
    shutil.rmtree(root)
    return not root.exists()


def validate_probe(scene, row, *, require_start):
    if row.get('ok') is not True or row['view']['scenario_id'] != scene['id']:
        raise ValueError('Independent world probe is missing.')
    view = row['view']
    if (view.get('automated_owner') is not True
            or view.get('evidence_kind') != 'independent_owner_client_server_state'
            or not 0 <= time.time() - view['at'] <= 2):
        raise ValueError('Fresh independent owner-client evidence is required.')
    expected_cells = {(x, y, math.floor(scene['layout']['start']['z']))
                      for x in range(math.floor(scene['layout']['start']['x']), math.ceil(scene['layout']['owner']['x']) + 1)
                      for y in (63, 64, 65)}
    actual_cells = {(cell['x'], cell['y'], cell['z']) for cell in view['cells']}
    if len(actual_cells) != len(view['cells']) or actual_cells != expected_cells:
        raise ValueError('Independent geometry evidence is incomplete or duplicated.')
    if not view['positions']['companion'] or distance(view['positions']['owner'], scene['layout']['owner']) > .25:
        raise ValueError('Selected owner/world position is not observed.')
    if require_start and distance(view['positions']['companion'], scene['layout']['start']) > .25:
        raise ValueError('Selected companion start is not observed.')
    for cell in view['cells']:
        expected = 'stone' if cell['y'] == 63 else 'air'
        wall = scene['layout']['wall']
        if wall and cell['x'] == wall['x'] and cell['z'] == wall['z'] and cell['y'] in {64, 65}:
            expected = 'stone'
        if cell['name'] != expected:
            raise ValueError('Independent client did not observe the frozen world geometry.')
    return view


def run(args):
    began = time.monotonic()
    if os.name != 'posix':
        raise RuntimeError('Execute only on Gamma Linux through the hidden controller.')
    project = Path(__file__).resolve().parents[1]
    if project.name in {'gamma-main', 'gamma-modular-phase1'}:
        raise RuntimeError('An isolated coordination candidate is required.')
    receipt = validate_approval(args.approval, args.server_jar, args.java)
    available = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                         if line.startswith('MemAvailable:'))) * 1024
    if available < 6_442_450_944 or shutil.disk_usage('/tmp').free < 3_221_225_472:
        raise RuntimeError('The packet needs at least 6 GiB available RAM and 3 GiB temporary space.')
    os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:2])
    version = subprocess.run([str(args.java), '-version'], capture_output=True, text=True, timeout=10)
    if version.returncode or not any(mark in version.stderr + version.stdout for mark in ('"21.', ' 21.')):
        raise RuntimeError('The approved runtime must identify Java 21.')
    production_sidecar = Path('/home/neety/Documents/gamma-main/sidecars/minecraft')
    if not (production_sidecar / 'node_modules/mineflayer/package.json').is_file():
        raise RuntimeError('Previously inventoried installed sidecar dependencies are unavailable.')
    rows = scenarios()
    temp = Path(tempfile.mkdtemp(prefix='shana-p6-minecraft-', dir='/tmp'))
    world, source = temp / 'world', temp / 'sidecar'
    world.mkdir()
    source.mkdir()
    result = packet()
    result.update(status='running', approval_sha256=sha(args.approval), java_sha256=sha(args.java),
                  server_jar_sha256=sha(args.server_jar), steps=[], temporary_root=str(temp))
    children = []
    stopped = threading.Event()
    watch = None
    api_url = None
    output = project / 'data/coordination/p6' / ('minecraft-world-' + uuid.uuid4().hex)
    output.mkdir(parents=True, mode=0o700)
    try:
        for path in (project / 'sidecars/minecraft/src').glob('*.ts'):
            target = source / 'src' / path.name
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(path.read_bytes())
        (source / 'package.json').write_bytes((project / 'sidecars/minecraft/package.json').read_bytes())
        (source / 'node_modules').symlink_to(production_sidecar / 'node_modules', target_is_directory=True)
        compiler = json.loads((project / 'sidecars/minecraft/tsconfig.json').read_text())
        compiler['compilerOptions'].update(rootDir='src', outDir='dist')
        compiler['include'] = ['src/**/*.ts']
        (source / 'tsconfig.json').write_text(json.dumps(compiler))
        built = subprocess.run(['node', str(production_sidecar / 'node_modules/typescript/bin/tsc'),
                                '-p', str(source / 'tsconfig.json')], cwd=source, capture_output=True,
                               text=True, timeout=30)
        if built.returncode:
            raise RuntimeError('Temporary sidecar build failed: ' + (built.stdout + built.stderr)[-1000:])
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
        properties = {'server-ip': '127.0.0.1', 'server-port': str(port), 'online-mode': 'false',
                      'enable-query': 'false', 'enable-rcon': 'false', 'enable-status': 'true',
                      'gamemode': 'creative', 'difficulty': 'peaceful', 'max-players': '2',
                      'spawn-protection': '0', 'view-distance': '3', 'simulation-distance': '3',
                      'level-name': 'disposable-world', 'level-type': 'minecraft:flat',
                      'level-seed': '471027', 'generate-structures': 'false', 'sync-chunk-writes': 'true',
                      'enforce-secure-profile': 'false', 'max-world-size': '64', 'allow-nether': 'false'}
        (world / 'server.properties').write_text(''.join(key + '=' + value + '\n' for key, value in properties.items()))
        (world / 'eula.txt').write_text('# Owner acceptance recorded in the approved packet.\neula=true\n')
        child_environment = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LANG': 'C.UTF-8',
                             'TMPDIR': str(temp), 'XDG_CACHE_HOME': str(temp / 'cache'),
                             'XDG_CONFIG_HOME': str(temp / 'config')}
        server = Child([str(args.java), '-Xms256M', '-Xmx2048M', '-XX:ActiveProcessorCount=2',
                        '-XX:ParallelGCThreads=2', '-Duser.home=' + str(temp / 'java-home'),
                        '-jar', str(args.server_jar), 'nogui'], cwd=world, env=child_environment, java=True)
        children.append(server)
        def watchdog():
            while not stopped.wait(.5):
                try:
                    size = sum(path.stat().st_size for path in temp.rglob('*') if path.is_file() and not path.is_symlink())
                    stop = time.monotonic() - began >= MAX_SECONDS or size > MAX_DISK_BYTES
                except OSError:
                    stop = True
                if stop:
                    result['watchdog_stop'] = 'time_or_disk_budget'
                    for child in reversed(children):
                        if child.process.poll() is None:
                            child.process.terminate()
                    return
        watch = threading.Thread(target=watchdog, daemon=True)
        watch.start()
        until(lambda: any('Done (' in line for line in server.recent), 120)
        for command in ['fill -12 63 -12 12 63 12 minecraft:stone', 'setworldspawn 0 64 0',
                        'gamerule doMobSpawning false', 'gamerule doDaylightCycle false', 'time set day']:
            server.send(command)
        actor = Child(['node', '--max-old-space-size=384', str(project / 'scripts/verify_shared_minecraft_world.cjs'),
                       '--approved-owner-actor'], cwd=temp, env=child_environment)
        children.append(actor)
        actor.send(json.dumps({'approved': True, 'host': '127.0.0.1', 'port': port, 'version': VERSION,
                               'owner': OWNER, 'companion': COMPANION,
                               'dependencyPackage': str(production_sidecar / 'package.json'), 'scenarios': rows}))
        until(lambda: any('actor_spawned' in line for line in actor.recent), 35)
        from gamma.coordination.app import build_app
        from gamma.coordination.hosting import loopback_server
        from gamma.coordination.shared_activity_minecraft import SharedMinecraft
        from gamma.integrations.minecraft.coordinator import MinecraftCoordinator
        from gamma.integrations.minecraft.websocket import create_minecraft_control_router
        from gamma.coding.durable_practice import PracticeRunner
        from gamma.schemas.response import AssistantResponse
        import httpx
        token, control_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        coordinator = MinecraftCoordinator(enabled=True)
        app = build_app(temp / 'state', bearer_token=token, run_background=False,
                        draft_factory=lambda: lambda *_: AssistantResponse(delivery_mode='silent', response_action='stay_silent'),
                        runner_factory=lambda store: PracticeRunner(store, propose=lambda *_: {}, check=lambda *_: {}),
                        shared_minecraft_factory=lambda core, activity: SharedMinecraft(activity, coordinator, private_test_world=True))
        app.include_router(create_minecraft_control_router(coordinator=coordinator, control_token=control_token,
            received_observer=lambda message, received: app.state.shared_minecraft.record_received_terminal(message, received)))
        with loopback_server(app) as api_url:
            with httpx.Client(base_url=api_url, headers={'Authorization': 'Bearer ' + token}, timeout=30) as client:
                prefix = '/v1/coordination/shared-activity'
                def post(path, **values):
                    response = client.post(prefix + path, json={'audience': 'owner_private', 'request_id': uuid.uuid4().hex, **values})
                    response.raise_for_status(); return response.json()
                post('/control', action='select', values={'source_id': 'disposable_world', 'label': 'Automated private development world',
                                                        'kind': 'minecraft_state', 'seconds': 600})
                env = {**child_environment, 'SHANA_MINECRAFT_CONTROL_WEBSOCKET_URL': api_url.replace('http:', 'ws:') + '/v1/minecraft/control',
                       'SHANA_MINECRAFT_CONTROL_TOKEN': control_token, 'SHANA_MINECRAFT_SERVER_HOST': '127.0.0.1',
                       'SHANA_MINECRAFT_SERVER_PORT': str(port), 'SHANA_MINECRAFT_VERSION': VERSION,
                       'SHANA_MINECRAFT_ACCOUNT_MODE': 'offline', 'SHANA_MINECRAFT_BOT_USERNAME': COMPANION,
                       'SHANA_MINECRAFT_OWNER_USERNAME': OWNER, 'SHANA_MINECRAFT_HEARTBEAT_SECONDS': '1'}
                sidecar = Child(['node', '--max-old-space-size=384', str(source / 'dist/index.js')], cwd=temp, env=env)
                children.append(sidecar)
                until(lambda: coordinator.status().handshake_complete, 15)
                post('/minecraft/permit', seconds=120, maximum_commands=10)
                joined = post('/minecraft/action', name='join', arguments={})
                until(lambda: app.state.shared_minecraft.status()['coordinator']['minecraft_connection_state'] == 'connected', 30)
                until(lambda: any(row['id'] == joined['id'] and row['status'] == 'observed_terminal'
                                  for row in app.state.shared_minecraft.status()['commands']), 15)
                trial_spec = {'baseline_tactic': 2, 'practice_tactics': [2, 4],
                              'scenarios': [{key: row[key] for key in ('id', 'partition', 'family', 'variant_sha256')} for row in rows]}
                trial = post('/minecraft/trials', spec=trial_spec)['trial']
                result['frozen_trial'] = trial
                for scene in rows:
                    for command in command_plan(scene):
                        server.send(command)
                    time.sleep(.5)
                    def probe(require_start):
                        response = actor.json_request({'action': 'probe', 'scenario_id': scene['id'], 'request_id': uuid.uuid4().hex})
                        return validate_probe(scene, response, require_start=require_start)
                    def ready_probe():
                        try:
                            return probe(True)
                        except ValueError:
                            return None
                    before = until(ready_probe, 10)
                    proof_sha = hashlib.sha256(canonical(before)).hexdigest()
                    app.state.shared_minecraft.skills.attest_scenario(trial['id'], scene['id'], variant_sha256=scene['variant_sha256'],
                                                                     proof_sha256=proof_sha, observed_at=time.time())
                    request_id = uuid.uuid4().hex
                    response = client.post(prefix + '/minecraft/trials/run', json={'audience': 'owner_private',
                        'request_id': request_id, 'trial_id': trial['id'], 'scenario_id': scene['id']})
                    response.raise_for_status()
                    observed = until(lambda: next((row for row in app.state.shared_minecraft.skills.inspect(trial['id'])['attempts']
                        if row['scenario_id'] == scene['id'] and row['status'] == 'observed'), None), 25)
                    time.sleep(.2)
                    after = probe(False)
                    moved = distance(before['positions']['companion'], after['positions']['companion'])
                    final_distance = distance(after['positions']['owner'], after['positions']['companion'])
                    if observed['outcome'] == 'completed':
                        if moved <= .8 or final_distance > observed['tactic'] + .5:
                            raise AssertionError('Received completion did not match independently observed movement/arrival.')
                    else:
                        command = app.state.core.store.read('shared_game_command', request_id)
                        if (scene['layout']['wall'] is None or observed['tactic'] != 2 or moved <= .8
                                or observed['outcome'] != 'failed' or command['terminal'].get('failure_code')
                                not in {'PATH_NOT_FOUND', 'PATH_STALLED', 'SAFETY_POLICY_BLOCKED'}):
                            raise AssertionError('A non-mechanical failure cannot be scored as an arrival-spacing result.')
                    time.sleep(.8)
                    stationary = probe(False)
                    if distance(after['positions']['companion'], stationary['positions']['companion']) > .15:
                        raise AssertionError('Companion kept moving after terminal result.')
                    result['steps'].append({'scenario': scene, 'received_outcome': observed, 'before': before, 'after': after,
                                            'movement_blocks': moved, 'final_owner_distance': final_distance,
                                            'independent_stationarity_checked': True})
                result['comparison'] = app.state.shared_minecraft.skills.inspect(trial['id'])
                if not result['comparison']['complete']:
                    raise AssertionError('Not every frozen scenario has a received outcome.')
                if not result['comparison']['observed_bounded_improvement']:
                    raise AssertionError('No measured improvement on the frozen held-out arrival variants.')
                clear = rows[0]
                for command in command_plan(clear):
                    server.send(command)
                time.sleep(.5)
                def clear_probe():
                    value = actor.json_request({'action': 'probe', 'scenario_id': clear['id'], 'request_id': uuid.uuid4().hex})
                    return validate_probe(clear, value, require_start=False)
                before_stop = clear_probe()
                post('/minecraft/action', name='follow_owner', arguments={'follow_distance': 3, 'lease_duration_seconds': 5})
                moving = until(lambda: (value if distance(value['positions']['companion'], before_stop['positions']['companion']) > .5 else None)
                               if (value := clear_probe()) else None, 5)
                post('/minecraft/revoke')
                time.sleep(.3)
                stopped_view = clear_probe()
                time.sleep(.8)
                still_stopped = clear_probe()
                if distance(stopped_view['positions']['companion'], still_stopped['positions']['companion']) > .15:
                    raise AssertionError('Actual server-observed movement continued after emergency revocation.')
                result['emergency_stop_observed'] = coordinator.status().emergency_stop_active
                result['emergency_stop_world_views'] = [moving, stopped_view, still_stopped]
                result['status'] = 'real_world_probe_complete'
    except BaseException as exc:
        result.update(status='failed', error={'type': type(exc).__name__, 'message': str(exc)[:500]})
        raise
    finally:
        stopped.set()
        if watch is not None:
            watch.join(2)
        for child in reversed(children):
            child.stop('stop' if child is children[0] else None)
        result['child_processes_absent'] = all(child.process.poll() is not None for child in children)
        result['elapsed_seconds'] = round(time.monotonic() - began, 3)
        result['bounded_logs'] = [{'pid': child.process.pid, 'tail': list(child.recent)[-60:]} for child in children]
        result['world_cleaned'] = False
        result['world_cleaned'] = cleanup_temp(temp)
        (output / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'report': str(output / 'report.json'), 'status': result['status'],
                          'child_processes_absent': result['child_processes_absent'], 'world_cleaned': result['world_cleaned']}))


def self_test():
    rows = scenarios()
    assert len(rows) == len({row['variant_sha256'] for row in rows}) == 6
    assert all(len(command_plan(row)) <= 6 for row in rows)
    assert all(row['layout']['owner']['x'] - row['layout']['start']['x'] <= 7 for row in rows)
    assert packet()['downloads_performed'] is False
    print(json.dumps({'self_test': True, 'no_server_started': True, 'frozen_variants': 6}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--execute-approved', action='store_true')
    parser.add_argument('--approval', type=Path)
    parser.add_argument('--server-jar', type=Path)
    parser.add_argument('--java', type=Path)
    args = parser.parse_args()
    if args.self_test:
        self_test()
    elif args.execute_approved:
        if not all((args.approval, args.server_jar, args.java)):
            parser.error('Execution requires the explicit owner receipt and exact approved artifacts.')
        run(args)
    else:
        print(json.dumps(packet(), indent=2))


if __name__ == '__main__':
    main()
