"""Candidate-only practical work using the coordinator's durable activity journal."""
from copy import deepcopy
import ast
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile
import time
from types import SimpleNamespace

from ..coding.durable_practice import PracticeYield, TERMINAL
from ..coding.practice import _digest
from ..coding.practice_artifacts import ArtifactStore
from ..coding import sandbox as _sandbox
from .store import process_lock
from . import work_models as _models


def _versions():
    return {name: hashlib.sha256(Path(path).read_bytes()).hexdigest() for name, path in (
        ('work', __file__), ('models', _models.__file__), ('sandbox', _sandbox.__file__))}


_LOADED_VERSIONS = _versions()


def _contract(spec):
    versions = _versions()
    if versions != _LOADED_VERSIONS:
        raise ValueError('work_implementation_changed_restart_required')
    raw = json.dumps({'spec': spec, 'versions': versions}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _run_limits(run):
    if (not isinstance(run, dict) or run.get('kind') != 'practical'
        or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,100}', str(run.get('id', '')))
        or type(run.get('maximum_attempts')) is not int or not 1 <= run['maximum_attempts'] <= 5
        or type(run.get('budget_seconds')) not in (int, float)
        or not math.isfinite(run['budget_seconds']) or not 1 <= run['budget_seconds'] <= 1800
        or type(run.get('created_at')) not in (int, float) or not math.isfinite(run['created_at'])
        or type(run.get('expires_at')) not in (int, float) or not math.isfinite(run['expires_at'])
        or not run['created_at'] < run['expires_at'] <= run['created_at'] + run['budget_seconds'] + .01
        or run.get('desired_state') not in {'running', 'pause', 'stop'}):
        raise ValueError('invalid_work_run_scope')


def _blocker(value):
    if (not isinstance(value, dict) or set(value) != {'kind', 'question'}
        or not isinstance(value['kind'], str)
        or value['kind'] not in {'missing_input', 'missing_capability', 'scope_decision'}
        or not isinstance(value['question'], str) or not value['question'].strip()
        or not 1 <= len(value['question']) <= 500):
        raise ValueError('invalid_work_blocker')
    return dict(value)


