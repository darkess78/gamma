"""Resumable bounded offline exercises, separate from the live coding executor."""
import ast
from dataclasses import asdict
import hashlib
import json
import time
from pathlib import Path

from .curriculum import discord_foundations
from .evaluation import evaluate_candidate
from .practice import _checks, _digest, _ordered
from .practice_artifacts import ArtifactStore
from ..coordination.store import process_lock
from . import evaluation as _evaluation_module, practice as _practice_module, sandbox as _sandbox_module


_CHECK_MODULES = (_evaluation_module, _practice_module, _sandbox_module)
_LOADED_CHECK_VERSIONS = {m.__name__: hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest() for m in _CHECK_MODULES}


TERMINAL = {'completed', 'blocked', 'needs_checker', 'stopped'}


class PracticeYield(Exception):
    """Admission stopped before any inference request was sent."""


def selected_tasks(exercise_id):
    lookup = {t.id: t for t in discord_foundations()}
    if exercise_id not in lookup:
        raise ValueError('unknown offline practice exercise')
    wanted = set()
    def include(identifier):
        wanted.add(identifier)
        for dependency in lookup[identifier].depends_on:
            include(dependency)
    include(exercise_id)
    return _ordered([task for task in lookup.values() if task.id in wanted])


def task_contract(task):
    # Bind protected/public checks and withheld checker implementation versions.
    code = {module.__name__: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
            for module in _CHECK_MODULES}
    if code != _LOADED_CHECK_VERSIONS:
        raise ValueError('loaded_check_implementation_changed_restart_required')
    body = {'id': task.id, 'skill': task.skill, 'instructions': task.instructions,
            'files': dict(task.files), 'editable': task.editable,
            'checks': [asdict(c) for c in task.checks], 'depends_on': task.depends_on,
            'check_versions': code}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


