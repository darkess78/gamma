"""One private activity owner. Capabilities return evidence, never select activities."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import json
import threading
import time
import uuid
import re
from pathlib import Path

from .store import OwnershipLost, process_lock, private_path
from .commands import plan_command
from .receipts import status_text, work_status_text
from .personal_models import PersonalStateUpdates
from .personal import PersonalState, digest, POLICY
from .growth import GrowthState
from .opportunities import validate_offer
from ..coding.durable_practice import PracticeYield, TERMINAL, selected_tasks, task_contract
from ..coding.practice import _digest
from ..schemas.response import AssistantResponse, BoundedStateUpdates
from ..errors import GammaError


class _InputSuperseded(GammaError):
    """A current-owner request lost its turn before another inference stage."""


class _QuietReviewSuperseded(GammaError):
    """A reserved quiet review is no longer current at an inference boundary."""


class Core:
    def __init__(self, store, *, draft, runner, clock=time.time, identity_seed=None, work_runner=None, identity_lock_root=None, expected_identity_id=None, additional_identity_lock_roots=()):
        self.store, self.draft, self.runner, self.clock = store, draft, runner, clock
        self.work_runner = work_runner
        if expected_identity_id is not None and (not isinstance(expected_identity_id, str)
            or not re.fullmatch('[a-f0-9]{32}', expected_identity_id)):
            raise ValueError('invalid_expected_identity_id')
        if identity_lock_root is not None and not Path(identity_lock_root).is_absolute():
            raise ValueError('identity_lock_root_must_be_absolute')
        if not isinstance(additional_identity_lock_roots,(tuple,list)) or len(additional_identity_lock_roots)>8:
            raise ValueError('invalid_additional_identity_lock_roots')
        lock_root=(private_path(identity_lock_root) if identity_lock_root is not None else
                   Path(__file__).resolve().parents[3]/'data'/'coordination'/'identities')
        roots=[lock_root]
        for path in additional_identity_lock_roots:
            if not Path(path).is_absolute():raise ValueError('identity_lock_root_must_be_absolute')
            roots.append(private_path(path))
        roots=sorted(set(roots),key=str)
        with store.transaction() as db:
            meta=store.get(db,'personal_meta','state')
            if expected_identity_id is not None and (not isinstance(meta, dict) or meta.get('identity_id') != expected_identity_id):
                raise ValueError('release_identity_missing_or_changed')
            if not meta:
                meta={'schema_version':1,'identity_id':uuid.uuid4().hex,'policy':dict(POLICY),'created_at':clock()}
                store.put(db,'personal_meta','state',meta)
        if not re.fullmatch(r'[a-f0-9]{32}',str(meta.get('identity_id',''))):
            raise ValueError('invalid_personal_identity_id')
        self._identity_lease=ExitStack()
        try:
            for path in roots:
                self._identity_lease.enter_context(process_lock(path/(meta['identity_id']+'.lock')))
            self.token = store.claim(store.read('brain', 'state')['generation'], expected_identity_id=expected_identity_id)
            self.personal = PersonalState(store,self.token,clock=clock,identity_seed=identity_seed)
            self.growth = GrowthState(store,self.token,self.personal,clock=clock)
        except BaseException:
            self._identity_lease.__exit__(None,None,None)
            raise
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='offline-practice')
        self.future = None
        self._schedule_lock = threading.Lock()
        self._review_lock = threading.Lock()
        self._recovery_lock = threading.Lock()
        self.dialogue_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='private-message-queue')
        self.dialogue_future = None
        self._queue_lock = threading.Lock()

    def close(self):
        try:
            self.store.release(self.token)
        except OwnershipLost:
            pass
        self.pool.shutdown(wait=True)
        self.dialogue_pool.shutdown(wait=True)
        self._identity_lease.__exit__(None,None,None)

    def _brain(self, db):
        return self.store.get(db, 'brain', 'state')

    def review_channel(self, context, request_id):
        from .channel_review import review_channel
        return review_channel(self, context, request_id)

    def _runs(self, db):
        return self.store.list(db, 'run', None)

    def _activities(self, db, limit=None):
        values = self.store.list(db, 'activity', None)
        if limit is None:
            return sorted(values, key=lambda a: a.get('created_at', 0), reverse=True)
        current = self._brain(db).get('current_activity_id')
        # Bound presentation history, never operational lookup or unfinished work.
        return sorted(values, key=lambda a: (a['id'] == current, a['status'] not in TERMINAL,
                                             a.get('created_at', 0)), reverse=True)[:limit]

    def _active_turns(self, db):
        return [t for t in self.store.list(db, 'turn', None) if t['status'] == 'pending']

    @staticmethod
    def _delivery_summary(outputs):
        if not outputs:return 'accepted_text_without_voice_receipt'
        count=max(output.get('chunk_count',1) for output in outputs)
        present={output.get('chunk_index',0) for output in outputs}
        statuses={output['status'] for output in outputs}
        if len(present)==count and statuses=={'played'}:return 'all_chunks_consumer_reported_played'
        if len(present)==count and statuses=={'delivered'}:return 'consumer_reported_text_delivered'
        if 'played' in statuses:return 'partially_consumer_reported_played'
        if 'started' in statuses:return 'playback_started_incomplete'
        if 'attempted' in statuses:return 'delivery_attempted_unconfirmed'
        if statuses<={'cancelled','failed','expired'}:return 'delivery_unconfirmed_or_cancelled'
        return 'generated_delivery_unconfirmed'

    def admission_checkpoint(self):
        # Existing authorized workers can continue during sleep and after an
        # owner change, but yield before HTTP to foreground or explicit control.
        with self.store.transaction() as db:
            if self._active_turns(db):
                raise PracticeYield('foreground_has_priority')
            for run in self._runs(db):
                if run['status'] == 'running' and (run['desired_state'] != 'running' or self.clock() >= run['expires_at']):
                    raise PracticeYield('worker_control_boundary')

    def _verify_result(self, run):
        if run.get('kind') == 'practical':
            if self.work_runner is None:
                raise ValueError('practical_capability_unavailable')
            return self.work_runner.verify(run)
        checkpoint = self.runner.artifacts.get(run['checkpoint_ref'])
        tasks = selected_tasks(run['exercise_id'])
        if len(tasks) != len(checkpoint['tasks']):
            raise ValueError('result_task_manifest_mismatch')
        for task, item in zip(tasks, checkpoint['tasks']):
            files = self.runner.artifacts.get(item['source_ref'])['files']
            if item['id'] != task.id or item['contract'] != task_contract(task) or _digest(files) != item['source_sha256']:
                raise ValueError('result_artifact_or_version_mismatch')
            if run['status'] == 'completed' and not (
                item['status'] == 'verified_practice' and item['stage'] == 'done'
                and item.get('checks_source_sha256') == item['source_sha256']
                and {c['kind'] for c in item['checks']} == {'regression', 'feature'}
                and all(c['passed'] is True and type(c['exit_code']) is int and c['exit_code'] == 0 for c in item['checks'])
                and item['evaluation']['passed'] is True and item['evaluation']['status'] == 'passed'
                and item['evaluation']['source_sha256'] == item['source_sha256']
            ):
                raise ValueError('completion_missing_verified_evidence')
        return checkpoint

    def _reconcile(self, db, *, force_run_id=None):
        self.growth.sync(db)
        brain = self._brain(db)
        if not brain['awake']:
            return []
        observed = []
        for activity in self._activities(db):
            if (activity['worker_run'] is None and activity['status'] in {'ready', 'waiting', 'paused'}
                and self.clock() >= activity['expires_at']):
                activity.update(status='blocked', reason='budget_exhausted', waiting_condition='owner_review',
                                next_step='review_expired_commitment')
                self.store.put(db, 'activity', activity['id'], activity)
        for run in self._runs(db):
            if run['status'] == 'paused' and run['desired_state'] == 'pause' and self.clock() >= run['expires_at']:
                run.update(status='blocked', reason='budget_exhausted', revision=run['revision'] + 1)
                self.store.put(db, 'run', run['id'], run)
            activity = self.store.get(db, 'activity', run['id'])
            fresh = activity['observed_revision'] != run['revision']
            incorrect_sleep_marker=(activity.get('observed_after_sleep') is True and activity.get('sleep_revision') is None)
            if not fresh and activity.get('validated_generation') == brain['generation'] and run['id'] != force_run_id and not incorrect_sleep_marker:
                continue
            previous_status = activity['status']
            try:
                checkpoint = self._verify_result(run)
                stage = checkpoint['stage'] if run.get('kind') == 'practical' else ', '.join(t['id'] + ':' + t['stage'] for t in checkpoint['tasks'])
                state = run['status']
                reason = run['reason']
                task_results = [{'id': t['id'], 'status': t['status'], 'reason': t['reason'],
                                 'attempt_count': t['attempt_count'], 'checks_passed': bool(t['checks']) and all(c['passed'] for c in t['checks']),
                                 'failed_public_checks': [c['name'] for c in t['checks'] if not c['passed']],
                                 'independent_evaluation_passed': bool(t.get('evaluation', {}).get('passed')) if t.get('evaluation') else False}
                                for t in checkpoint.get('tasks', [])]
            except (ValueError, OSError, KeyError, TypeError):
                state, reason, stage = 'blocked', 'result_evidence_invalid', 'review_artifacts'
                task_results = []
            activity.update(status=state, checkpoint_ref=run['checkpoint_ref'],
                            created_at=run['created_at'],
                            result_ref=run['checkpoint_ref'] if state in TERMINAL else None,
                            observed_revision=run['revision'], reason=reason,
                            next_step=('review_work_candidate' if run.get('kind') == 'practical' else 'review_practice_evidence') if state in TERMINAL else stage,
                            validated_generation=brain['generation'],
                            task_results=task_results,
                            desired_state=run['desired_state'],
                            waiting_condition='owner_review' if state in TERMINAL else
                                'pause_acknowledgment' if run['desired_state'] == 'pause' and state != 'paused' else 'worker_checkpoint',
                            observed_after_sleep=bool(brain.get('slept_at') and activity.get('sleep_revision') is not None
                                                      and run['revision'] != activity['sleep_revision']))
            if run.get('kind') == 'practical' and reason != 'result_evidence_invalid':
                blocker=checkpoint.get('blocker')
                activity.update(mode=checkpoint['spec']['mode'], attempt_count=checkpoint['attempt_count'],
                    source_sha256=checkpoint['source_sha256'], checks_source_sha256=checkpoint.get('checks_source_sha256'),
                    checks=checkpoint['checks'], plan_ref=checkpoint.get('plan_ref'), research_ref=checkpoint.get('research_ref'),
                    blocker=blocker, help_needed=('Worker asks: '+blocker['question']
                        if reason=='needs_owner_input' and blocker else self._work_help(reason)), activation_authorized=False)
            elif run.get('kind') == 'practical':
                activity.update(blocker=None,help_needed=self._work_help(reason))
            self.store.put(db, 'activity', run['id'], activity)
            if incorrect_sleep_marker:
                self.personal.correct_activity_sleep_provenance(db,activity['id'])
            if fresh or state != previous_status:
                observed.append(activity)
                self.personal.observe_activity(db,activity)
                self.growth.observe_activity(db,activity)
        return observed

    def status(self):
        with self.store.transaction(self.token) as db:
            observed = self._reconcile(db)
            brain = self._brain(db)
            self.personal.settle_mood(db)
            brain = self._brain(db)
            return {'generation': brain['generation'], 'awake': brain['awake'], 'focus': brain['focus'],
                    'mood': brain['mood'], 'working_notes': brain['working_notes'],
                    'voice_delivery': dict(brain.get('voice_delivery', {'pace': 1.0, 'loudness_db': 0.0})),
                    'last_decision': brain.get('last_decision'),
                    'activities': self._activities(db, 100), 'catch_up': observed,
                    'scope': 'isolated_owner_private', 'production_verified': False}

    def _context(self, db, session_id, query=''):
        self.personal.settle_mood(db)
        brain = self._brain(db)
        growth=self.growth.context(db,target='dialogue',scope_text=query)
        applicable_refs={row['trial_id']+':'+str(row['version']) for row in growth.get('guidance',[])}
        note_refs=brain.get('working_note_growth_refs',{})
        recent = [t for t in self.store.list(db, 'turn', 100) if
                  t['session_id'] == session_id and t['status'] == 'completed' and not t.get('redacted')][:6]
        history = [{'user': t['text'][:600], 'assistant': t['response'].get('display_text', '')[:600]} for t in reversed(recent)]
        activities = [{key: value for key, value in a.items() if key in
                       {'id', 'goal', 'kind', 'mode', 'origin', 'requested_goal', 'status', 'desired_state', 'worker_run',
                        'reason', 'next_step', 'waiting_condition'}}
                      for a in self._activities(db, 6)]
        activities = [{key: value[:160] if isinstance(value, str) else value for key, value in a.items()}
                      for a in activities]
        exercises = []
        for identifier in (brain['permit'] or {}).get('exercise_ids', []):
            task = selected_tasks(identifier)[-1]
            exercises.append({'id': identifier, 'skill': task.skill, 'purpose': task.instructions[:500]})
        context = {'awake': brain['awake'], 'focus': brain['focus'], 'mood': brain['mood'],
                           'voice_delivery': dict(brain.get('voice_delivery', {'pace': 1.0, 'loudness_db': 0.0})),
                           'working_notes': {key: value[:300] for key, value in brain['working_notes'].items()
                                              if set(note_refs.get(key,[]))<=applicable_refs},
                           'recent_exchanges': history, 'personal':self.personal.context(db,query,recent),
                           'growth':growth,'exploration':self._exploration_context(db),
                           'activities': activities, 'current_activity_id': brain.get('current_activity_id'),
                           'practical_work': {'scope': 'selected disposable files only; no live source, activation, external submissions or paid tools',
                               'tasks': [{'id':a['id'],'goal':a['goal'][:300],'mode':a['mode'],'status':a['status'],
                                          'authorize_start':a.get('authorize_start',False), 'expires_at':a['expires_at']}
                                         for a in self._activities(db) if a.get('kind')=='practical' and a['status']=='ready'][:6]},
                           'authorized_practice': brain['permit'],
                           'offline_practice_capability': {'execution': 'nonverbal independent worker',
                               'scope': 'disposable exercises, no live source or weight changes',
                                'available_exercises': exercises}}
        shared = getattr(self, 'shared_activity', None)
        if shared is not None:
            context['shared_activity'] = shared.context(db)
        return json.dumps(context, ensure_ascii=False)

    @staticmethod
    def _observation_sources(context):
        """Every private source exposed through growth or offered-file observations."""
        sources=[]
        def visit(value):
            if isinstance(value,dict):
                for key,item in value.items():
                    if key=='source_ids' and isinstance(item,list):
                        sources.extend(sid for sid in item if isinstance(sid,str))
                    else:visit(item)
            elif isinstance(value,list):
                for item in value:visit(item)
        for section in ('growth','exploration','shared_activity'):visit(context.get(section))
        return list(dict.fromkeys(sources))

    def _enqueue(self, db, exercise_id, *, identifier, maximum_attempts, budget_seconds, origin, expires_at=None):
        if self.store.get(db, 'run', identifier):
            return self.store.get(db, 'activity', identifier)
        if any(r['status'] not in TERMINAL and not (r['status'] == 'paused' and r['desired_state'] == 'pause') for r in self._runs(db)):
            raise ValueError('one offline practice run may be active at a time')
        checkpoint_ref = self.runner.prepare(exercise_id)
        run = {'id': identifier, 'exercise_id': exercise_id, 'checkpoint_ref': checkpoint_ref,
               'initial_checkpoint_ref': checkpoint_ref,
               'status': 'queued', 'desired_state': 'running', 'revision': 0,
               'reason': 'authorized_offline_practice', 'created_at': self.clock(),
               'expires_at': self.clock() + budget_seconds, 'budget_seconds': budget_seconds,
               'maximum_attempts': maximum_attempts, 'scope': 'fictional_offline_exercises',
               'production_verified': False, 'model_weights_changed': False}
        activity = {'id': identifier, 'goal': exercise_id, 'origin': origin,
                    'created_at': run['created_at'],
                    'status': 'queued', 'worker_run': identifier, 'checkpoint_ref': checkpoint_ref,
                    'result_ref': None, 'observed_revision': -1, 'next_step': 'baseline_checks',
                    'waiting_condition': 'worker_checkpoint', 'reason': run['reason']}
        if expires_at is not None:
            run['expires_at'] = min(run['expires_at'], expires_at)
        previous = self.store.get(db, 'activity', identifier)
        if previous:
            activity.update({k: previous[k] for k in ('requested_goal', 'source_request') if k in previous})
        self.store.put(db, 'run', identifier, run)
        self.store.put(db, 'activity', identifier, activity)
        return activity

    def _activity_command(self, db, updates, brain, *, source_request=None, requested_goal=None, exploration=False):
        """Validate and commit bounded lifecycle choices under this transaction's owner."""
        if updates.activity_action == 'start_work':
            return self._start_work(db, brain, getattr(updates,'work_task_id',None), explicit=False)
        command = plan_command(updates.activity_action, updates.activity_run_id, updates.practice_exercise,
                               self._activities(db), self._runs(db), brain['permit'], self.clock())
        if not command['accepted']:
            return command
        action = updates.activity_action
        if action == 'request_practice':
            permit = brain['permit']
            selected_tasks(updates.practice_exercise)
            identifier = 'practice-' + uuid.uuid4().hex
            activity = {'id': identifier, 'goal': updates.practice_exercise, 'origin': 'owner_request',
                        'requested_goal': (requested_goal or '')[:1000], 'source_request': source_request,
                        'status': 'waiting', 'worker_run': None, 'created_at': self.clock(),
                        'checkpoint_ref': None, 'result_ref': None, 'observed_revision': -1,
                        'expires_at': permit['expires_at'], 'budget_seconds': permit['budget_seconds'],
                        'maximum_attempts': permit['maximum_attempts'], 'reason': 'accepted_user_commitment',
                        'next_step': 'start_authorized_exercise', 'waiting_condition': 'current_work_or_start'}
            permit['remaining_runs'] -= 1
            self.store.put(db, 'activity', identifier, activity)
        else:
            identifier = command['target_id']
            activity = self.store.get(db, 'activity', identifier)
            for pause_id in command.get('pause_ids', []):
                run = self.store.get(db, 'run', pause_id)
                run.update(desired_state='pause', revision=run['revision'] + 1)
                self.store.put(db, 'run', pause_id, run)
            desired = {'pause': 'pause', 'resume': 'running', 'stop': 'stop', 'switch': 'running'}[action]
            run = self.store.get(db, 'run', identifier)
            if run:
                run.update(desired_state=desired, revision=run['revision'] + 1)
                if desired == 'running' and run['status'] == 'paused':
                    run['status'] = 'queued'
                self.store.put(db, 'run', identifier, run)
            else:
                activity.update(status={'pause': 'paused', 'running': 'waiting', 'stop': 'stopped'}[desired],
                                reason='owner_' + action, waiting_condition='owner_resume' if desired == 'pause' else 'current_work_or_start')
                self.store.put(db, 'activity', identifier, activity)
        if not exploration and activity.get('origin')=='self_exploration':
            activity['owner_commitment']=True
            self.store.put(db,'activity',activity['id'],activity)
        brain['current_activity_id'] = activity['id']
        command['activity'] = activity
        return command

    def control(self, request_id, action, *, exercise_id=None, run_id=None,
                focus=None, maximum_attempts=3, budget_seconds=300, exercise_ids=(), maximum_runs=1):
        with self.store.transaction(self.token) as db:
            old = self.store.get(db, 'control', request_id)
            payload = {'action': action, 'exercise_id': exercise_id, 'run_id': run_id, 'focus': focus,
                       'maximum_attempts': maximum_attempts, 'budget_seconds': budget_seconds,
                       'exercise_ids': list(exercise_ids), 'maximum_runs': maximum_runs}
            if old:
                if old['payload'] != payload:
                    raise ValueError('request id reused with different operation')
                return old['result']
            brain = self._brain(db)
            brain['sequence'] += 1
            result = {'ok': True, 'action': action}
            if action in {'sleep', 'wake'}:
                brain['awake'] = action == 'wake'
                # Invalidate drafts already in flight, even if sleep is brief.
                if action == 'sleep':
                    brain['slept_at'] = self.clock()
                    for activity in self._activities(db):
                        run = self.store.get(db, 'run', activity['id'])
                        activity['sleep_revision'] = run['revision'] if run else None
                        self.store.put(db, 'activity', activity['id'], activity)
                    for turn in self._active_turns(db):
                        turn['status'] = 'queued'
                        turn['queue_reason'] = 'sleep_interrupted_draft'
                        self.store.put(db, 'turn', turn['id'], turn)
            elif action == 'focus':
                if focus not in {'focused', 'relaxed'}:
                    raise ValueError('unsupported focus')
                brain['focus'] = focus
            elif action == 'practice':
                if not brain['awake']:
                    raise ValueError('new practice decisions wait for wake')
                result['activity'] = self._enqueue(db, exercise_id, identifier='practice-' + request_id,
                                                   maximum_attempts=maximum_attempts, budget_seconds=budget_seconds,
                                                   origin='owner_request')
                brain['current_activity_id'] = result['activity']['id']
            elif action in {'pause', 'resume', 'stop', 'switch'}:
                if action in {'resume','switch'} and not brain['awake']:
                    raise ValueError('resume decisions wait for wake')
                command = self._activity_command(db, PersonalStateUpdates(activity_action=action, activity_run_id=run_id), brain)
                if not command['accepted']:
                    raise ValueError(command['reason'])
                result.update(run_id=run_id, requested={'pause': 'pause', 'resume': 'running', 'stop': 'stop', 'switch':'running'}[action],
                              acknowledged=command['activity']['worker_run'] is None)
            elif action == 'permit':
                if not brain['awake'] or not exercise_ids:
                    raise ValueError('quiet-time practice needs awake explicit scope')
                for identifier in exercise_ids:
                    selected_tasks(identifier)
                brain['permit'] = {'exercise_ids': list(exercise_ids), 'remaining_runs': maximum_runs,
                                   'remaining_reviews': 3, 'maximum_attempts': maximum_attempts,
                                   'budget_seconds': budget_seconds, 'expires_at': self.clock() + budget_seconds}
                brain['reconsider_at'] = self.clock() + 30
            else:
                raise ValueError('unsupported coordination action')
            self.store.put(db, 'brain', 'state', brain)
            result['catch_up'] = self._reconcile(db)
            if action=='wake':
                activities=self._activities(db)
                result['sleep_catch_up']={
                    'completed':[a['id'] for a in activities if a['status']=='completed'],
                    'failed_or_needs_review':[a['id'] for a in activities if a['status'] in {'blocked','needs_checker','stopped'}],
                    'waiting':[a['id'] for a in activities if a['status'] not in TERMINAL],
                    'changed_situation_messages':[t['id'] for t in self.store.list(db,'turn',None)
                        if t['status'] in {'queued','recoverable','waiting_review'}],
                    'maintenance_proposals':[p['id'] for p in self.store.list(db,'maintenance_proposal',100)
                        if p['status']=='pending_owner_review'],
                    'worker_experiences_claimed':False}
            self.store.put(db, 'control', request_id, {'payload': payload, 'result': result})
            return result

    def _accept_decision(self, db, response, *, interactive=False, source_request=None, requested_goal=None, guidance_refs=()):
        brain = self._brain(db)
        updates = response.state_updates
        delivery = dict(brain.get('voice_delivery', {'pace': 1.0, 'loudness_db': 0.0}))
        for field, key in (('voice_pace', 'pace'), ('voice_loudness_db', 'loudness_db')):
            value = getattr(updates, field, None)
            if value is not None:
                delivery[key] = value
        brain['voice_delivery'] = delivery
        # Snapshot once for this response, before any sentence is synthesized.
        response.tts_metadata['voice_delivery'] = dict(delivery)
        if updates.emotion:
            brain['mood'] = updates.emotion
            brain.update(mood_updated_at=self.clock(),mood_intensity=.5)
        if updates.attention_focus:
            brain['focus'] = updates.attention_focus
        brain['working_notes'].update({key: value for key, value in updates.model_dump(exclude_none=True).items()
                                       if key in {'active_topic', 'current_objective', 'deferred_intention'}})
        refs=brain.setdefault('working_note_growth_refs',{})
        for key in {'active_topic','current_objective','deferred_intention'}:
            if getattr(updates,key,None) is not None:refs[key]=list(guidance_refs)
        permit = brain['permit']
        dispatched = None
        command = None
        if interactive and updates.activity_action in {'request_practice', 'start_work', 'pause', 'resume', 'stop', 'switch'}:
            command = self._activity_command(db, updates, brain, source_request=source_request, requested_goal=requested_goal)
        if (updates.activity_action == 'practice' or not interactive and updates.activity_action == 'request_practice') and permit and (
            permit['expires_at'] > self.clock() and permit['remaining_runs'] > 0
            and updates.practice_exercise in permit['exercise_ids']
            and not any(r['status'] not in TERMINAL and not (r['status'] == 'paused' and r['desired_state'] == 'pause') for r in self._runs(db))
            and not any(a['worker_run'] is None and a['status'] == 'waiting' for a in self._activities(db))
        ):
            dispatched = self._enqueue(db, updates.practice_exercise, identifier='practice-' + uuid.uuid4().hex,
                          maximum_attempts=permit['maximum_attempts'],
                          budget_seconds=min(permit['budget_seconds'], permit['expires_at'] - self.clock()),
                          origin='owner_request' if interactive else 'authorized_quiet_time',
                          expires_at=permit['expires_at'])
            if interactive:
                dispatched.update(requested_goal=(requested_goal or '')[:1000], source_request=source_request)
                self.store.put(db, 'activity', dispatched['id'], dispatched)
            permit['remaining_runs'] -= 1
        brain['reconsider_at'] = self.clock() + (updates.reconsider_after_seconds or 300)
        if permit and permit['expires_at'] > self.clock():
            brain['reconsider_at'] = min(brain['reconsider_at'], permit['expires_at'])
        brain['last_decision'] = {'response_action': response.response_action,
                                  'proposed_activity': updates.activity_action or 'wait',
                                  'accepted_activity': 'practice' if dispatched else 'wait',
                                  'worker_run': dispatched['worker_run'] if dispatched else None,
                                  'reason_code': response.reason_code, 'observed_at': self.clock(),
                                  'reconsider_at': brain['reconsider_at']}
        if response.tts_metadata.get('coordination_prompt'):
            brain['last_decision']['prompt_diagnostics'] = response.tts_metadata['coordination_prompt']
        if command:
            brain['last_decision'].update(accepted_activity=updates.activity_action if command['accepted'] else 'wait',
                                          capability_reason=command['reason'],
                                          activity_id=command.get('activity', {}).get('id'))
        if dispatched:
            brain['current_activity_id'] = dispatched['id']
        self.store.put(db, 'brain', 'state', brain)
        return command if command else dispatched

    @staticmethod
    def _deferred(reason):
        return AssistantResponse(delivery_mode='deferred', response_action='defer', reason_code=reason)

    def _render_status(self, db, run_id=None):
        """Factual capability receipts use evidence, without model paraphrasing."""
        self._reconcile(db)
        activities = self._activities(db, 1)
        identifier = run_id or self._brain(db).get('current_activity_id')
        activity = self.store.get(db, 'activity', identifier) if identifier else activities[0] if activities else None
        if activity and activity.get('kind') == 'practical' and activity.get('worker_run'):
            self._reconcile(db,force_run_id=activity['id'])
            activity=self.store.get(db,'activity',activity['id'])
        text = status_text(activity) if activity or not identifier else 'No matching private activity exists. Completion is not verified.'
        if activity and activity.get('kind') == 'practical':
            text = work_status_text(activity)
        return AssistantResponse(display_text=text, delivery_mode='text_only', reason_code='work_status' if activity and activity.get('kind')=='practical' else 'practice_status')

    def input(self, text, session_id, request_id, *, _resume=False, observation=None):
        interaction = None
        if observation is not None:
            from .interaction_models import InteractionObservation
            observed = InteractionObservation.model_validate({**observation, 'text': text,
                'session_id': session_id, 'request_id': request_id})
            if observed.speaker != 'owner':
                raise ValueError('private interaction requires an explicitly bound owner source')
            interaction = observed.model_dump(exclude={'text'})
        if not _resume:
            self._drain_queue()
        with self.store.transaction(self.token) as db:
            old = self.store.get(db, 'turn', request_id)
            if old:
                if (old.get('text_sha256',digest(old['text'])) != digest(text) or old['session_id'] != session_id
                        or digest(old.get('interaction')) != digest(interaction)):
                    raise ValueError('request id reused with different text or session')
                if not (_resume and old['status'] in {'queued','recoverable'}):
                    response = AssistantResponse.model_validate(old['response']) if old['status'] == 'completed' else self._deferred(old['status'])
                    return self._envelope(response, old['generation'])
            self._reconcile(db)
            brain = self._brain(db)
            queue = [t for t in self.store.list(db,'turn',None) if t['status'] in {'queued','recoverable'}]
            received = old.get('received_at',self.clock()) if old else self.clock()
            expires = old.get('expires_at',received+self.personal.meta(db)['policy']['queue_seconds']) if old else received+self.personal.meta(db)['policy']['queue_seconds']
            if not brain['awake'] or queue and not _resume:
                if not old and len(queue)>=64:
                    return self._envelope(self._deferred('sleep_queue_full'),brain['generation'])
                turn = old or {'id':request_id,'session_id':session_id,'text':text,'text_sha256':digest(text),
                    'received_at':received,'expires_at':expires,'generation':self.token[0],
                    'privacy_scope':'local_private','response':None}
                turn.update(status='queued',queue_reason='asleep' if not brain['awake'] else 'ordered_wake_queue')
                if interaction is not None:
                    turn['interaction'] = interaction
                self.store.put(db,'turn',request_id,turn)
                self.personal.receive(db,turn)
                return self._envelope(self._deferred('asleep' if not brain['awake'] else 'queued'),brain['generation'])
            if _resume and self.clock()>=expires:
                old['status'] = 'waiting_review'
                self.store.put(db,'turn',request_id,old)
                return self._envelope(self._deferred('queued_message_expired'),brain['generation'])
            if len(self._active_turns(db)) >= 8:
                return self._envelope(self._deferred('foreground_queue_full'), brain['generation'])
            brain['sequence'] += 1
            brain['last_input_at'] = self.clock()
            brain['last_session_id'] = session_id
            self.store.put(db, 'brain', 'state', brain)
            sequence = brain['sequence']
            context = self._context(db, session_id,text)
            bound_context=json.loads(context)
            if interaction is not None:
                bound_context['interaction'] = interaction
                outputs=self.store.list(db,'interaction_output',32)
                bound_context['interaction_delivery']=[{key:output[key] for key in
                    ('id','request_id','channel','status','reason','utterance_id','chunk_index','chunk_count')
                    if key in output} for output in outputs[:8]]
                recent=[t for t in self.store.list(db,'turn',100) if t['session_id']==session_id
                    and t['status']=='completed' and not t.get('redacted')][:6]
                for row,previous in zip(bound_context['recent_exchanges'],reversed(recent)):
                    related=[o for o in outputs if o['request_id']==previous['id']]
                    row['request_id']=previous['id']
                    row['delivery_status']=self._delivery_summary(related)
            else:
                # Core.input is an admitted owner turn. Quiet reviews use
                # _context directly and never receive this text-only marker.
                bound_context['owner_input'] = {'audience':'owner_private', 'kind':'text', 'address_cue':'direct'}
            context = json.dumps(bound_context, ensure_ascii=False)
            growth_context=bound_context.get('growth',{})
            growth_guidance=growth_context.get('guidance',[])
            guidance_sha=digest(growth_guidance)
            guidance_refs=[str(g['trial_id'])+':'+str(g['version']) for g in growth_guidance]
            turn = {'id': request_id, 'session_id': session_id, 'text': text,
                    'status': 'pending', 'sequence': sequence, 'generation': self.token[0],
                    'received_at':received,'expires_at':expires,'text_sha256':digest(text),
                    'privacy_scope': 'local_private', 'response': None}
            if interaction is not None:
                turn['interaction'] = interaction
            self.store.put(db, 'turn', request_id, turn)
            # Descriptive growth context can influence a reply just as scoped guidance can.
            # Bind all supplied sources before optional prompt compaction; this grants no authority.
            recent_sources=bound_context['personal'].setdefault('recent_source_ids',[])
            recent_sources[:]=list(dict.fromkeys([*recent_sources,*self._observation_sources(bound_context)]))
            self.personal.bind_context(db,turn,bound_context)
            turn['growth_guidance_sha256']=guidance_sha
            self.store.put(db,'turn',request_id,turn)
        def check_current_turn():
            # The provider invokes this while waiting for its existing shared
            # backend slot. A stale request must not occupy another inference
            # stage or publish personal changes after newer owner input.
            with self.store.transaction(self.token) as db:
                current = self.store.get(db, 'turn', request_id)
                brain = self._brain(db)
                if (not brain['awake'] or brain['sequence'] != sequence
                        or not current or current.get('status') != 'pending'
                        or current.get('redacted')):
                    raise _InputSuperseded()

        try:
            if text.strip().lower() in {'practice status', 'practice progress', 'practice result'}:
                response = AssistantResponse(delivery_mode='silent', response_action='stay_silent',
                                             state_updates=BoundedStateUpdates(activity_action='report_status'))
            else:
                from ..llm.local_adapter import local_inference_admission
                with local_inference_admission(foreground=True, checkpoint=check_current_turn):
                    check_current_turn()
                    response = self.draft(text, context)
                    check_current_turn()
            response = AssistantResponse.model_validate(response)
            # The HTTP result is private text only, regardless of model preference.
            response.speech_text = ''
            response.audio_path = response.audio_content_type = None
            if response.delivery_mode == 'speech':
                response.delivery_mode = 'text_only'
            with self.store.transaction(self.token) as db:
                brain = self._brain(db)
                if not brain['awake'] or brain['sequence'] != sequence:
                    current = self.store.get(db,'turn',request_id)
                    if current['status']=='pending':
                        current['status']='interrupted'
                        self.store.put(db,'turn',request_id,current)
                    return self._envelope(self._deferred('superseded'), self.token[0])
                if digest(self.growth.context(db,target='dialogue',scope_text=text).get('guidance',[]))!=guidance_sha:
                    current=self.store.get(db,'turn',request_id)
                    current['status']='interrupted'
                    self.store.put(db,'turn',request_id,current)
                    return self._envelope(self._deferred('growth_guidance_changed'),self.token[0])
                personal_changes = getattr(response.state_updates,'personal_changes',[])
                growth_proposals = getattr(response.state_updates,'growth_proposals',[])
                note_keys = set(response.state_updates.model_dump(exclude_none=True)) & {'active_topic','current_objective','deferred_intention'}
                if response.state_updates.activity_action == 'report_status':
                    updates = response.state_updates
                    response = self._render_status(db, updates.activity_run_id)
                    response.state_updates = updates
                proposed = response.state_updates.activity_action
                dispatched = self._accept_decision(db, response, interactive=True, source_request=request_id, requested_goal=text,
                                                   guidance_refs=guidance_refs)
                if proposed == 'practice':
                    message = (f"I queued the {dispatched['goal'].replace('_', ' ')} exercise for bounded offline practice."
                               if dispatched else 'Practice was not started. It needs an active authorized scope and available quiet time.')
                    response = AssistantResponse(display_text=message, delivery_mode='text_only', reason_code='practice_queued' if dispatched else 'practice_not_started')
                elif proposed in {'request_practice', 'start_work', 'pause', 'resume', 'stop', 'switch'}:
                    if dispatched['accepted']:
                        activity = dispatched['activity']
                        if proposed in {'request_practice','start_work'}:
                            message, reason = f"I kept your {activity['goal']} request as {activity['id']}; it will start when the current work yields, within its original scope and deadline.", 'activity_waiting'
                        else:
                            message, reason = f"{proposed.capitalize()} accepted for {activity['id']}. Worker changes take effect at a safe checkpoint; saved work and limits are retained.", 'activity_control'
                    else:
                        message, reason = 'That activity action was not accepted: ' + dispatched['reason'] + '.', 'activity_not_accepted'
                    response = AssistantResponse(display_text=message, delivery_mode='text_only', reason_code=reason)
                # Only an admitted private voice consumer can request audio. The
                # finalized, checked response is the sole synthesis source.
                if (interaction and interaction['capability_speech_allowed']
                        and interaction['channel'] in {'private_voice', 'discord_voice'}
                        and response.response_action in {'reply', 'acknowledge'}
                        and response.delivery_mode == 'text_only' and response.display_text):
                    response.speech_text = response.display_text
                    response.delivery_mode = 'speech'
                personal_receipt = self.personal.accept(db,turn,response.model_dump(),personal_changes,dispatched,note_keys)
                self.growth.sync(db)
                current = self.store.get(db,'turn',request_id)
                if current.get('redacted'):
                    response = AssistantResponse(display_text='I removed that source and dependent private recollections.',
                        delivery_mode='text_only',reason_code='personal_source_removed')
                    return self._envelope(response,brain['generation'])
                growth_receipt=self.growth.accept_turn(db,turn,response.model_dump(),growth_proposals)
                turn.update(status='completed', response=response.model_dump(),personal_receipt=personal_receipt,growth_receipt=growth_receipt)
                self.store.put(db, 'turn', request_id, turn)
                self.personal.prune(db)
                self.growth.sync(db)
                # Journal, accepted bounded state, and private output commit together.
                return self._envelope(response, brain['generation'])
        except OwnershipLost:
            raise
        except _InputSuperseded:
            with self.store.transaction(self.token) as db:
                current = self.store.get(db, 'turn', request_id)
                # Explicit sleep queueing and forgetting already own their
                # states. Only pending superseded drafts become terminal.
                if current and current['status'] == 'pending':
                    current['status'] = 'interrupted'
                    self.store.put(db, 'turn', request_id, current)
            return self._envelope(self._deferred('superseded'), self.token[0])
        except Exception:
            with self.store.transaction(self.token) as db:
                current = self.store.get(db,'turn',request_id)
                if current and current['status']=='pending':
                    current['status']='failed'
                    self.store.put(db,'turn',request_id,current)
            raise

    @staticmethod
    def _envelope(response, generation):
        return {'ok': True, 'event': {'kind': 'private_text', 'metadata': {'output_target_policy': 'dashboard_monitor'}},
                'result': {'generation': generation, 'assistant_response': response.model_dump(),
                           'decision': {'decision': response.response_action, 'reason': response.reason_code}}}

    def _drain_queue(self):
        with self._queue_lock:
            for _ in range(8):
                with self.store.transaction(self.token) as db:
                    if not self._brain(db)['awake'] or self._active_turns(db):
                        return
                    queued = sorted([t for t in self.store.list(db,'turn',None) if t['status'] in {'queued','recoverable'}],
                                    key=lambda t:(t.get('received_at',0),t['id']))
                    if not queued:
                        return
                    turn = queued[0]
                self.input(turn['text'],turn['session_id'],turn['id'],_resume=True,
                           observation=turn.get('interaction'))

    def personal_state(self):
        with self.store.transaction(self.token) as db:
            return self.personal.inspect(db)

    def personal_control(self, request_id, action, values):
        with self.store.transaction(self.token) as db:
            payload = {'action':action,'values':values}
            old = self.store.get(db,'personal_control',request_id)
            if old:
                if old['payload_sha256']!=digest(payload):
                    raise ValueError('request id reused with different operation')
                return old['result']
            result = self.personal.administer(db,request_id,action,values)
            self.growth.sync(db)
            self.store.put(db,'personal_control',request_id,{'payload_sha256':digest(payload),'result':result})
            return result

    @staticmethod
    def _work_help(reason):
        if reason.startswith(('uncertain_inference_no_replay','inference_unavailable_no_replay:')):
            return 'Reconcile the uncertain local model request before authorizing another attempt. No automatic replay occurs.'
        if reason.startswith('work_evidence_invalid:'):
            return 'Review missing, changed or damaged source and check artifacts before creating a new task.'
        return {'attempt_limit':'Review the failed protected checks or authorize a revised bounded task.',
                'needs_owner_input':'Answer the worker question or revise the selected task scope before authorizing a new task.',
                'budget_exhausted':'Review the saved candidate and authorize a new deadline in a separate task if needed.',
                'inference_uncertain':'Reconcile the uncertain model request before authorizing another attempt.',
                'inference_unavailable':'Restore the selected local capability; this run is not automatically replayed.',
                'practical_capability_unavailable':'Provide the configured isolated practical executor.',
                'check_infrastructure_unavailable':'Restore the Gamma sandbox and inspect the saved candidate.',
                'verification_unavailable':'Restore the Gamma sandbox and inspect the saved candidate. No completion is verified.',
                'result_evidence_invalid':'Review missing, changed or damaged source and check artifacts.'}.get(reason,
                    'Review the saved evidence and clarify any missing task input.' if reason not in {
                        'verified_candidate','researched','authorized_practical_work','protected_candidate_checks_passed',
                        'selected_source_research_complete'} else '')

    def _start_work(self, db, brain, task_id, *, explicit):
        result={'accepted':False,'reason':'unknown_task','action':'start_work','target_id':task_id,'pause_ids':[]}
        activity=self.store.get(db,'activity',task_id) if isinstance(task_id,str) else None
        if not activity or activity.get('kind')!='practical':
            return result
        if not brain['awake']:
            result['reason']='asleep'; return result
        if self.work_runner is None:
            result['reason']='practical_capability_unavailable'; return result
        if self.clock()>=activity['expires_at']:
            result['reason']='budget_exhausted'; return result
        if not explicit and not activity.get('authorize_start'):
            result['reason']='explicit_start_required'; return result
        if activity['status'] in {'waiting','queued','running','paused'}:
            return {**result,'accepted':True,'reason':'already_started','activity':activity}
        if activity['status']!='ready':
            result['reason']='target_not_ready'; return result
        activity.update(status='waiting',reason='accepted_practical_commitment',
                        next_step='start_authorized_work',waiting_condition='current_work_or_start',
                        authorize_start=True, authorized_at=self.clock())
        self.store.put(db,'activity',task_id,activity)
        brain['current_activity_id']=task_id
        return {**result,'accepted':True,'reason':'ok','activity':activity}

    def _enqueue_work(self, db, activity):
        if self.store.get(db,'run',activity['id']):
            return
        run={'id':activity['id'],'kind':'practical','checkpoint_ref':activity['checkpoint_ref'],
             'initial_checkpoint_ref':activity['initial_checkpoint_ref'],'status':'queued','desired_state':'running',
             'revision':0,'reason':'authorized_practical_work','created_at':activity['created_at'],
             'expires_at':activity['expires_at'],'budget_seconds':activity['budget_seconds'],
             'maximum_attempts':activity['maximum_attempts'],'scope':'selected_disposable_files',
             'production_verified':False,'model_weights_changed':False,'activation_authorized':False}
        try:
            self.work_runner.verify(run)
        except (ValueError,OSError,KeyError,TypeError):
            activity.update(status='blocked',reason='result_evidence_invalid',waiting_condition='owner_review',
                            next_step='review_work_candidate',help_needed=self._work_help('result_evidence_invalid'))
            self.store.put(db,'activity',activity['id'],activity)
            return
        self.store.put(db,'run',run['id'],run)
        activity.update(status='queued',worker_run=run['id'],reason=run['reason'],next_step='research_selected_sources')
        self.store.put(db,'activity',activity['id'],activity)

    def work_control(self, request_id, action, *, spec=None, task_id=None, maximum_attempts=3,
                     budget_seconds=300, authorize_start=False):
        from .work_models import validate_spec
        if (type(maximum_attempts) is not int or not 1<=maximum_attempts<=5
            or type(budget_seconds) is not int or not 30<=budget_seconds<=1800
            or type(authorize_start) is not bool):
            raise ValueError('invalid_practical_budget')
        if action=='prepare':
            if task_id is not None:
                raise ValueError('prepare_does_not_accept_task_id')
            spec=validate_spec(spec)
        elif action=='start':
            if spec is not None or authorize_start:
                raise ValueError('start_cannot_change_scope')
        else:
            raise ValueError('unsupported_practical_action')
        payload={'action':action,'spec':spec,'task_id':task_id,'maximum_attempts':maximum_attempts,
                 'budget_seconds':budget_seconds,'authorize_start':authorize_start}
        with self.store.transaction(self.token) as db:
            old=self.store.get(db,'work_control',request_id)
            if old:
                if old['payload_sha256']!=digest(payload):
                    raise ValueError('request id reused with different operation')
                return old['result']
            if self.work_runner is None:
                raise ValueError('practical_capability_unavailable')
            brain=self._brain(db)
            if action=='prepare':
                if len([a for a in self._activities(db) if a.get('kind')=='practical'])>=100:
                    raise ValueError('practical_task_capacity_reached')
                identifier='work-'+uuid.uuid4().hex
                ref=self.work_runner.prepare(spec)
                activity={'id':identifier,'kind':'practical','mode':spec['mode'],'goal':spec['goal'],
                    'origin':'owner_request','created_at':self.clock(),'status':'ready','worker_run':None,
                    'checkpoint_ref':ref,'initial_checkpoint_ref':ref,'result_ref':None,'observed_revision':-1,
                    'expires_at':self.clock()+budget_seconds,'budget_seconds':budget_seconds,
                    'maximum_attempts':maximum_attempts,'authorize_start':authorize_start,
                    'reason':'prepared_scope_only','next_step':'explicit_start', 'waiting_condition':'owner_start',
                    'activation_authorized':False,'production_verified':False}
                self.store.put(db,'activity',identifier,activity)
                result={'ok':True,'activity':activity}
            else:
                command=self._start_work(db,brain,task_id,explicit=True)
                if not command['accepted']:
                    raise ValueError(command['reason'])
                result={'ok':True,'activity':command['activity']}
            brain['sequence']+=1
            self.store.put(db,'brain','state',brain)
            self.store.put(db,'work_control',request_id,{'payload_sha256':digest(payload),'result':result})
            return result

    def work_state(self, task_id=None):
        with self.store.transaction(self.token) as db:
            self._reconcile(db)
            if task_id is None:
                return {'activities':[a for a in self._activities(db) if a.get('kind')=='practical'],
                        'production_verified':False,'activation_authorized':False}
            activity=self.store.get(db,'activity',task_id)
            if not activity or activity.get('kind')!='practical':
                raise ValueError('unknown_task')
            if self.work_runner is None:
                raise ValueError('practical_capability_unavailable')
            run=self.store.get(db,'run',task_id)
            evidence_run=run or {'id':task_id,'kind':'practical','checkpoint_ref':activity['checkpoint_ref'],
                'initial_checkpoint_ref':activity['initial_checkpoint_ref'],'status':'queued','desired_state':'running',
                'created_at':activity['created_at'],'expires_at':activity['expires_at'],
                'budget_seconds':activity['budget_seconds'],'maximum_attempts':activity['maximum_attempts']}
            checkpoint=self.work_runner.verify(evidence_run)
            files=self.work_runner.artifacts.get(checkpoint['source_ref'])['files']
            return {'activity':activity,'run':run,'checkpoint':checkpoint,'files':files,
                    'production_verified':False,'activation_authorized':False}

    def tick(self):
        """Batch evidence without inference. Launch only already authorized work."""
        with self._schedule_lock:
            with self.store.transaction(self.token) as db:
                self._reconcile(db)
                brain = self._brain(db)
                if self.clock()-brain.get('last_retention_check',0)>=60:
                    self.personal.prune(db)
                    brain=self._brain(db)
                    brain['last_retention_check']=self.clock()
                    self.store.put(db,'brain','state',brain)
                queued = sorted([t for t in self.store.list(db,'turn',None) if t['status'] in {'queued','recoverable'}],
                                key=lambda t:(t.get('received_at',0),t['id']))
                if brain['awake'] and queued and not self._active_turns(db) and (self.dialogue_future is None or self.dialogue_future.done()):
                    self.dialogue_future = self.dialogue_pool.submit(self._drain_queue)
                live = [r for r in self._runs(db) if r['status'] not in TERMINAL
                        and not (r['status'] == 'paused' and r['desired_state'] == 'pause')]
                if brain['awake'] and not live and not self._active_turns(db):
                    pending = [a for a in self._activities(db) if a['worker_run'] is None and a['status'] == 'waiting']
                    pending.sort(key=lambda a: (a['id'] != brain.get('current_activity_id'), a['created_at']))
                    if pending:
                        activity = pending[0]
                        if activity['expires_at'] <= self.clock():
                            activity.update(status='blocked', reason='budget_exhausted', waiting_condition='owner_review')
                            self.store.put(db, 'activity', activity['id'], activity)
                        else:
                            if activity.get('kind') == 'practical':
                                self._enqueue_work(db, activity)
                            else:
                                self._enqueue(db, activity['goal'], identifier=activity['id'],
                                              maximum_attempts=activity['maximum_attempts'], budget_seconds=activity['budget_seconds'],
                                              expires_at=activity['expires_at'], origin=activity['origin'])
                            brain['current_activity_id'] = activity['id']
                            self.store.put(db, 'brain', 'state', brain)
                runs = [r for r in reversed(self._runs(db)) if r['status'] in {'queued', 'running'}
                        or r['status'] == 'paused' and r['desired_state'] != 'pause']
                runs.sort(key=lambda r: r['desired_state'] == 'running')
                if self._active_turns(db):
                    runs = [r for r in runs if r['desired_state'] != 'running']
                if runs and (self.future is None or self.future.done()):
                    selected = self.work_runner if runs[0].get('kind') == 'practical' else self.runner
                    if selected is None:
                        run=runs[0]
                        run.update(status='blocked',reason='practical_capability_unavailable',revision=run['revision']+1)
                        self.store.put(db,'run',run['id'],run)
                    else:
                        self.future = self.pool.submit(selected.run, runs[0]['id'])

    def _exploration_context(self, db):
        brain=self._brain(db)
        offer=self.store.get(db,'growth_opportunity',brain.get('exploration_offer_id',''))
        if not offer:return None
        tasks=[]
        for identifier in offer['task_ids']:
            activity=self.store.get(db,'activity',identifier)
            if activity:
                tasks.append({key:activity.get(key) for key in ('id','goal','status','mode','origin','owner_commitment')})
                tasks[-1]['goal']=tasks[-1]['goal'][:300]
                observations=self.growth.activity_observations(db,activity)
                if observations:tasks[-1]['source_observations']=observations
        return {'id':offer['id'],'status':offer['status'],'expires_at':offer['expires_at'],
                'remaining_reviews':offer['remaining_reviews'],'reconsider_at':offer['reconsider_at'],
                'tasks':tasks,'allowed_choices':['start_work','wait','pause','resume','stop','switch'],
                'authority':'Only this finite offer; controlling user commitments is not permitted.',
                'interpretation':'Choices are observations, not proof of a lasting interest or feeling.'}

    def growth_state(self):
        with self.store.transaction(self.token) as db:
            self._reconcile(db)
            return {**self.growth.state(db),'exploration':self._exploration_context(db),
                    'choices':self.store.list(db,'growth_choice',30),'runtime_trial_targets':['dialogue'],
                    'production_activated':False,'weights_changed':False}

    def growth_control(self, request_id, action, values):
        if not isinstance(values,dict) or len(json.dumps(values,ensure_ascii=False))>24000:
            raise ValueError('invalid_growth_control')
        payload={'action':action,'values':values}
        with self.store.transaction(self.token) as db:
            self.growth.sync(db)
            old=self.store.get(db,'growth_control',request_id)
            if old:
                if old['payload_sha256']!=digest(payload):raise ValueError('request id reused with different growth operation')
                return old['result']
            brain=self._brain(db)
            if action=='opportunities':
                if set(values)-{'task_ids','budget_seconds','maximum_reviews'}:raise ValueError('invalid_exploration_fields')
                if not brain['awake']:raise ValueError('exploration_decisions_wait_for_wake')
                offer=validate_offer(values.get('task_ids'),self._activities(db),self.clock(),
                    values.get('budget_seconds',300),values.get('maximum_reviews',3))
                prior=self.store.get(db,'growth_opportunity',brain.get('exploration_offer_id',''))
                if prior:
                    prior['status']='superseded'
                    self.store.put(db,'growth_opportunity',prior['id'],prior)
                offer.update(id='offer-'+uuid.uuid4().hex,status='active',created_at=self.clock(),
                             reconsider_at=self.clock(),origin='owner_authorized_opportunities')
                self.store.put(db,'growth_opportunity',offer['id'],offer)
                brain['exploration_offer_id']=offer['id']
                result={'ok':True,'offer':offer}
            elif action=='review':
                if values:raise ValueError('review_has_no_values')
                offer=self.store.get(db,'growth_opportunity',brain.get('exploration_offer_id',''))
                if not brain['awake'] or not offer or offer['status']!='active' or offer['remaining_reviews']<=0 or self.clock()>=offer['expires_at']:
                    raise ValueError('no_current_exploration_permission')
                offer['reconsider_at']=self.clock()
                self.store.put(db,'growth_opportunity',offer['id'],offer)
                result={'ok':True,'review_scheduled':True,'offer_id':offer['id']}
            else:
                if action=='trial' and not brain['awake']:raise ValueError('growth_trial_waits_for_wake')
                result=self.growth.control(db,action,values)
            brain['sequence']+=1
            self.store.put(db,'brain','state',brain)
            self.store.put(db,'growth_control',request_id,{'payload_sha256':digest(payload),'result':result})
            return result

    def _draft_quietly(self, text, context, checkpoint):
        from ..llm.local_adapter import local_inference_admission

        def checked():
            try:
                checkpoint()
            except OwnershipLost as exc:
                # Preserve this fence through ConversationService's typed error
                # boundary, then re-raise the original ownership failure below.
                raise _QuietReviewSuperseded() from exc

        try:
            with local_inference_admission(foreground=False, checkpoint=checked):
                checked()
                response = self.draft(text, context)
                checked()
                return response
        except _QuietReviewSuperseded as exc:
            if isinstance(exc.__cause__, OwnershipLost):
                raise exc.__cause__
            raise

    def _review_exploration(self):
        with self.store.transaction(self.token) as db:
            self._reconcile(db)
            brain=self._brain(db)
            offer=self.store.get(db,'growth_opportunity',brain.get('exploration_offer_id',''))
            if not offer or offer['status']!='active':return False
            if self.clock()>=offer['expires_at']:
                offer['status']='expired'
                self.store.put(db,'growth_opportunity',offer['id'],offer)
                return False
            if (not brain['awake'] or self._active_turns(db) or offer['remaining_reviews']<=0
                or self.clock()<offer['reconsider_at']):return False
            live=[a for a in self._activities(db) if a.get('worker_run') and a['status'] not in TERMINAL and a['status']!='paused']
            if any(a.get('origin')!='self_exploration' or a.get('owner_commitment') is True for a in live):return False
            if any(a['worker_run'] is None and a['status']=='waiting' for a in self._activities(db)):return False
            offer['remaining_reviews']-=1
            offer['reconsider_at']=min(self.clock()+300,offer['expires_at'])
            self.store.put(db,'growth_opportunity',offer['id'],offer)
            sequence=brain['sequence']
            context=self._context(db,brain.get('last_session_id','monitor-private'))
            observations=json.loads(context)
            guidance=observations['growth'].get('guidance',[])
            source_ids=self._observation_sources(observations)
        def check_exploration():
            with self.store.transaction(self.token) as db:
                brain = self._brain(db)
                current = self.store.get(db, 'growth_opportunity', offer['id'])
                if (not brain['awake'] or brain['sequence'] != sequence or self._active_turns(db)
                        or not current or current['status'] != 'active' or self.clock() >= current['expires_at']):
                    raise _QuietReviewSuperseded()
                if digest(self.growth.context(db, target='dialogue', scope_text='').get('guidance', [])) != digest(guidance):
                    raise _QuietReviewSuperseded()
                if any((self.store.get(db, 'personal_source', sid) or {}).get('status') != 'active' for sid in source_ids):
                    raise _QuietReviewSuperseded()
                live = [a for a in self._activities(db) if a.get('worker_run') and a['status'] not in TERMINAL and a['status'] != 'paused']
                if (any(a.get('origin') != 'self_exploration' or a.get('owner_commitment') is True for a in live)
                        or any(a['worker_run'] is None and a['status'] == 'waiting' for a in self._activities(db))):
                    raise _QuietReviewSuperseded()

        try:
            response = AssistantResponse.model_validate(self._draft_quietly(
                'Quiet opportunity review. Choose among the currently authorized exploration tasks, continue, pause, return, stop your own project, or wait. '
                'Use observations and current situation. No dialogue is required. A choice is not proof of a feeling or permanent interest.', context, check_exploration))
        except _QuietReviewSuperseded:
            return True
        with self.store.transaction(self.token) as db:
            brain=self._brain(db)
            current=self.store.get(db,'growth_opportunity',offer['id'])
            if (not brain['awake'] or brain['sequence']!=sequence or self._active_turns(db)
                or current['status']!='active' or self.clock()>=current['expires_at']):return True
            if digest(self.growth.context(db,target='dialogue',scope_text='').get('guidance',[]))!=digest(guidance):return True
            if any((self.store.get(db,'personal_source',sid) or {}).get('status')!='active' for sid in source_ids):return True
            updates=response.state_updates
            action=updates.activity_action or 'wait'
            identifier=getattr(updates,'work_task_id',None) if action=='start_work' else updates.activity_run_id
            result={'accepted':action=='wait','reason':'wait' if action=='wait' else 'choice_outside_offer'}
            if identifier in current['task_ids']:
                activity=self.store.get(db,'activity',identifier)
                if action=='start_work' and activity['status']=='ready':
                    result=self._start_work(db,brain,identifier,explicit=False)
                    if result['accepted']:
                        activity=result['activity'];activity.update(origin='self_exploration',owner_commitment=False,
                            exploration_offer_id=current['id'])
                        self.store.put(db,'activity',identifier,activity)
                elif (action in {'pause','resume','stop','switch'} and activity.get('origin')=='self_exploration'
                      and not activity.get('owner_commitment')):
                    result=self._activity_command(db,updates,brain,exploration=True)
            choice={'id':'choice-'+uuid.uuid4().hex,'offer_id':current['id'],'opportunity_id':current['id'],
                    'action':action,'decision':action,
                    'task_id':identifier,'accepted':result['accepted'],'reason':result['reason'],
                    'task_goal':(self.store.get(db,'activity',identifier) or {}).get('goal','')[:300] if result['accepted'] and identifier else '',
                    'reason_code':response.reason_code,'observed_at':self.clock(),
                    'source_ids':source_ids,
                    'generation':self.token[0],'source':'actual_model_choice_admission',
                    'feeling_or_durable_interest_proven':False}
            self.store.put(db,'growth_choice',choice['id'],choice)
            brain['last_exploration_choice']=choice['id']
            self.store.put(db,'brain','state',brain)
        return True

    def review(self):
        """Bounded private review. User focus does not prohibit authorized quiet work."""
        if not self._review_lock.acquire(blocking=False):
            return
        try:
            if self._review_exploration():return
            with self.store.transaction(self.token) as db:
                self._reconcile(db)
                brain = self._brain(db)
                permit = brain['permit']
                if (not brain['awake'] or self._active_turns(db)
                    or not permit or permit['remaining_reviews'] <= 0 or permit['remaining_runs'] <= 0
                    or self.clock() >= permit['expires_at'] or self.clock() < brain['reconsider_at']
                    or any(r['status'] not in TERMINAL and not (r['status'] == 'paused' and r['desired_state'] == 'pause') for r in self._runs(db))
                    or any(a['worker_run'] is None and a['status'] == 'waiting' for a in self._activities(db))):
                    return
                permit['remaining_reviews'] -= 1
                brain['reconsider_at'] = min(self.clock() + 300, permit['expires_at'])
                self.store.put(db, 'brain', 'state', brain)
                sequence = brain['sequence']
                context = self._context(db, brain.get('last_session_id', 'monitor-private'))
                observations = json.loads(context)
                guidance = observations['growth'].get('guidance', [])
                source_ids = self._observation_sources(observations)
                permit_sha256 = digest(permit)

            def check_practice():
                with self.store.transaction(self.token) as db:
                    brain = self._brain(db)
                    current = brain['permit']
                    if (not brain['awake'] or brain['sequence'] != sequence or self._active_turns(db)
                            or not current or digest(current) != permit_sha256
                            or current['remaining_runs'] <= 0 or self.clock() >= current['expires_at']
                            or any(r['status'] not in TERMINAL and not (r['status'] == 'paused' and r['desired_state'] == 'pause') for r in self._runs(db))
                            or any(a['worker_run'] is None and a['status'] == 'waiting' for a in self._activities(db))):
                        raise _QuietReviewSuperseded()
                    if digest(self.growth.context(db, target='dialogue', scope_text='').get('guidance', [])) != digest(guidance):
                        raise _QuietReviewSuperseded()
                    if any((self.store.get(db, 'personal_source', sid) or {}).get('status') != 'active' for sid in source_ids):
                        raise _QuietReviewSuperseded()

            try:
                response = self._draft_quietly('Quiet-time review. Choose authorized offline practice or wait. No dialogue is required.', context, check_practice)
                response = AssistantResponse.model_validate(response)
            except _QuietReviewSuperseded:
                return
            with self.store.transaction(self.token) as db:
                brain = self._brain(db)
                if brain['awake'] and brain['sequence'] == sequence and not self._active_turns(db):
                    self._accept_decision(db, response)
        finally:
            self._review_lock.release()
