"""Shana's coordination core: one owner of attention, activities and follow-through.

The loop is: take in input or worker results, decide what to do next, act through
a bounded capability, and record what actually happened. Speaking is one action.

Scope (handoff first milestone): private text from the owner plus one capability,
offline coding practice. Workers keep going while Shana talks or sleeps; new
decisions wait until she is awake, and she catches up on real outcomes on waking.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import threading
import time
import uuid

from ..coding.durable_practice import TERMINAL as RUN_TERMINAL
from .decide import Decision
from .store import process_lock


FINAL = {'completed', 'blocked', 'needs_checker', 'stopped'}
ACTIVE = {'waiting', 'running', 'pausing', 'paused', 'stopping'}
RETRY_AFTER_YIELD_SECONDS = 60
RECENT_MESSAGES = 12


class Core:
    def __init__(self, store, *, decide, runner, practice_ids, clock=time.time,
                 quiet_after_seconds=300, run_budget_seconds=3600, maximum_attempts=3):
        self.store = store
        self.decide = decide
        self.runner = runner
        self.practice_ids = tuple(practice_ids)
        self.clock = clock
        self.quiet_after_seconds = quiet_after_seconds
        self.run_budget_seconds = run_budget_seconds
        self.maximum_attempts = maximum_attempts

        self._lease = ExitStack()
        # A second core on the same state fails here with BlockingIOError.
        self._lease.enter_context(process_lock(store.root / 'core.lock'))
        self.token = store.claim()
        with store.transaction(self.token) as db:
            brain = self._brain(db)
            # After a (re)start, wait a normal quiet period before the first quiet review.
            brain['reconsider_at'] = max(brain['reconsider_at'], self.clock() + quiet_after_seconds)
            store.put(db, 'brain', 'state', brain)
        self._worker_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='practice')
        self._thinking_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='thinking')
        self._worker = None      # (activity_id, Future) for the one running practice run
        self._thinking = None    # Future for background thinking (queued turns, quiet review)
        self._guard = threading.Lock()
        self._closed = False

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._thinking_pool.shutdown(wait=False, cancel_futures=True)
        self._worker_pool.shutdown(wait=False, cancel_futures=True)
        try:
            self.store.release(self.token)
        finally:
            self._lease.close()

    # ----- conversation -------------------------------------------------

    def input(self, text, *, session_id='owner', request_id=None):
        """Owner text. Returns Shana's reply, or 'queued' while she is asleep."""
        text = str(text).strip()
        if not text:
            raise ValueError('empty_input')
        request_id = request_id or uuid.uuid4().hex
        now = self.clock()
        with self.store.transaction(self.token) as db:
            existing = self.store.get(db, 'turn', request_id)
            if existing is not None:
                return self._turn_result(existing)
            brain = self._brain(db)
            turn = {'id': request_id, 'session_id': session_id, 'text': text,
                    'created_at': now, 'status': 'queued', 'reply': '', 'effect': None}
            self._log(db, 'owner', text, now, turn_id=request_id)
            if not brain['awake']:
                self.store.put(db, 'turn', request_id, turn)
                return self._turn_result(turn)
            brain['sequence'] += 1
            brain['last_input_at'] = now
            turn.update(status='pending', sequence=brain['sequence'])
            self.store.put(db, 'turn', request_id, turn)
            self.store.put(db, 'brain', 'state', brain)
            situation = self._situation(db, brain, latest_input=text)
        return self._answer(request_id, turn['sequence'], situation)

    def _answer(self, request_id, sequence, situation):
        try:
            decision = self.decide(situation, 'conversation')
        except Exception as exc:  # The model is unavailable or answered badly.
            return self._finish_turn(request_id, status='unavailable', reason=type(exc).__name__)
        now = self.clock()
        with self.store.transaction(self.token) as db:
            brain = self._brain(db)
            turn = self.store.get(db, 'turn', request_id)
            if brain['sequence'] != sequence:
                # A newer message arrived while this one was being answered; that
                # turn already sees this text, so this draft must not act.
                turn.update(status='superseded')
                self.store.put(db, 'turn', request_id, turn)
                return self._turn_result(turn)
            effect = self._apply(db, brain, decision, origin='owner', now=now)
            if decision.say:
                self._log(db, 'shana', decision.say, now, turn_id=request_id)
            turn.update(status='done', reply=decision.say, effect=effect)
            self.store.put(db, 'turn', request_id, turn)
            self.store.put(db, 'brain', 'state', brain)
        return self._turn_result(turn)

    def _finish_turn(self, request_id, *, status, reason):
        with self.store.transaction(self.token) as db:
            turn = self.store.get(db, 'turn', request_id)
            turn.update(status=status, reason=reason)
            self.store.put(db, 'turn', request_id, turn)
        return self._turn_result(turn)

    @staticmethod
    def _turn_result(turn):
        return {'request_id': turn['id'], 'status': turn['status'], 'say': turn.get('reply', ''),
                'effect': turn.get('effect'), 'reason': turn.get('reason')}

    # ----- sleep and wake -----------------------------------------------

    def sleep(self):
        with self.store.transaction(self.token) as db:
            brain = self._brain(db)
            if brain['awake']:
                brain.update(awake=False, slept_at=self.clock())
                self.store.put(db, 'brain', 'state', brain)
        return self.status()

    def wake(self):
        """Wake up and catch up on what workers actually did."""
        self.reconcile()
        with self.store.transaction(self.token) as db:
            brain = self._brain(db)
            brain.update(awake=True)
            catch_up = []
            for activity in self.store.list(db, 'activity', None):
                outcome = activity.get('outcome')
                if outcome and not outcome['reported']:
                    catch_up.append(self._summary(db, activity))
                    outcome['reported'] = True
                    self.store.put(db, 'activity', activity['id'], activity)
            queued = sum(1 for t in self.store.list(db, 'turn', 200) if t['status'] == 'queued')
            self.store.put(db, 'brain', 'state', brain)
            situation = self._situation(db, brain, catch_up=catch_up)
            situation['messages_waiting'] = queued
            sequence = brain['sequence']
        say = ''
        try:
            decision = self.decide(situation, 'wake')
            say = self._commit_initiative(decision, sequence, kind='wake')
        except Exception:
            pass  # Facts are still returned; the greeting is optional.
        return {'catch_up': catch_up, 'say': say, 'messages_waiting': queued}

    # ----- the background loop ------------------------------------------

    def tick(self):
        """Called about once a second. Cheap when nothing changed; no inference itself."""
        if self._closed:
            return
        self.reconcile()
        self._launch_next_run()
        if self._thinking is None or self._thinking.done():
            job = self._next_thinking_job()
            if job is not None:
                self._thinking = self._thinking_pool.submit(job)

    def reconcile(self):
        """Fold worker results into the activity record, once per result."""
        now = self.clock()
        with self._guard:
            live = self._worker[0] if self._worker and not self._worker[1].done() else None
        with self.store.transaction(self.token) as db:
            brain = self._brain(db)
            changed_brain = False
            for activity in self.store.list(db, 'activity', None):
                if activity['status'] in FINAL:
                    continue  # Duplicate or late reports cannot change a recorded outcome.
                run = self.store.get(db, 'run', activity['run_id'])
                status = activity['status']
                if run['status'] in RUN_TERMINAL:
                    status = run['status']
                    activity['outcome'] = {'status': status, 'reason': run.get('reason'),
                                           'finished_at': now, 'while_asleep': not brain['awake'],
                                           'reported': False}
                    if brain['awake']:
                        brain['reconsider_at'] = now  # Let her mention it soon.
                        changed_brain = True
                elif run['status'] == 'paused':
                    status = 'paused'
                elif activity['id'] != live and status in {'running', 'pausing', 'stopping'}:
                    # Worker yielded (e.g. the GPU was busy) or died; relaunch from the checkpoint.
                    if status == 'stopping':
                        run.update(desired_state='stop')
                        self.store.put(db, 'run', run['id'], run)
                    status = 'paused' if status == 'pausing' else 'waiting'
                    # A yield waits for the backend to free up; a dead or foreign worker is retried soon.
                    activity['next_launch_at'] = now + (RETRY_AFTER_YIELD_SECONDS if run['status'] == 'queued' else 5)
                if status != activity['status']:
                    activity.update(status=status, updated_at=now, run_reason=run.get('reason'))
                    self.store.put(db, 'activity', activity['id'], activity)
            if changed_brain:
                self.store.put(db, 'brain', 'state', brain)

    def _launch_next_run(self):
        with self._guard:
            if self._worker and not self._worker[1].done():
                return
        now = self.clock()
        with self.store.transaction(self.token) as db:
            waiting = [a for a in self.store.list(db, 'activity', None)
                       if a['status'] == 'waiting' and a.get('next_launch_at', 0) <= now]
            if not waiting:
                return
            activity = waiting[-1]  # Oldest first.
            activity.update(status='running', updated_at=now)
            self.store.put(db, 'activity', activity['id'], activity)
        with self._guard:
            self._worker = (activity['id'], self._worker_pool.submit(self.runner.run, activity['run_id']))

    def _next_thinking_job(self):
        now = self.clock()
        with self.store.transaction() as db:
            brain = self._brain(db)
            if not brain['awake']:
                return None
            queued = [t for t in self.store.list(db, 'turn', 200) if t['status'] == 'queued']
            if queued:
                turn = queued[-1]
                return lambda: self._answer_queued(turn['id'])
            quiet_for = now - brain['last_input_at']
            if now >= brain['reconsider_at'] and quiet_for >= self.quiet_after_seconds:
                return self.quiet_review
        return None

    def _answer_queued(self, request_id):
        with self.store.transaction(self.token) as db:
            brain = self._brain(db)
            turn = self.store.get(db, 'turn', request_id)
            if turn['status'] != 'queued' or not brain['awake']:
                return None
            brain['sequence'] += 1
            brain['last_input_at'] = self.clock()
            turn.update(status='pending', sequence=brain['sequence'])
            self.store.put(db, 'turn', request_id, turn)
            self.store.put(db, 'brain', 'state', brain)
            situation = self._situation(db, brain, latest_input=turn['text'])
        return self._answer(request_id, turn['sequence'], situation)

    def quiet_review(self):
        """Nothing is happening: practice, say something, or keep quiet."""
        with self.store.transaction(self.token) as db:
            brain = self._brain(db)
            if not brain['awake']:
                return ''
            sequence = brain['sequence']
            situation = self._situation(db, brain)
            # Push the next review out first so a failing model cannot cause a call loop.
            brain['reconsider_at'] = self.clock() + self.quiet_after_seconds
            self.store.put(db, 'brain', 'state', brain)
        try:
            decision = self.decide(situation, 'quiet')
        except Exception:
            return ''
        return self._commit_initiative(decision, sequence, kind='initiative')

    def _commit_initiative(self, decision: Decision, sequence, *, kind):
        now = self.clock()
        with self.store.transaction(self.token) as db:
            brain = self._brain(db)
            if brain['sequence'] != sequence or not brain['awake']:
                return ''  # The owner spoke or she fell asleep meanwhile; drop this draft.
            self._apply(db, brain, decision, origin='self', now=now)
            if decision.say:
                self._log(db, 'shana', decision.say, now, kind=kind)
            for activity in self.store.list(db, 'activity', None):
                outcome = activity.get('outcome')
                if outcome and not outcome['reported'] and decision.say:
                    outcome['reported'] = True
                    self.store.put(db, 'activity', activity['id'], activity)
            self.store.put(db, 'brain', 'state', brain)
        return decision.say

    # ----- acting on decisions ------------------------------------------

    def _apply(self, db, brain, decision: Decision, *, origin, now):
        if decision.focus:
            brain['focus'] = decision.focus
        if decision.mood:
            brain['mood'] = decision.mood.strip()[:40]
        if decision.note is not None:
            brain['notes'] = decision.note.strip()[:400]
        brain['reconsider_at'] = now + min(7200, max(60, decision.reconsider_after_seconds))
        if decision.action == 'none':
            return None
        if decision.action == 'start_practice':
            return self._start_practice(db, decision.exercise_id, origin=origin, now=now)
        return self._control_activity(db, decision.action, decision.activity_id, now=now)

    def _start_practice(self, db, exercise_id, *, origin, now):
        if exercise_id not in self.practice_ids:
            return {'action': 'start_practice', 'refused': 'unknown_exercise'}
        activities = self.store.list(db, 'activity', None)
        for activity in activities:
            if activity['exercise_id'] == exercise_id and activity['status'] in ACTIVE:
                return {'action': 'start_practice', 'activity_id': activity['id'], 'already': activity['status']}
        if origin == 'self' and any(a['status'] in ACTIVE for a in activities):
            return {'action': 'start_practice', 'refused': 'already_busy'}
        run_id = uuid.uuid4().hex
        self.store.put(db, 'run', run_id, {
            'id': run_id, 'exercise_id': exercise_id, 'checkpoint_ref': self.runner.prepare(exercise_id),
            'revision': 0, 'status': 'queued', 'reason': 'authorized', 'desired_state': 'run',
            'expires_at': now + self.run_budget_seconds, 'maximum_attempts': self.maximum_attempts,
        })
        activity_id = uuid.uuid4().hex[:12]
        self.store.put(db, 'activity', activity_id, {
            'id': activity_id, 'kind': 'practice', 'exercise_id': exercise_id, 'origin': origin,
            'status': 'waiting', 'run_id': run_id, 'created_at': now, 'updated_at': now,
            'next_launch_at': 0, 'outcome': None,
        })
        return {'action': 'start_practice', 'activity_id': activity_id}

    def _control_activity(self, db, action, activity_id, *, now):
        activity = self.store.get(db, 'activity', activity_id or '')
        if activity is None:
            return {'action': action, 'refused': 'unknown_activity'}
        run = self.store.get(db, 'run', activity['run_id'])
        status = activity['status']
        if action == 'pause' and status in {'waiting', 'running'}:
            run['desired_state'] = 'pause'
            status = 'paused' if status == 'waiting' else 'pausing'
        elif action == 'resume' and status in {'paused', 'pausing'}:
            run.update(desired_state='run', status='queued')
            status, activity['next_launch_at'] = 'waiting', 0
        elif action == 'stop' and status in ACTIVE:
            run['desired_state'] = 'stop'
            status = 'stopping' if status in {'running', 'pausing'} else 'stopped'
            if status == 'stopped':
                run.update(status='stopped', reason='owner_stopped')
                activity['outcome'] = {'status': 'stopped', 'reason': 'owner_stopped',
                                       'finished_at': now, 'while_asleep': False, 'reported': True}
        else:
            return {'action': action, 'activity_id': activity_id, 'refused': 'not_allowed_from_' + status}
        activity.update(status=status, updated_at=now)
        self.store.put(db, 'run', run['id'], run)
        self.store.put(db, 'activity', activity_id, activity)
        return {'action': action, 'activity_id': activity_id, 'status': status}

    # ----- what Shana sees ----------------------------------------------

    def _situation(self, db, brain, *, latest_input=None, catch_up=None):
        log = list(reversed(self.store.list(db, 'message', RECENT_MESSAGES)))
        situation = {
            'time': time.strftime('%Y-%m-%d %H:%M', time.localtime(self.clock())),
            'awake': brain['awake'], 'focus': brain['focus'], 'mood': brain['mood'],
            'your_notes': brain['notes'],
            'recent_conversation': [{'who': m['who'], 'text': m['text']} for m in log],
            'activities': [self._summary(db, a) for a in self.store.list(db, 'activity', 8)],
            'available_practice': list(self.practice_ids),
        }
        if latest_input is not None:
            situation['latest_input'] = latest_input
        if catch_up is not None:
            situation['while_you_slept'] = catch_up
        return situation

    def _summary(self, db, activity):
        summary = {'activity_id': activity['id'], 'what': 'coding practice: ' + activity['exercise_id'],
                   'started_by': 'owner' if activity['origin'] == 'owner' else 'you',
                   'status': activity['status']}
        if activity.get('outcome'):
            summary['outcome'] = {k: activity['outcome'][k] for k in ('status', 'reason', 'while_asleep')}
        tasks = self._task_states(db, activity)
        if tasks:
            summary['tasks'] = tasks
        return summary

    def _task_states(self, db, activity):
        """Per-task results from the run's latest checkpoint: evidence, not claims."""
        try:
            run = self.store.get(db, 'run', activity['run_id'])
            checkpoint = self.runner.artifacts.get(run['checkpoint_ref'])
        except Exception:
            return None
        return [{'task': t['id'], 'status': t['status'], 'attempts': t['attempt_count'], 'reason': t['reason']}
                for t in checkpoint.get('tasks', [])]

    def _log(self, db, who, text, now, *, turn_id=None, kind='message'):
        identifier = uuid.uuid4().hex
        self.store.put(db, 'message', identifier, {'id': identifier, 'who': who, 'text': text,
                                                   'at': now, 'turn_id': turn_id, 'kind': kind})

    def _brain(self, db):
        return self.store.get(db, 'brain', 'state')

    def status(self):
        with self.store.transaction() as db:
            brain = self._brain(db)
            activities = self.store.list(db, 'activity', 20)
            messages = list(reversed(self.store.list(db, 'message', 30)))
            summaries = [self._summary(db, a) for a in activities]
        return {
            'awake': brain['awake'], 'focus': brain['focus'], 'mood': brain['mood'],
            'activities': summaries,
            'messages': [{'who': m['who'], 'text': m['text'], 'at': m['at'], 'kind': m['kind']} for m in messages],
        }
