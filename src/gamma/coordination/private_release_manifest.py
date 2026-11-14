"""Exact private-release proposal, source pins and read-only preflight."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import subprocess
import uuid
from urllib.parse import urlsplit

from .store import private_path

ACTIVE = ('private_text', 'private_voice', 'presence', 'work', 'personal', 'growth',
          'selected_source', 'private_rehearsal', 'phone')
INACTIVE = ('public_twitch', 'live_discord', 'real_minecraft')
LEGACY = ('shana_service', 'dashboard_service', 'proactive_selector', 'stream_selector',
          'voice_selector', 'coding_autonomous_service', 'coding_idle_service',
          'improvement_selector', 'legacy_asgi_processes', 'external_twitch', 'external_discord', 'minecraft_actions')
UNITS = ('gamma-shana.service', 'gamma-dashboard.service',
         'gamma-coding-autonomous.service', 'gamma-coding-idle.service')
CRITICAL = {'src/gamma/coordination/private_release.py',
            'src/gamma/coordination/private_release_manifest.py',
            'src/gamma/coordination/core.py', 'src/gamma/coordination/app.py',
            'src/gamma/coordination/transfer.py', 'src/gamma/coordination/dashboard.py'}
MODULE_COMPONENTS = {
    'gamma.integrations.twitch': {'gamma.integrations.twitch.worker', 'gamma.integrations.twitch.eventsub'},
    'gamma.integrations.discord': {'gamma.integrations.discord.worker'},
    'gamma.coordination.discord_client': {'gamma.coordination.discord_client'},
    'gamma.coding': {'gamma.coding.worker', 'gamma.coding.autonomous', 'gamma.improvement.worker'},
    'gamma.legacy_apps': {'gamma.main:app', 'gamma.dashboard.main:app'},
}
VOICE_FIELDS = ('profile_id','profile_label','provider','tts_model','tts_voice','tts_format',
    'piper_executable','piper_model_path','piper_config_path','piper_speaker_id','rvc_enabled','rvc_python',
    'rvc_project_root','rvc_model_name','rvc_index_path','rvc_pitch','rvc_formant','rvc_f0_method',
    'rvc_index_rate','rvc_filter_radius','rvc_rms_mix_rate','rvc_protect','rvc_resample_sr','rvc_device',
    'qwen_tts_endpoint','qwen_tts_reference_audio','qwen_tts_reference_text','qwen_tts_language',
    'qwen_tts_speaker','qwen_tts_instruct','qwen_tts_timeout_seconds','denoise_enabled','denoise_strength','denoise_gain')


def effective_voice_pin(profile_id, project_root):
    """Digest safe resolved options and actual reference bytes, never expose content."""
    from ..config import settings
    from ..voice.voice_profiles import resolve_tts_config
    if settings.project_root.resolve() != absolute_path(str(project_root)):
        raise ValueError('voice_configuration_must_resolve_in_release_source')
    config=resolve_tts_config(profile_id)
    if config.profile_id != profile_id:
        raise ValueError('named_release_voice_profile_missing')
    if config.provider.lower() not in {'qwen','qwen-tts','qwen_tts','qwentts','piper','stub'}:
        raise ValueError('private_release_voice_provider_must_be_local')
    if config.qwen_tts_endpoint:
        url=urlsplit(config.qwen_tts_endpoint)
        if (url.scheme!='http' or url.hostname not in {'127.0.0.1','::1'} or url.username
            or url.password or url.query or url.fragment or not url.port):
            raise ValueError('private_release_voice_endpoint_must_be_clean_loopback')
    elif config.provider.lower() in {'qwen','qwen-tts','qwen_tts','qwentts'}:
        raise ValueError('private_release_voice_endpoint_missing')
    extra=config.qwen_tts_extra_json
    from typing import get_args
    from ..schemas.response import EmotionTag
    from .private_release_voice import validate_voice_extra
    try:
        validate_voice_extra(extra,get_args(EmotionTag))
    except ValueError:
        raise ValueError('release_voice_extra_options_not_reviewed') from None
    safe={name:getattr(config,name) for name in VOICE_FIELDS}
    safe['qwen_tts_extra_json']=extra
    reference=None
    if config.qwen_tts_reference_audio:
        path=Path(config.qwen_tts_reference_audio).expanduser()
        if not path.is_absolute():path=absolute_path(str(project_root))/path
        path=private_path(path)
        if not path.is_file() or path.stat().st_size>32*1024*1024:
            raise ValueError('release_voice_reference_missing_or_unbounded')
        reference=file_hash(path)
    return {'configuration_sha256':digest(safe),'reference_audio_sha256':reference,'provider':config.provider.lower()}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def file_hash(path):
    return hashlib.sha256(private_path(path).read_bytes()).hexdigest()


def absolute_path(value):
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise ValueError('release_path_must_be_absolute')
    path = private_path(value)
    if path == Path(path.anchor):
        raise ValueError('release_path_cannot_be_filesystem_root')
    return path


def validate_lock_root(path):
    path = absolute_path(str(path))
    if path.exists():
        info = path.stat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('identity_lock_root_must_be_directory')
        if os.name == 'posix' and (info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise ValueError('identity_lock_root_must_be_owned_and_private')
    return path


def validate_discord_binding(value):
    from .discord_scope import validate_scope, discord_id
    if not isinstance(value, dict) or set(value) != {
        'owner_user_id', 'dm_channel_id', 'voice_guild_id', 'voice_channel_id', 'bot_user_id'}:
        raise ValueError('invalid_private_discord_binding')
    validate_scope(**{k: value[k] for k in value if k != 'bot_user_id'})
    discord_id(value['bot_user_id'])
    if value['bot_user_id'] == value['owner_user_id']:
        raise ValueError('discord_bot_cannot_be_owner')
    return dict(value)


def validate_contract(roles, effects, coverage, targets, *, private_discord=None):
    if (not isinstance(roles, dict) or set(roles) != {'api', 'dashboard'}
        or any(type(roles[k]) is not int for k in roles)
        or roles != {'api': 8000, 'dashboard': 8001}):
        raise ValueError('invalid_release_role_mapping')
    expected = {**dict.fromkeys(ACTIVE, 'core'), **dict.fromkeys(INACTIVE, 'inactive')}
    if private_discord is not None:
        validate_discord_binding(private_discord)
        expected['live_discord'] = 'owner_private'
    if not isinstance(effects, dict) or effects != expected:
        raise ValueError('private_release_scope_incomplete_or_public')
    if (not isinstance(targets, set) or not targets
        or any(not isinstance(x, str) or not x for x in targets)
        or not isinstance(coverage, dict) or set(coverage) != set(LEGACY)
        or any(not isinstance(v, str) or not v or v not in targets for v in coverage.values())):
        raise ValueError('legacy_effect_coverage_incomplete')


def source_inventory(root):
    root = absolute_path(str(root))
    files = {}
    for path in sorted((root / 'src/gamma').rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
            files[path.relative_to(root).as_posix()] = file_hash(path)
    for name in ('pyproject.toml', 'scripts/private_release.py', 'scripts/verify_private_release.py'):
        if (root / name).is_file():
            files[name] = file_hash(root / name)
    for path in sorted((root / 'config').glob('*')):
        if path.is_file() and not path.name.endswith('.local.toml') and path.suffix in {'.toml', '.yaml', '.json'}:
            files[path.relative_to(root).as_posix()] = file_hash(path)
    if not CRITICAL.issubset(files) or len(files) > 5000:
        raise ValueError('release_source_inventory_incomplete')
    return files


def state_identity(root):
    path = absolute_path(str(root)) / 'coordination.sqlite3'
    if not path.is_file():
        raise ValueError('release_state_missing')
    with closing(sqlite3.connect(private_path(path).as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        if db.execute('PRAGMA quick_check').fetchone() != ('ok',):
            raise ValueError('release_state_integrity_failed')
        row = db.execute("SELECT body FROM records WHERE kind='personal_meta' AND id='state'").fetchone()
        if not row:
            raise ValueError('release_identity_missing')
        meta = json.loads(row[0])
        if meta.get('schema_version') != 1 or not re.fullmatch('[a-f0-9]{32}', str(meta.get('identity_id', ''))):
            raise ValueError('unsupported_release_identity')
        return meta['identity_id']


def load_manifest(path):
    path = absolute_path(str(path))
    if path.stat().st_size > 2_000_000:
        raise ValueError('release_manifest_too_large')
    value = json.loads(path.read_text(encoding='utf-8'))
    validate_manifest(value)
    return value


def validate_manifest(value):
    fields = {'format', 'release_id', 'status', 'source', 'state', 'reader', 'original_identity_lock_roots',
              'identity_lock_root', 'roles', 'models', 'effects', 'coverage', 'drain_targets'}
    if (not isinstance(value, dict) or set(value) not in (fields, fields | {'discord'}, fields | {'inference_profile'}, fields | {'discord', 'inference_profile'})
        or type(value['format']) is not int or value['format'] != 1):
        raise ValueError('invalid_release_manifest')
    if 'discord' in value:
        validate_discord_binding(value['discord'])
    if 'inference_profile' in value:
        from .inference_profile import release_inference_options
        if value['inference_profile'] is None:
            raise ValueError('explicit_private_inference_profile_required')
        release_inference_options(value['inference_profile'])
    if (not re.fullmatch('[a-f0-9]{32}', str(value['release_id']))
        or value['status'] not in {'proposed', 'owner_approved'}):
        raise ValueError('invalid_release_status')
    for key in ('source', 'reader'):
        entry = value[key]
        if not isinstance(entry, dict) or set(entry) != {'root', 'files', 'kind'} or entry['kind'] != 'coordination_current':
            raise ValueError('compatible_current_reader_required')
        absolute_path(entry['root'])
        pins = entry['files']
        if not isinstance(pins, dict) or not CRITICAL.issubset(pins) or len(pins) > 5000:
            raise ValueError('release_source_pins_missing')
        for name, sha in pins.items():
            path = PurePosixPath(name)
            if (path.is_absolute() or '..' in path.parts or '\\' in name
                or not re.fullmatch('[a-f0-9]{64}', str(sha))):
                raise ValueError('invalid_release_source_pin')
    state = value['state']
    if not isinstance(state, dict) or set(state) != {'active_root', 'baseline_bundle', 'baseline_manifest_sha256', 'identity_id'}:
        raise ValueError('invalid_release_state_reference')
    active, source = absolute_path(state['active_root']), absolute_path(value['source']['root'])
    if not active.is_relative_to(source / 'data/coordination'):
        raise ValueError('release_state_must_be_explicit_coordination_state')
    absolute_path(state['baseline_bundle'])
    if not re.fullmatch('[a-f0-9]{64}', str(state['baseline_manifest_sha256'])) or not re.fullmatch('[a-f0-9]{32}', str(state['identity_id'])):
        raise ValueError('invalid_release_state_pin')
    lock = validate_lock_root(value['identity_lock_root'])
    if any(lock.is_relative_to(absolute_path(value[key]['root'])) for key in ('source', 'reader')):
        raise ValueError('shared_identity_lock_must_be_outside_release_checkouts')
    originals=value['original_identity_lock_roots']
    if (not isinstance(originals,list) or not 1<=len(originals)<=4
        or len(set(originals))!=len(originals)):
        raise ValueError('explicit_original_identity_lock_roots_required')
    for path in originals:validate_lock_root(path)
    for key in ('source','reader'):
        validate_lock_root(absolute_path(value[key]['root'])/'data/coordination/identities')
    models = value['models']
    if (not isinstance(models, dict) or set(models) != {'primary', 'vision','voice','voice_pin'}
        or any(not isinstance(models[k], str) or not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,160}', models[k]) for k in ('primary','vision','voice'))):
        raise ValueError('invalid_release_model_reference')
    voice=models['voice_pin']
    if (not isinstance(voice,dict) or set(voice)!={'configuration_sha256','reference_audio_sha256','provider'}
        or not re.fullmatch('[a-f0-9]{64}',str(voice['configuration_sha256']))
        or voice['reference_audio_sha256'] is not None and not re.fullmatch('[a-f0-9]{64}',str(voice['reference_audio_sha256']))
        or voice['provider'] not in {'qwen','qwen-tts','qwen_tts','qwentts','piper','stub'}):
        raise ValueError('invalid_effective_voice_pin')
    targets = value['drain_targets']
    if not isinstance(targets, dict) or not targets or len(targets) > 20:
        raise ValueError('invalid_legacy_drain_targets')
    validate_contract(value['roles'], value['effects'], value['coverage'], set(targets),
                      private_discord=value.get('discord'))
    for target in targets.values():
        if not isinstance(target, dict) or set(target) != {'kind', 'name'}:
            raise ValueError('invalid_legacy_drain_target')
        if target['kind'] == 'user_unit' and target['name'] in UNITS:
            continue
        if target['kind'] == 'module_census' and target['name'] in MODULE_COMPONENTS:
            continue
        raise ValueError('unrecognised_legacy_drain_target')
    if not set(UNITS).issubset(targets) or any(targets[name] != {'kind': 'user_unit', 'name': name} for name in UNITS):
        raise ValueError('required_legacy_services_missing')
    if set(targets) != set(UNITS) | set(MODULE_COMPONENTS) or any(
        targets[name] != {'kind':'module_census','name':name} for name in MODULE_COMPONENTS):
        raise ValueError('required_legacy_module_census_missing')
    required_coverage = {'shana_service':'gamma-shana.service', 'dashboard_service':'gamma-dashboard.service',
        'proactive_selector':'gamma-shana.service', 'stream_selector':'gamma-shana.service',
        'voice_selector':'gamma-shana.service', 'coding_autonomous_service':'gamma-coding-autonomous.service',
        'coding_idle_service':'gamma-coding-idle.service', 'minecraft_actions':'gamma-shana.service',
        'improvement_selector':'gamma.coding',
        'legacy_asgi_processes':'gamma.legacy_apps',
        'external_twitch':'gamma.integrations.twitch', 'external_discord':'gamma.coordination.discord_client'}
    if value['coverage'] != required_coverage:
        raise ValueError('legacy_selector_mapped_to_wrong_drain_target')


def observe_target(target):
    """Observe known legacy entry points only; never stops or changes anything."""
    if os.name != 'posix':
        return {'inactive_observed': False, 'reason': 'Gamma_posix_observation_required'}
    if target['kind'] == 'user_unit':
        try:
            result = subprocess.run(['systemctl', '--user', 'show', target['name'],
                '--property=ActiveState,SubState,MainPID,LoadState'], capture_output=True,
                text=True, timeout=5)
            values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
            ok = (result.returncode == 0 and values.get('LoadState') == 'loaded'
                  and values.get('ActiveState') == 'inactive' and values.get('MainPID') == '0')
            return {'inactive_observed': ok, 'unit': target['name'], 'observed': values}
        except (OSError, subprocess.TimeoutExpired):
            return {'inactive_observed': False, 'reason': 'unit_observation_failed'}
    matches = []
    try:
        for path in Path('/proc').glob('[0-9]*/cmdline'):
            try:
                if path.stat().st_uid != os.getuid():
                    continue
                argv = path.read_bytes().decode('utf-8', 'replace').split('\0')
            except PermissionError:
                return {'inactive_observed': False, 'reason': 'owned_process_census_incomplete'}
            except (OSError, ProcessLookupError):
                continue
            if known_module_argv(argv, target['name']):
                matches.append(int(path.parent.name))
    except OSError:
        return {'inactive_observed': False, 'reason': 'process_census_failed'}
    return {'inactive_observed': not matches, 'matching_pids': matches,
            'limit': 'Only this exact known module entry point; not unknown noncooperating writers.'}


def known_module_argv(argv, component):
    """Recognize actual known python -m adapter children and their own descendants."""
    known = MODULE_COMPONENTS[component]
    if component == 'gamma.legacy_apps':
        module_uvicorn = any(item == '-m' and argv[index + 1] == 'uvicorn'
                             for index,item in enumerate(argv[:-1]))
        executable_uvicorn = bool(argv) and any(Path(item).name in {'uvicorn','uvicorn.exe'} for item in argv[:2])
        launcher = any(Path(item).name in {'start_shana.py','start_dashboard.py','start_all.sh','startup.sh'} for item in argv[:3])
        return launcher or (module_uvicorn or executable_uvicorn) and any(item in known for item in argv)
    for index, item in enumerate(argv[:-1]):
        if item == '-m' and argv[index + 1] in known:
            return True
    return False


def preflight(value, *, purpose='permanent'):
    validate_manifest(value)
    if purpose not in {'permanent', 'test'}:
        raise ValueError('unsupported_release_preflight_purpose')
    failures = []
    for key in ('source', 'reader'):
        if source_inventory(value[key]['root']) != value[key]['files']:
            failures.append(key + '_source_changed')
    state = value['state']
    if file_hash(absolute_path(state['baseline_bundle']) / 'manifest.json') != state['baseline_manifest_sha256']:
        failures.append('baseline_manifest_changed')
    if state_identity(state['active_root']) != state['identity_id']:
        failures.append('active_identity_changed')
    baseline = json.loads((absolute_path(state['baseline_bundle']) / 'manifest.json').read_text(encoding='utf-8'))
    if baseline.get('identity_id') != state['identity_id']:
        failures.append('baseline_identity_changed')
    from .transfer import validate_manifest_entries
    validate_manifest_entries(baseline.get('files'))
    for name, sha in baseline['files'].items():
        if file_hash(absolute_path(state['baseline_bundle']) / name) != sha:
            failures.append('baseline_artifact_changed')
    observations = {}
    if purpose == 'permanent':
        if value['status'] != 'owner_approved':
            failures.append('owner_activation_decision_missing')
        observations = {key: observe_target(target) for key, target in value['drain_targets'].items()}
        if not all(item['inactive_observed'] for item in observations.values()):
            failures.append('enumerated_legacy_drain_not_observed')
    return {'ok': not failures, 'purpose': purpose, 'blockers': failures,
            'release_id': value['release_id'], 'manifest_sha256': digest(value),
            'drain_observations': observations,
            'permanent_activation_performed': False,
            'noncooperating_legacy_writers_fenced': False,
            'known_original_identity_lock_roots_pinned':True,
            'limit': 'A cooperative identity lease and observed drain are not an OS fence for uncooperative legacy writers.'}


def compatibility_lock_roots(value):
    return sorted({str(validate_lock_root(path)) for path in value['original_identity_lock_roots']} |
        {str(validate_lock_root(absolute_path(value[key]['root'])/'data/coordination/identities')) for key in ('source','reader')})


def prepare_manifest(source_root, active_root, baseline_bundle, reader_root, identity_lock_root, *, model, voice, original_lock_roots):
    from ..config import settings
    source_root, reader_root = absolute_path(str(source_root)), absolute_path(str(reader_root))
    baseline_bundle = absolute_path(str(baseline_bundle))
    targets = {unit: {'kind': 'user_unit', 'name': unit} for unit in UNITS}
    targets.update({name: {'kind': 'module_census', 'name': name} for name in MODULE_COMPONENTS})
    coverage = {name: 'gamma-shana.service' for name in LEGACY}
    coverage.update(dashboard_service='gamma-dashboard.service',
                    coding_autonomous_service='gamma-coding-autonomous.service',
                    coding_idle_service='gamma-coding-idle.service',
                    improvement_selector='gamma.coding',
                    legacy_asgi_processes='gamma.legacy_apps',
                    external_twitch='gamma.integrations.twitch', external_discord='gamma.coordination.discord_client')
    value = {'format': 1, 'release_id': uuid.uuid4().hex, 'status': 'proposed',
             'source': {'root': str(source_root), 'files': source_inventory(source_root), 'kind': 'coordination_current'},
             'reader': {'root': str(reader_root), 'files': source_inventory(reader_root), 'kind': 'coordination_current'},
             'state': {'active_root': str(absolute_path(str(active_root))), 'baseline_bundle': str(baseline_bundle),
                       'baseline_manifest_sha256': file_hash(baseline_bundle / 'manifest.json'),
                       'identity_id': state_identity(active_root)},
             'identity_lock_root': str(absolute_path(str(identity_lock_root))),
             'original_identity_lock_roots':[str(validate_lock_root(path)) for path in original_lock_roots],
             'roles': {'api': 8000, 'dashboard': 8001}, 'models': {'primary': model,
                 'vision':str(settings.local_llm_vision_model or model), 'voice': voice,
                 'voice_pin':effective_voice_pin(voice,source_root)},
             'effects': {**dict.fromkeys(ACTIVE, 'core'), **dict.fromkeys(INACTIVE, 'inactive')},
             'coverage': coverage, 'drain_targets': targets}
    validate_manifest(value)
    return value
