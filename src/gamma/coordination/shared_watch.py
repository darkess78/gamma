"""Explicit finite selected-window watch authority, never image freshness."""
from copy import deepcopy
import secrets
import uuid

from .personal import digest
from .shared_activity_policy import bounded_identifier
from .shared_moments import MOMENT, _cancel_record
from .store import Store


WATCH = 'shared_watch'
COMMAND = 'shared_watch_command'
CHOICES = {5: (10, 30), 15: (20, 45), 30: (30, 60)}
LEASE_SECONDS = 45
CAPACITY = 1024


class SharedWatch:
    def __init__(self, moments):
        self.moments, self.activity, self.core, self.store, self.clock = (
            moments, moments.activity, moments.core, moments.store, moments.clock)
        moments.watch = self
        with self.store.transaction(self.core.token) as db:
            for row in self.store.list(db, WATCH, None):
                _stop(db, row, 'owner_restart_no_resume')

    def _current(self, db, row):
        if row['status'] not in {'active', 'exhausted'}:
            return row
        meta = self.activity._meta(db)
        brain = self.core._brain(db)
        audio_deadline = None
        if row['status'] == 'exhausted':
            from .channel_audio_moment_source import accepted_audio_deadline
            final = self.store.get(db, MOMENT, row.get('last_completed'))
            audio_deadline = accepted_audio_deadline(self.store, db, final, self.clock()) if final else None
        reason = None
        if self.clock() >= row['expires_at']:
            reason = 'watch_deadline'
        elif self.clock() >= row['availability_expires_at'] and audio_deadline is None:
            reason = 'window_availability_expired'
        elif (not meta['enabled'] or not brain['awake'] or row['generation'] != self.core.token[0]
              or row['source_epoch'] != meta['source_epoch'] or row['source_id'] != meta['source_id']):
            reason = 'selected_source_revoked'
        if reason:
            _stop(db, row, reason)
        elif row['status'] == 'exhausted':
            final = self.store.get(db, MOMENT, row.get('last_completed'))
            if not final or not final.get('snapshot') or self.clock() >= final['snapshot']['deadline'] and audio_deadline is None:
                _stop(db, row, 'final_delivery_deadline')
        return row

    def _row(self, db, watch_id, lease=None):
        row = self.store.get(db, WATCH, watch_id)
        if not row:
            raise ValueError('shared_watch_not_found')
        self._current(db, row)
        if lease is not None and row.get('lease') != lease:
            raise ValueError('shared_watch_lease_changed')
        return row

    def _public(self, row):
        return {key: deepcopy(row[key]) for key in ('id', 'status', 'reason', 'source_id', 'source_epoch',
            'minutes', 'maximum_moments', 'remaining_moments', 'consumed_moments', 'created_at', 'expires_at',
            'availability_expires_at', 'next_sample_at', 'inflight', 'quiet_streak', 'lease')}

    def start(self, request_id, source_id, source_epoch, minutes):
        bounded_identifier(request_id); bounded_identifier(source_id)
        if type(minutes) is not int or minutes not in CHOICES or type(source_epoch) is not int or source_epoch < 0:
            raise ValueError('invalid_shared_watch_plan')
        fingerprint = digest({'source_id': source_id, 'source_epoch': source_epoch, 'minutes': minutes})
        with self.store.transaction(self.core.token) as db:
            old = self.store.get(db, COMMAND, request_id)
            if old:
                if old['fingerprint'] != fingerprint:
                    raise ValueError('shared_watch_request_id_reused')
                return self._public(self._row(db, old['watch_id']))
            if len(self.store.list(db, WATCH, None)) >= CAPACITY:
                raise ValueError('shared_watch_capacity_requires_maintenance')
            if not callable(self.moments.background_image_analyzer):
                raise ValueError('background_image_analysis_unavailable')
            for previous in self.store.list(db, WATCH, None):
                if self._current(db, previous)['status'] == 'active':
                    raise ValueError('shared_watch_already_active')
            meta = self.activity._meta(db)
            brain = self.core._brain(db)
            seconds, (maximum, cadence) = minutes * 60, CHOICES[minutes]
            if (not meta['enabled'] or not brain['awake'] or meta['source_id'] != source_id
                    or meta['source_epoch'] != source_epoch):
                raise ValueError('shared_watch_selected_source_changed')
            if meta['expires_at'] - self.clock() < seconds - 2:
                raise ValueError('shared_watch_requires_explicit_source_duration')
            if meta['remaining_frames'] < maximum or meta['remaining_reviews'] < maximum:
                raise ValueError('shared_watch_requires_explicit_source_budget')
            now = self.clock()
            row = {'id': 'watch-' + uuid.uuid4().hex, 'status': 'active', 'reason': 'owner_opted_in',
                   'generation': self.core.token[0], 'source_id': source_id, 'source_epoch': source_epoch,
                   'minutes': minutes, 'maximum_moments': maximum, 'remaining_moments': maximum,
                   'consumed_moments': 0, 'cadence_seconds': cadence, 'created_at': now,
                   'expires_at': min(now + seconds, meta['expires_at']),
                   'availability_expires_at': min(now + LEASE_SECONDS, meta['expires_at']),
                   'next_sample_at': now, 'inflight': None, 'quiet_streak': 0, 'lease': secrets.token_hex(16)}
            self.store.put(db, WATCH, row['id'], row)
            self.store.put(db, COMMAND, request_id, {'fingerprint': fingerprint, 'watch_id': row['id']})
            return self._public(row)

    def inspect(self, watch_id):
        with self.store.transaction(self.core.token) as db:
            return self._public(self._row(db, watch_id))

    def status(self):
        with self.store.transaction(self.core.token) as db:
            rows = [self._public(self._current(db, row)) for row in self.store.list(db, WATCH, None)]
            return next((row for row in reversed(rows) if row['status'] == 'active'), None)

    def heartbeat(self, watch_id, lease):
        with self.store.transaction(self.core.token) as db:
            row = self._row(db, watch_id, lease)
            if row['status'] != 'active':
                return self._public(row)
            # This confirms only client consent/window availability. It never
            # updates source expiry, a capture time, pixels or interpretation.
            row['availability_expires_at'] = min(self.clock() + LEASE_SECONDS, row['expires_at'])
            self.store.put(db, WATCH, row['id'], row)
            return self._public(row)

    def stop(self, watch_id, lease):
        with self.store.transaction(self.core.token) as db:
            row = self._row(db, watch_id)
            if row['status'] in {'active', 'exhausted'} and row['lease'] != lease:
                raise ValueError('shared_watch_lease_changed')
            _stop(db, row, 'owner_stopped_watch')
            return self._public(row)

    def admit_moment(self, db, watch_id, lease, request_id):
        row = self._row(db, watch_id, lease)
        if row['status'] != 'active':
            return 'watch_not_active'
        if row['inflight']:
            return 'watch_moment_in_flight'
        if row['remaining_moments'] <= 0:
            _stop(db, row, 'watch_sample_budget_exhausted')
            return 'watch_sample_budget_exhausted'
        if self.clock() < row['next_sample_at']:
            return 'watch_cooldown'
        return None

    def reserve_moment(self, db, watch_id, request_id):
        row = self._row(db, watch_id)
        if row['status'] != 'active' or row['remaining_moments'] <= 0 or row['inflight']:
            raise ValueError('shared_watch_reservation_changed')
        row['remaining_moments'] -= 1
        row['consumed_moments'] += 1
        row['inflight'] = request_id
        row['next_sample_at'] = self.clock() + row['cadence_seconds']
        self.store.put(db, WATCH, row['id'], row)

    def moment_authority(self, db, moment):
        row = self._row(db, moment['watch_id'])
        terminal_delivery = (row['status'] == 'exhausted' and row.get('last_completed') == moment['id']
                             and moment['status'] == 'generated')
        if ((not terminal_delivery and row['status'] != 'active') or row['lease'] != moment['watch_lease']
                or moment['status'] == 'pending' and row['inflight'] != moment['id']):
            return 'watch_authority_revoked'
        return None

    def share(self, watch_id, lease, request_id, metadata, raw, media_type):
        result = self.moments.share(request_id, metadata, raw, media_type, watch_id=watch_id, watch_lease=lease)
        if result.get('status') in {'busy', 'deferred'}:
            with self.store.transaction(self.core.token) as db:
                row = self._row(db, watch_id)
                if row['status'] == 'active' and row['inflight'] is None:
                    row['next_sample_at'] = max(row['next_sample_at'], self.clock() + 30)
                    self.store.put(db, WATCH, row['id'], row)
        return {'moment': result, 'watch': self.inspect(watch_id)}

    def finished_moment(self, request_id):
        with self.store.transaction(self.core.token) as db:
            moment = self.store.get(db, MOMENT, request_id)
            if not moment or not moment.get('watch_id'):
                return
            row = self._row(db, moment['watch_id'])
            if row['inflight'] != request_id:
                return
            row['inflight'] = None
            if row['status'] == 'active':
                if moment['status'] in {'failed', 'unavailable'}:
                    _stop(db, row, 'failed_moment_no_automatic_resume')
                    return
                row['quiet_streak'] = row['quiet_streak'] + 1 if moment['status'] == 'silent' else 0
                gap = min(120, 60 * (2 ** min(row['quiet_streak'] - 1, 1))) if row['quiet_streak'] else 10
                if moment['status'] in {'deferred', 'cancelled'}:
                    gap = max(gap, 30)
                row['next_sample_at'] = max(row['next_sample_at'], self.clock() + gap)
                if row['remaining_moments'] <= 0:
                    # No new capture or availability renewal. Only the final
                    # completed output may obtain a receipt within its deadline.
                    row.update(status='exhausted', reason='watch_sample_budget_exhausted', last_completed=request_id)
                    self.store.put(db, WATCH, row['id'], row)
                    return
            self.store.put(db, WATCH, row['id'], row)


def _stop(db, row, reason):
    if row['status'] in {'active', 'exhausted'}:
        row.update(status='stopped', reason=reason, lease=None)
        from .channel_audio_moment_source import revoke_selection_audio
        revoke_selection_audio(db, watch_id=row['id'])
        for moment in Store.list(db, MOMENT, None):
            if moment.get('watch_id') == row['id'] and moment['status'] == 'pending':
                _cancel_record(db, moment, reason)
    Store.put(db, WATCH, row['id'], row)


def invalidate_source_authority(db, meta):
    for row in Store.list(db, WATCH, None):
        if row['status'] in {'active', 'exhausted'} and (not meta['enabled'] or row['source_epoch'] != meta['source_epoch']
                                        or row['source_id'] != meta['source_id']):
            _stop(db, row, 'selected_source_revoked')


def invalidate_snapshot(db):
    for row in Store.list(db, WATCH, None):
        _stop(db, row, 'snapshot_no_resume')