class WorkRunner:
    def __init__(self, store, *, propose, check=_sandbox.run_practice_check, clock=time.time):
        self.store, self.propose, self.check, self.clock = store, propose, check, clock
        self.artifacts = ArtifactStore(store.root / 'artifacts')

    def prepare(self, spec):
        spec = _models.validate_spec(spec.model_dump() if hasattr(spec, 'model_dump') else spec)
        source = self.artifacts.put({'files': spec['files']})
        research = {'scope': 'selected_private_files_only', 'network_used': False,
                    'observations': [{'path': path, 'sha256': hashlib.sha256(text.encode()).hexdigest(),
                        'utf8_bytes': len(text.encode()), 'excerpt': text[:800],
                        'excerpt_is_partial': len(text) > 800}
                        for path, text in sorted(spec['files'].items())]}
        checkpoint = {'version': 1, 'kind': 'practical', 'spec': spec, 'contract': _contract(spec),
            'original_source_ref': source, 'source_ref': source, 'source_sha256': _digest(spec['files']),
            'stage': 'need_research' if spec['mode'] == 'research' else 'baseline',
            'attempt_count': 0, 'checks': [], 'feedback': [], 'attempts': [],
            'plan_ref': None, 'research_ref': self.artifacts.put(research),
            'status': 'pending', 'reason': 'authorized_candidate_only',
            'production_verified': False, 'activation_allowed': False}
        return self.artifacts.put(checkpoint)

    def verify(self, run):
        _run_limits(run)
        checkpoint = self.artifacts.get(run['checkpoint_ref'])
        original = self.artifacts.get(run['initial_checkpoint_ref'])
        spec = _models.validate_spec(checkpoint['spec'])
        if (checkpoint.get('version') != 1 or checkpoint.get('kind') != 'practical'
            or checkpoint.get('contract') != _contract(spec)
            or original.get('contract') != checkpoint['contract'] or original.get('spec') != spec
            or checkpoint.get('original_source_ref') != original.get('source_ref')
            or checkpoint.get('original_source_ref') != original.get('original_source_ref')):
            raise ValueError('work_contract_changed')
        files = self.artifacts.get(checkpoint['source_ref'])['files']
        original_files = self.artifacts.get(checkpoint['original_source_ref'])['files']
        if (original_files != spec['files'] or set(files) != set(spec['files'])
            or _digest(files) != checkpoint['source_sha256']
            or any(not isinstance(text, str) for text in files.values())
            or sum(len(text.encode()) for text in files.values()) > 60000
            or any(files[path] != spec['files'][path] for path in files if path not in spec['editable'])
            or type(checkpoint.get('attempt_count')) is not int
            or not 0 <= checkpoint['attempt_count'] <= run['maximum_attempts']):
            raise ValueError('work_source_or_scope_changed')
        research = self.artifacts.get(checkpoint['research_ref'])
        if (research.get('scope') != 'selected_private_files_only' or research.get('network_used') is not False
            or {(o['path'], o['sha256']) for o in research['observations']} != {
                (path, hashlib.sha256(text.encode()).hexdigest()) for path, text in spec['files'].items()}):
            raise ValueError('work_research_source_changed')
        if checkpoint.get('plan_ref'):
            self.artifacts.get(checkpoint['plan_ref'])
        if 'blocker' in checkpoint:
            blocker = checkpoint['blocker']
            if (not isinstance(blocker, dict)
                or set(blocker) != {'kind', 'question', 'source_sha256', 'contract'}
                or blocker['source_sha256'] != checkpoint['source_sha256']
                or blocker['contract'] != checkpoint['contract']
                or checkpoint.get('stage') != 'done' or checkpoint.get('status') != 'needs_checker'
                or checkpoint.get('reason') != 'needs_owner_input' or run['status'] == 'completed'):
                raise ValueError('work_blocker_evidence_invalid')
            _blocker({key: blocker[key] for key in ('kind', 'question')})
        if run['status'] == 'completed':
            if checkpoint.get('stage') != 'done' or not checkpoint.get('plan_ref'):
                raise ValueError('work_completion_missing_evidence')
            if spec['mode'] == 'research':
                if checkpoint.get('status') != 'researched' or files != original_files or checkpoint['checks']:
                    raise ValueError('research_cannot_claim_implementation')
            elif (checkpoint.get('status') != 'verified_candidate'
                or checkpoint.get('checks_source_sha256') != checkpoint['source_sha256']
                or len(checkpoint['checks']) != len(spec['checks'])
                or any(c.get('passed') is not True or type(c.get('exit_code')) is not int or c['exit_code'] != 0
                       or c.get('infrastructure_error') is not False
                       or c.get('source_sha256') != checkpoint['source_sha256'] or c.get('contract') != checkpoint['contract']
                       or (c.get('name'), c.get('module'), c.get('kind')) != (s['name'], s['module'], s['kind'])
                       for c, s in zip(checkpoint['checks'], spec['checks']))):
                raise ValueError('work_completion_missing_verified_checks')
        return checkpoint

    def _save(self, run_id, checkpoint, **updates):
        return self.store.update_run(run_id, self.artifacts.put(checkpoint), **updates)

    def boundary(self, run_id, checkpoint):
        run = self.store.read('run', run_id)
        _run_limits(run)
        if run['desired_state'] == 'stop':
            self._save(run_id, checkpoint, status='stopped', reason='owner_stopped')
            return False
        if run['desired_state'] == 'pause':
            self._save(run_id, checkpoint, status='paused', reason='pause_acknowledged')
            return False
        if self.clock() >= run['expires_at']:
            self._save(run_id, checkpoint, status='blocked', reason='budget_exhausted')
            return False
        return True

    def _checks(self, checkpoint, files):
        results = []
        with tempfile.TemporaryDirectory(prefix='gamma-candidate-work-') as directory:
            workspace = Path(directory)
            for path, text in files.items():
                target = workspace / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding='utf-8')
            for spec in checkpoint['spec']['checks']:
                started = time.monotonic()
                try:
                    raw = self.check(workspace, SimpleNamespace(**spec))
                    valid = isinstance(raw, dict) and raw.get('name') == spec['name'] and raw.get('kind') == spec['kind']
                    passed = (valid and raw.get('passed') is True and type(raw.get('exit_code')) is int
                              and raw['exit_code'] == 0 and raw.get('infrastructure_error') is False)
                    result = {**spec, 'passed': bool(passed), 'exit_code': raw.get('exit_code', -1) if valid else -1,
                        'infrastructure_error': raw.get('infrastructure_error') is True if valid else True,
                        'output_tail': str(raw.get('output_tail', ''))[-1800:] if valid else 'invalid_check_result'}
                except Exception as exc:
                    result = {**spec, 'passed': False, 'exit_code': -1, 'infrastructure_error': True,
                              'output_tail': 'check_error:' + type(exc).__name__}
                result.update(duration_ms=round((time.monotonic() - started) * 1000, 3),
                              source_sha256=checkpoint['source_sha256'], contract=checkpoint['contract'])
                results.append(result)
            if any(path.is_symlink() for path in workspace.rglob('*')):
                raise ValueError('work_check_modified_candidate')
            actual = {path.relative_to(workspace).as_posix(): path.read_text(encoding='utf-8')
                      for path in workspace.rglob('*') if path.is_file()}
            if actual != files:
                raise ValueError('work_check_modified_candidate')
        return results

    def run(self, run_id):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,100}', str(run_id)):
            raise ValueError('invalid_work_run_id')
        try:
            with process_lock(self.store.root / 'runs' / (run_id + '.lock')):
                self._run_locked(run_id)
        except BlockingIOError:
            return

    def _run_locked(self, run_id):
        run = self.store.read('run', run_id)
        if not run or run.get('status') in TERMINAL:
            return
        checkpoint = None
        try:
            checkpoint = self.verify(run)
            if not self.boundary(run_id, checkpoint):
                return
            if checkpoint['stage'] == 'inference_started':
                self._save(run_id, checkpoint, status='blocked', reason='uncertain_inference_no_replay')
                return
            self._save(run_id, checkpoint, status='running', reason='resumed_work_checkpoint')
            while checkpoint['stage'] != 'done':
                if not self.boundary(run_id, checkpoint):
                    return
                files = self.artifacts.get(checkpoint['source_ref'])['files']
                if checkpoint['stage'] in {'baseline', 'candidate_saved', 'checking'}:
                    checkpoint['stage'] = 'checking'
                    self._save(run_id, checkpoint)
                    checks = self._checks(checkpoint, files)
                    checkpoint.update(checks=checks, checks_source_sha256=checkpoint['source_sha256'])
                    checkpoint['feedback'] = [{'latest_executed_checks': checks}]
                    if checkpoint['attempts']:
                        checkpoint['attempts'][-1]['checks'] = checks
                    if any(c['infrastructure_error'] for c in checks):
                        checkpoint.update(stage='done', status='blocked', reason='verification_unavailable')
                    elif checks and all(c['passed'] for c in checks):
                        if not checkpoint['plan_ref']:
                            checkpoint['plan_ref'] = self.artifacts.put({'steps': ['Inspect selected source and protected checks.',
                                'Run the protected feature flow against the unchanged candidate.'],
                                'origin': 'executor_observed_existing_solution', 'research_ref': checkpoint['research_ref']})
                        checkpoint.update(stage='done', status='verified_candidate', reason='protected_candidate_checks_passed')
                    else:
                        checkpoint.update(stage='need_proposal', reason='protected_checks_failed')
                    self._save(run_id, checkpoint)
                elif checkpoint['stage'] in {'need_proposal', 'need_research'}:
                    if checkpoint['attempt_count'] >= run['maximum_attempts']:
                        checkpoint.update(stage='done', status='needs_checker', reason='attempt_limit')
                        self._save(run_id, checkpoint)
                        continue
                    checkpoint['attempt_count'] += 1
                    checkpoint['stage'] = 'inference_started'
                    attempt = {'attempt': checkpoint['attempt_count'], 'source_ref': checkpoint['source_ref'],
                               'reason': 'inference_started', 'checks': []}
                    checkpoint['attempts'].append(attempt)
                    self._save(run_id, checkpoint)
                    try:
                        raw = self.propose(deepcopy(checkpoint['spec']),
                            {path: files[path] for path in checkpoint['spec']['editable']},
                            deepcopy(checkpoint['feedback'][-2:]))
                        if not isinstance(raw, dict):
                            raise ValueError('work_proposal_invalid')
                        if set(raw) <= set(checkpoint['spec']['editable']) and raw:
                            raw = {'plan': ['Implement the admitted goal and run protected feature checks.'], 'edits': raw}
                        if set(raw) not in ({'plan', 'edits'}, {'plan', 'edits', 'blocker'}):
                            raise ValueError('work_proposal_invalid')
                        plan, edits = raw['plan'], raw['edits']
                        if (not isinstance(plan, list) or not 1 <= len(plan) <= 12
                            or any(not isinstance(step, str) or not 1 <= len(step) <= 500 for step in plan)
                            or not isinstance(edits, dict) or not set(edits) <= set(checkpoint['spec']['editable'])
                            or any(not isinstance(text, str) for text in edits.values())):
                            raise ValueError('work_proposal_scope_violation')
                        blocker = _blocker(raw['blocker']) if 'blocker' in raw else None
                        if blocker is not None and edits:
                            raise ValueError('invalid_work_blocker')
                        checkpoint['plan_ref'] = self.artifacts.put({'steps': plan, 'origin': 'specialist_proposal',
                            'research_ref': checkpoint['research_ref'], 'unverified_interpretation': True})
                        metrics = getattr(self.propose, 'last_metrics', None)
                        if isinstance(metrics, dict):
                            attempt['metrics'] = {k: v for k, v in metrics.items() if isinstance(v, (int, float, bool)) and math.isfinite(v)}
                        if blocker is not None:
                            checkpoint.update(stage='done', status='needs_checker', reason='needs_owner_input',
                                blocker={**blocker, 'source_sha256': checkpoint['source_sha256'],
                                         'contract': checkpoint['contract']})
                        elif checkpoint['spec']['mode'] == 'research':
                            if edits:
                                raise ValueError('research_cannot_implement')
                            checkpoint.update(stage='done', status='researched', reason='selected_source_research_complete')
                        else:
                            candidate = {**files, **edits}
                            if not edits or candidate == files or sum(len(t.encode()) for t in candidate.values()) > 60000:
                                raise ValueError('work_proposal_unchanged_or_oversized')
                            for path, text in edits.items():
                                if path.endswith('.py'):
                                    ast.parse(text, filename=path)
                            checkpoint.update(source_ref=self.artifacts.put({'files': candidate}),
                                source_sha256=_digest(candidate), stage='candidate_saved', checks=[],
                                reason='candidate_saved', status='pending')
                            attempt.update(candidate_source_ref=checkpoint['source_ref'],
                                           candidate_source_sha256=checkpoint['source_sha256'])
                        attempt['reason'] = checkpoint['reason']
                    except PracticeYield:
                        checkpoint['attempt_count'] -= 1
                        checkpoint['attempts'].pop()
                        checkpoint['stage'] = 'need_research' if checkpoint['spec']['mode'] == 'research' else 'need_proposal'
                        self._save(run_id, checkpoint, status='queued', reason='foreground_or_control_boundary')
                        self.boundary(run_id, checkpoint)
                        return
                    except (ValueError, SyntaxError) as exc:
                        allowed = {'work_proposal_invalid', 'work_proposal_scope_violation', 'research_cannot_implement',
                                   'work_proposal_unchanged_or_oversized', 'work_context_too_large', 'invalid_work_blocker'}
                        reason = str(exc) if str(exc) in allowed else 'invalid_work_proposal:' + type(exc).__name__
                        checkpoint.update(stage='need_research' if checkpoint['spec']['mode'] == 'research' else 'need_proposal', reason=reason)
                        attempt['reason'] = reason
                        checkpoint['feedback'] = [*checkpoint['feedback'][-1:], {'reason': reason}]
                    except Exception as exc:
                        self._save(run_id, checkpoint, status='blocked', reason='inference_unavailable_no_replay:' + type(exc).__name__)
                        return
                    self._save(run_id, checkpoint)
                else:
                    raise ValueError('unknown_work_checkpoint_stage')
            if not self.boundary(run_id, checkpoint):
                return
            success = checkpoint['status'] in {'verified_candidate', 'researched'}
            self._save(run_id, checkpoint, status='completed' if success else 'blocked' if checkpoint['status'] == 'blocked' else 'needs_checker',
                       reason=checkpoint['reason'])
        except Exception as exc:
            ref = self.artifacts.put(checkpoint) if checkpoint is not None else run['checkpoint_ref']
            self.store.update_run(run_id, ref, status='blocked', reason='work_evidence_invalid:' + type(exc).__name__)