class PracticeRunner:
    def __init__(self, store, *, propose, check, evaluate=None, clock=time.time):
        self.store = store
        self.artifacts = ArtifactStore(store.root / 'artifacts')
        self.propose = propose
        self.check = check
        self.evaluate = evaluate or (lambda task, files: evaluate_candidate(task, files, check=check))
        self.clock = clock

    def prepare(self, exercise_id):
        tasks = selected_tasks(exercise_id)
        checkpoint = {'version': 1, 'tasks': []}
        for task in tasks:
            source_ref = self.artifacts.put({'files': dict(task.files)})
            checkpoint['tasks'].append({
                'id': task.id, 'contract': task_contract(task), 'source_ref': source_ref,
                'original_source_ref': source_ref, 'instructions': task.instructions,
                'editable': list(task.editable), 'checks_manifest': [asdict(c) for c in task.checks],
                'source_sha256': _digest(task.files), 'stage': 'baseline',
                'attempt_count': 0, 'checks': [], 'feedback': [], 'evaluation': None,
                'status': 'pending', 'reason': 'baseline_pending', 'attempts': [],
            })
        return self.artifacts.put(checkpoint)

    def _save(self, run_id, checkpoint, **updates):
        # The complete immutable artifact is durable before its atomic DB reference.
        ref = self.artifacts.put(checkpoint)
        return self.store.update_run(run_id, ref, **updates)

    def boundary(self, run_id, checkpoint):
        run = self.store.read('run', run_id)
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

    def run(self, run_id):
        """An OS lease reconnects surviving work and prevents duplicate execution."""
        try:
            with process_lock(self.store.root / 'runs' / (run_id + '.lock')):
                self._run_locked(run_id)
        except BlockingIOError:
            return  # A surviving worker still owns the run.

    def _run_locked(self, run_id):
        run = self.store.read('run', run_id)
        if not run or run['status'] in TERMINAL:
            return
        try:
            checkpoint = self.artifacts.get(run['checkpoint_ref'])
        except (ValueError, OSError):
            self.store.update_run(run_id, run['checkpoint_ref'], status='blocked', reason='checkpoint_artifact_invalid')
            return
        try:
            tasks = selected_tasks(run['exercise_id'])
            if [t.id for t in tasks] != [t['id'] for t in checkpoint['tasks']]:
                raise ValueError('exercise_manifest_changed')
            for task, item in zip(tasks, checkpoint['tasks']):
                if item['contract'] != task_contract(task):
                    raise ValueError('exercise_or_check_version_changed')
                files = self.artifacts.get(item['source_ref'])['files']
                if _digest(files) != item['source_sha256'] or set(files) != set(task.files):
                    raise ValueError('candidate_artifact_mismatch')
                if any(files[p] != task.files[p] for p in files if p not in task.editable):
                    raise ValueError('protected_check_changed')
            if not self.boundary(run_id, checkpoint):
                return
            self._save(run_id, checkpoint, status='running', reason='resumed_checkpoint')
            for task, item in zip(tasks, checkpoint['tasks']):
                if item['stage'] == 'done':
                    continue
                if any(t['status'] != 'verified_practice' for t in checkpoint['tasks'] if t['id'] in task.depends_on):
                    item.update(stage='done', status='blocked', reason='dependency_unverified')
                    self._save(run_id, checkpoint)
                    continue
                if item['stage'] == 'inference_started':
                    # Upstream may still be running. No automatic replay on recovery.
                    self._save(run_id, checkpoint, status='blocked', reason='uncertain_inference_no_replay')
                    return
                while item['stage'] != 'done':
                    if not self.boundary(run_id, checkpoint):
                        return
                    files = self.artifacts.get(item['source_ref'])['files']
                    if item['stage'] in {'baseline', 'candidate_saved', 'checking'}:
                        item['stage'] = 'checking'
                        self._save(run_id, checkpoint)
                        checked = _checks(task, files, self.check)
                        item['checks'] = checked
                        item['checks_source_sha256'] = item['source_sha256']
                        item['feedback'] = [{'latest_executed_checks': checked}]
                        if any(c['infrastructure_error'] for c in checked):
                            item.update(stage='done', status='blocked', reason='verification_unavailable')
                        elif all(c['passed'] for c in checked):
                            item.update(stage='public_checked', reason='public_checks_passed')
                        else:
                            item.update(stage='need_proposal', reason='check_failed')
                        if item['attempt_count'] and item.get('attempts'):
                            item['attempts'][-1].update(checks=checked, reason=item['reason'])
                        self._save(run_id, checkpoint)
                    elif item['stage'] == 'need_proposal':
                        if item['attempt_count'] >= run['maximum_attempts']:
                            item.update(stage='done', status='needs_checker', reason='attempt_limit')
                            self._save(run_id, checkpoint)
                            continue
                        item['attempt_count'] += 1
                        item['stage'] = 'inference_started'
                        item.setdefault('attempts', []).append({'attempt': item['attempt_count'], 'reason': 'inference_started', 'checks': []})
                        self._save(run_id, checkpoint)
                        try:
                            feedback = [{'latest_executed_checks': item['checks']}, *item['feedback'][-1:]]
                            edits = self.propose(task, {p: files[p] for p in task.editable}, feedback)
                            if not isinstance(edits, dict) or not edits or not set(edits) <= set(task.editable):
                                raise ValueError('proposal_scope_violation')
                            if any(not isinstance(s, str) for s in edits.values()):
                                raise ValueError('proposal_invalid_source')
                            candidate = {**files, **edits}
                            if candidate == files or sum(len(s.encode()) for s in candidate.values()) > 65536:
                                raise ValueError('proposal_unchanged_or_oversized')
                            for path, source in edits.items():
                                ast.parse(source, filename=path)
                            item['source_ref'] = self.artifacts.put({'files': candidate})
                            item['source_sha256'] = _digest(candidate)
                            item.update(stage='candidate_saved', checks=[], evaluation=None, reason='candidate_saved')
                            item['attempts'][-1].update(reason='candidate_saved', source_ref=item['source_ref'], source_sha256=item['source_sha256'])
                        except PracticeYield:
                            item.update(stage='need_proposal', attempt_count=item['attempt_count'] - 1)
                            item['attempts'].pop()
                            self._save(run_id, checkpoint, status='queued', reason='foreground_or_control_boundary')
                            self.boundary(run_id, checkpoint)
                            return
                        except (ValueError, SyntaxError) as exc:
                            known = {'proposal_scope_violation', 'proposal_invalid_source', 'proposal_unchanged_or_oversized'}
                            reason = str(exc) if str(exc) in known else 'invalid_proposal:' + type(exc).__name__
                            item.update(stage='need_proposal', reason=reason)
                            item['attempts'][-1]['reason'] = reason
                            item['feedback'] = [*item['feedback'][-1:], {'reason': item['reason']}]
                        except Exception as exc:
                            self._save(run_id, checkpoint, status='blocked', reason='inference_unavailable_no_replay:' + type(exc).__name__)
                            return
                        self._save(run_id, checkpoint)
                    elif item['stage'] in {'public_checked', 'evaluation_started'}:
                        item['stage'] = 'evaluation_started'
                        self._save(run_id, checkpoint)
                        raw = self.evaluate(task, files)
                        valid = isinstance(raw, dict) and raw.get('source_sha256') == item['source_sha256']
                        passed = valid and raw.get('passed') is True and raw.get('status') == 'passed'
                        # Withheld details cannot reach feedback, artifacts or candidate examples.
                        item['evaluation'] = {'passed': bool(passed), 'status': raw.get('status') if valid else 'blocked',
                                              'source_sha256': item['source_sha256'], 'checker_version': 1}
                        unavailable = not valid or raw.get('status') == 'blocked'
                        item.update(stage='done', status='verified_practice' if passed else 'blocked' if unavailable else 'needs_checker',
                                    reason='independently_verified' if passed else 'independent_evaluation_unavailable' if unavailable else 'independent_evaluation_failed')
                        self._save(run_id, checkpoint)
                    else:
                        raise ValueError('unknown_checkpoint_stage')
            if not self.boundary(run_id, checkpoint):
                return
            passed = all(t['status'] == 'verified_practice' for t in checkpoint['tasks'])
            blocked = any(t['status'] == 'blocked' for t in checkpoint['tasks'])
            self._save(run_id, checkpoint, status='completed' if passed else 'blocked' if blocked else 'needs_checker',
                       reason='verified_offline_practice' if passed else 'verification_blocked' if blocked else 'bounded_practice_failed')
        except Exception as exc:
            self._save(run_id, checkpoint, status='blocked', reason='checkpoint_or_check_error:' + type(exc).__name__)
