"""One captured moment, one interpretation, and one choice by the existing Core.

This evidence is historical even when the selected application's pixels change.
It never populates the live/current observation or authorizes a game action.
"""
from copy import deepcopy
import hashlib
import json
import time

from .personal import digest, identifier
from .shared_activity_policy import bounded_identifier, bounded_text, finite_number
from .shared_moment_policy import MomentInterrupted, moment_guard
from .store import OwnershipLost, Store


MOMENT = 'shared_moment'
MOMENT_LIMIT = 4096
PROCESSING_SECONDS = 45
TERMINAL = {'generated', 'silent', 'deferred', 'unavailable', 'cancelled', 'failed'}


class SharedMoments:
    def __init__(self, activity, *, monotonic=time.monotonic, background_image_analyzer=None):
        self.activity, self.core, self.store, self.clock = activity, activity.core, activity.store, activity.clock
        self.monotonic = monotonic
        self.background_image_analyzer = background_image_analyzer
        self._deadlines = {}
        self.watch = None
        with self.store.transaction(self.core.token) as db:
            for row in self.store.list(db, MOMENT, None):
                _cancel_record(db, row, 'owner_restart_no_replay')

    def _snapshot(self, db, meta):
        brain = self.core._brain(db)
        return {'generation': self.core.token[0], 'owner': self.core.token[1], 'sequence': brain['sequence'],
                **{key: meta[key] for key in ('source_epoch', 'source_id', 'kind', 'goal_source_id', 'goal', 'mode', 'label')},
                'deadline': self.clock() + PROCESSING_SECONDS}

    def _guard(self, db, row):
        redact_removed_sources(db)
        current_row = self.store.get(db, MOMENT, row['id'])
        if not current_row or current_row.get('redacted') or current_row['status'] == 'cancelled':
            return 'moment_cancelled'
        if current_row['status'] == 'generated' and not checked_review(self.store, db, current_row):
            return 'moment_review_revoked'
        meta = self.activity._meta(db)
        brain = self.core._brain(db)
        current = {'generation': self.core.token[0], 'owner': self.core.token[1], 'sequence': brain['sequence'],
                   **{key: meta[key] for key in ('source_epoch', 'source_id', 'kind', 'goal_source_id', 'goal', 'mode', 'label')},
                   'enabled': bool(meta['enabled']), 'awake': bool(brain['awake']),
                   'sources_live': all(self.activity._source_live(db, source_id) for source_id in row['source_ids']),
                   'source_available': bool(meta['enabled'] and meta['source_id']),
                   'source_expires_at': meta['expires_at'],
                   'deadline_active': self.monotonic() < self._deadlines.get(row['id'], float('-inf')),
                   'foreground_active': bool(self.core._active_turns(db))}
        reason = moment_guard(row['snapshot'], current, self.clock())
        if reason is None and row.get('watch_id'):
            if self.watch is None:
                return 'watch_unavailable'
            reason = self.watch.moment_authority(db, row)
        return reason

    def _public(self, row):
        result = deepcopy(row.get('result') or {'id': row['id'], 'ok': True,
            'status': row['status'], 'phase': row.get('phase'), 'text': ''})
        result.update(purpose='historical_shared_moment', current_state='unknown', action_authority=False)
        if row.get('capture') and not row.get('redacted'):
            result.update(captured_at=row['capture']['captured_at'], observation_id=row['capture']['observation_id'],
                image_sha256=row['capture']['content_sha256'], received_at=row['received_at'],
                age_seconds=max(0, self.clock() - row['capture']['captured_at']), deadline=row['snapshot']['deadline'],
                personal_source_id=row.get('personal_source_id'))
        return result

    def _checkpoint(self, row):
        with self.store.transaction(self.core.token) as db:
            reason = self._guard(db, row)
        if reason:
            raise MomentInterrupted(reason)

    def inspect(self, request_id):
        bounded_identifier(request_id)
        with self.store.transaction(self.core.token) as db:
            row = self.store.get(db, MOMENT, request_id)
            if not row:
                raise ValueError('shared_moment_not_found')
            reason = self._guard(db, row) if row['status'] not in {'cancelled', 'failed', 'unavailable'} else None
            closed = self._acceptance_closed_with_audio(db, row, reason)
            if closed is not None:
                return closed
            if reason:
                _cancel_record(db, self.store.get(db, MOMENT, request_id), reason)
            return self._public(self.store.get(db, MOMENT, request_id))

    def received(self, request_id, moment_id, text_sha256, consumer_id=None):
        """Reported DOM receipt, optionally bound to the actual local audio lease."""
        bounded_identifier(request_id); bounded_identifier(moment_id)
        if consumer_id is not None:
            bounded_identifier(consumer_id)
        if not isinstance(text_sha256, str) or len(text_sha256) != 64 or any(c not in '0123456789abcdef' for c in text_sha256):
            raise ValueError('invalid_shared_moment_text_hash')
        with self.store.transaction(self.core.token) as db:
            row = self.store.get(db, MOMENT, moment_id)
            if not row:
                raise ValueError('shared_moment_not_found')
            receipt = row.get('text_receipt')
            if receipt and receipt['text_sha256'] != text_sha256:
                raise ValueError('shared_moment_receipt_mismatch')
            needs_audio = False
            if consumer_id is not None:
                consumer = self.store.get(db, 'interaction_consumer', consumer_id)
                consent = self.store.get(db, 'channel_audio_consent', consumer_id)
                if (not consumer or consumer.get('connected') is not True or consumer.get('generation') != self.core.token[0]
                        or self.clock() >= consumer['expires_at'] or not consent or consent.get('enabled') is not True
                        or consent.get('generation') != self.core.token[0] or consent.get('consumer_lease') != consumer['lease']):
                    raise ValueError('shared_moment_audio_consumer_unavailable')
                audio_receipt = row.get('audio_text_receipt') or {}
                needs_audio = (audio_receipt.get('consumer_id') != consumer_id
                    or audio_receipt.get('consumer_lease') != consumer['lease']
                    or audio_receipt.get('text_sha256') != text_sha256)
                if needs_audio and row.get('audio_request_id'):
                    raise ValueError('shared_moment_audio_already_claimed')
            if not receipt or needs_audio:
                reason = self._guard(db, row)
                result = row.get('result') or {}
                if reason or row['status'] != 'generated' or not result.get('text'):
                    raise ValueError('shared_moment_receipt_unavailable')
                if hashlib.sha256(result['text'].encode('utf-8')).hexdigest() != text_sha256:
                    raise ValueError('shared_moment_receipt_mismatch')
            if not receipt:
                row['text_receipt'] = {'request_id': request_id, 'text_sha256': text_sha256,
                                       'received_at': self.clock(), 'status': 'consumer_reported_received'}
                row['text_received_count'] = 1
                row['result']['delivery_status'] = 'consumer_reported_received'
            if needs_audio:
                row['audio_text_receipt'] = {'request_id': request_id, 'text_sha256': text_sha256,
                    'consumer_id': consumer_id, 'consumer_lease': consumer['lease'],
                    'generation': self.core.token[0], 'received_at': self.clock()}
            self.store.put(db, MOMENT, row['id'], row)
            return {'ok': True, 'id': moment_id, 'status': 'consumer_reported_received',
                    'received_at': row['text_receipt']['received_at'], 'duplicate': bool(receipt),
                    'audio_consumer_bound': consumer_id is not None}

    def _acceptance_closed_with_audio(self, db, row, reason):
        if reason != 'processing_deadline':
            return None
        from .channel_audio_moment_source import accepted_audio_deadline
        if accepted_audio_deadline(self.store, db, row, self.clock()) is None:
            return None
        return {'id': row['id'], 'ok': True, 'status': 'cancelled', 'text': '',
                'reason': 'moment_acceptance_closed', 'purpose': 'historical_shared_moment',
                'current_state': 'unknown', 'action_authority': False}

    def _prior_context(self, db, row):
        if not row.get('watch_id'):
            return [], []
        candidates = []
        for prior in self.store.list(db, MOMENT, None):
            snapshot = prior.get('snapshot')
            if (prior['id'] == row['id'] or prior.get('watch_id') != row['watch_id']
                    or prior.get('watch_lease') != row['watch_lease'] or not snapshot
                    or prior.get('redacted') or not prior.get('summary')
                    or (prior['status'] not in {'generated', 'silent', 'deferred'}
                        and not prior.get('own_text_redacted'))):
                continue
            if any(snapshot[key] != row['snapshot'][key] for key in
                   ('generation', 'owner', 'source_id', 'source_epoch', 'goal_source_id', 'goal', 'mode')):
                continue
            if not all(self.activity._source_live(db, sid) for sid in prior['source_ids']):
                continue
            candidates.append(prior)
        candidates.sort(key=lambda item: (item['capture']['captured_at'], item['id']))
        views, selected = [], []
        for prior in candidates[-3:]:
            result = prior.get('result') or {}
            received = bool(prior.get('text_receipt')) and checked_review(self.store, db, prior)
            view = {'captured_at': prior['capture']['captured_at'],
                    'summary': prior['summary'], 'confidence': prior['confidence'],
                    'personal_source_id': prior['personal_source_id'],
                    'temporal_scope': 'historical_capture', 'current_state': 'unknown',
                    'contribution_status': 'consumer_reported_received' if received else 'not_received'}
            if received and result.get('text'):
                view['own_text_received_by_browser'] = result['text'][:800]
                view['own_text_is_excerpt'] = len(result['text']) > 800
                view['text_received_at'] = prior['text_receipt']['received_at']
            views.append(view)
            selected.append(prior)
        # Count encoded bytes, not characters. Unicode must not overflow Core's
        # bounded channel event or turn a known context limit into provider work.
        while views and len(json.dumps(views, ensure_ascii=False).encode('utf-8')) > 6000:
            views.pop(0); selected.pop(0)
        sources = [sid for prior in selected for sid in prior['source_ids']]
        return views, list(dict.fromkeys(sources))

    def share(self, request_id, metadata, raw, media_type, *, watch_id=None, watch_lease=None):
        bounded_identifier(request_id)
        if not self.activity.frames.slots.acquire(blocking=False):
            return {'ok': True, 'status': 'busy', 'reason': 'frame_capacity'}
        workflow = perception = False
        row = None
        scope = None
        try:
            payload = self.activity.frames._payload(metadata, raw, media_type)
            fingerprint = digest({'purpose': 'historical_shared_moment', 'capture': payload,
                                  'media_type': media_type, 'watch_id': watch_id, 'watch_lease': watch_lease})
            with self.store.transaction(self.core.token) as db:
                previous = self.store.get(db, MOMENT, request_id)
                if previous:
                    if previous['fingerprint'] != fingerprint:
                        raise ValueError('shared_moment_id_reused')
                    return self.inspect_in_transaction(db, previous)
            workflow = self.activity._review_lock.acquire(blocking=False)
            if not workflow:
                return {'ok': True, 'status': 'busy', 'reason': 'shared_activity_busy'}
            perception = self.activity.frames.perception.acquire(blocking=False)
            if not perception:
                return {'ok': True, 'status': 'busy', 'reason': 'image_analysis_active'}
            with self.store.transaction(self.core.token) as db:
                meta = self.activity._meta(db)
                if len(self.store.list(db, MOMENT, None)) >= MOMENT_LIMIT:
                    raise ValueError('shared_moment_capacity_requires_maintenance')
                if self.core._active_turns(db):
                    return {'ok': True, 'status': 'busy', 'reason': 'foreground_active'}
                if meta['remaining_reviews'] <= 0:
                    raise ValueError('shared_review_budget_exhausted')
                if self.clock() < meta['next_review_at']:
                    return {'ok': True, 'status': 'deferred', 'reason': 'review_cooldown'}
                if watch_id:
                    if self.watch is None:
                        return {'ok': True, 'status': 'unavailable', 'reason': 'watch_unavailable'}
                    reason = self.watch.admit_moment(db, watch_id, watch_lease, request_id)
                    if reason:
                        return {'ok': True, 'status': 'deferred', 'reason': reason}
                meta, capture, early = self.activity.frames._admit(db, payload, media_type)
                if early:
                    if early.get('duplicate'):
                        raise ValueError('shared_capture_already_used')
                    return {'ok': True, 'status': 'deferred', 'reason': 'frame_rate_limited'}
                if len(self.store.list(db, 'personal_source', None)) >= self.core.personal.meta(db)['policy']['maximum_sources']:
                    raise ValueError('personal_source_limit')
                source_id = identifier('src', 'shared-moment', request_id, fingerprint)
                dependencies = [meta['goal_source_id']] if meta.get('goal_source_id') else []
                identity_sources = list(dict.fromkeys(self.core.personal.identity(db).get('field_sources', {}).values()))
                # Baseline references and the opaque window handle are not
                # fabricated memory dependencies. Authored identity edits are.
                identity_sources = [sid for sid in identity_sources if self.store.get(db, 'personal_source', sid)]
                self.store.put(db, 'personal_source', source_id, {'id': source_id,
                    'source_kind': 'selected_screen_moment', 'origin_kind': 'capability_evidence', 'scope': 'owner_private',
                    'status': 'active', 'occurred_at': capture['captured_at'], 'ingested_at': self.clock(),
                    'text': '', 'text_sha256': digest(''), 'assistant_text': '', 'confidence': 0.0,
                    'image_sha256': capture['content_sha256'], 'capture_observation_id': capture['observation_id'],
                    'temporal_scope': 'historical_capture', 'current_state': 'unknown', 'promoted': False,
                    'dependencies': {'sources': dependencies, 'memories': []}})
                receipt = self.store.get(db, 'shared_frame_receipt', capture['observation_id'])
                receipt['interpretation_source_id'] = source_id
                self.store.put(db, 'shared_frame_receipt', capture['observation_id'], receipt)
                snapshot = self._snapshot(db, meta)
                row = {'id': request_id, 'fingerprint': fingerprint, 'capture': deepcopy(capture),
                       'media_type': media_type, 'snapshot': snapshot, 'received_at': self.clock(),
                       'personal_source_id': source_id, 'source_ids': [*dependencies, source_id, *identity_sources],
                       'identity_source_ids': identity_sources,
                       'status': 'pending', 'phase': 'interpreting', 'summary': None, 'confidence': None,
                       'analysis_started_at': self.clock(), 'analysis_completed_at': None,
                       'review_started_at': None, 'generated_at': None, 'result': None,
                       'review_id': 'moment-review-' + digest([request_id, fingerprint])[:32],
                       'watch_id': watch_id, 'watch_lease': watch_lease}
                self._deadlines[request_id] = self.monotonic() + PROCESSING_SECONDS
                meta['remaining_reviews'] -= 1
                meta['next_review_at'] = self.clock() + meta['review_interval_seconds']
                self.store.put(db, 'shared_meta', 'state', meta)
                self.store.put(db, MOMENT, request_id, row)
                if watch_id:
                    self.watch.reserve_moment(db, watch_id, request_id)
            analyzer = self.background_image_analyzer if watch_id else self.activity.analyze_image
            if not callable(analyzer):
                return self._finish(row, 'unavailable', 'image_analysis_unavailable')
            from ..llm.local_adapter import local_inference_admission
            scope = local_inference_admission(foreground=not bool(watch_id), checkpoint=lambda: self._checkpoint(row))
            scope.__enter__()
            analysis = analyzer(raw, media_type, metadata['kind'])
            if not isinstance(analysis, dict) or set(analysis) != {'summary', 'confidence'}:
                raise ValueError('invalid_shared_image_analysis')
            summary = bounded_text(analysis['summary'], 600)
            confidence = finite_number(analysis['confidence'], maximum=1)
            with self.store.transaction(self.core.token) as db:
                reason = self._guard(db, row)
                if reason:
                    _cancel_record(db, self.store.get(db, MOMENT, row['id']), reason)
                    return self._public(self.store.get(db, MOMENT, row['id']))
                row.update(summary=summary, confidence=confidence, analysis_completed_at=self.clock(),
                           review_started_at=self.clock(), phase='reviewing')
                self.store.put(db, MOMENT, request_id, row)
                source = self.store.get(db, 'personal_source', source_id)
                text = 'In the captured moment at ' + str(capture['captured_at']) + ': ' + summary
                source.update(text=text, text_sha256=digest(text), confidence=confidence,
                              interpreted_at=self.clock())
                prior, prior_sources = self._prior_context(db, row)
                row['source_ids'] = list(dict.fromkeys([*row['source_ids'], *prior_sources]))
                row['prior_moment_sources'] = prior_sources
                # This source is the independent image interpretation. The
                # operational result, not its caption, consumes prior callbacks.
                # Keep those full dependencies on row['source_ids'] above.
                self.store.put(db, MOMENT, request_id, row)
                self.store.put(db, 'personal_source', source_id, source)
                context = {'channel': 'shared_activity', 'audience': 'owner_private',
                    'purpose': 'Reflect briefly or choose comfortable silence about this historical captured moment. '
                               'It is not the current view. Later pixels may differ. Never claim a current game state, '
                               'give time-critical instructions, perform an action, or invent later events.',
                    'shared_moment': {'id': row['id'], 'captured_at': capture['captured_at'],
                        'age_seconds': max(0, self.clock() - capture['captured_at']),
                        'analysis_completed_at': row['analysis_completed_at'], 'image_sha256': capture['content_sha256'],
                        'selected_source': snapshot['label'], 'kind': snapshot['kind'],
                        'summary': summary, 'confidence': confidence, 'source_ids': [source_id, *dependencies],
                        'shared_goal': snapshot['goal'], 'mode': snapshot['mode'],
                        'temporal_scope': 'historical_capture', 'current_state': 'unknown', 'action_authority': False},
                    'prior_shared_moments': prior,
                    'prior_context_note': 'At most three earlier historical captures from this same active watch and goal. '
                        'Browser-received text is a consumer report, never proof of human reading or hearing. '
                        'Do not infer unobserved events between captures.',
                    'foreground_focus': self.core._brain(db)['focus']}
            self.activity.frames.perception.release(); perception = False
            hook = getattr(self.core, 'review_channel', None)
            if not callable(hook):
                return self._finish(row, 'unavailable', 'shared_core_review_unavailable')
            response = hook(context, row['review_id'])
            if response is None:
                return self._finish(row, 'deferred', 'core_review_deferred')
            response = response.model_dump() if hasattr(response, 'model_dump') else dict(response)
            action = response.get('response_action')
            if action not in {'reply', 'acknowledge', 'stay_silent', 'defer'}:
                raise ValueError('unsupported_shared_moment_reaction')
            status = ('generated' if action in {'reply', 'acknowledge'} and response.get('display_text', '').strip()
                      else 'deferred' if action == 'defer' else 'silent')
            return self._finish(row, status, response.get('reason_code', 'shared_moment'),
                                response.get('display_text', '') if status == 'generated' else '')
        except OwnershipLost:
            raise
        except MomentInterrupted as exc:
            with self.store.transaction(self.core.token) as db:
                _cancel_record(db, self.store.get(db, MOMENT, row['id']), str(exc))
                return self._public(self.store.get(db, MOMENT, row['id']))
        except BaseException:
            if row is not None:
                with self.store.transaction(self.core.token) as db:
                    current = self.store.get(db, MOMENT, row['id'])
                    if current and current['status'] == 'pending':
                        current.update(status='failed', phase='complete', result={'id': row['id'], 'ok': False,
                            'status': 'failed', 'text': '', 'reason': 'provider_failed_no_replay'})
                        self.store.put(db, MOMENT, row['id'], current)
                        meta = self.store.get(db, 'shared_meta', 'state')
                        if meta['enabled'] and meta['source_epoch'] == row['snapshot']['source_epoch']:
                            meta.update(enabled=False, source_epoch=meta['source_epoch'] + 1,
                                        latest_frame=None, analysis_pending=None, remaining_frames=0,
                                        remaining_reviews=0, reason='moment_provider_failed_no_replay')
                            self.store.put(db, 'shared_meta', 'state', meta)
            raise
        finally:
            if scope is not None:
                scope.__exit__(None, None, None)
            if row is not None and row.get('watch_id') and self.watch is not None:
                self.watch.finished_moment(row['id'])
            if perception:
                self.activity.frames.perception.release()
            if workflow:
                self.activity._review_lock.release()
            self.activity.frames.slots.release()

    def inspect_in_transaction(self, db, row):
        reason = self._guard(db, row) if row['status'] not in {'cancelled', 'failed', 'unavailable'} else None
        closed = self._acceptance_closed_with_audio(db, row, reason)
        if closed is not None:
            return closed
        if reason:
            _cancel_record(db, self.store.get(db, MOMENT, row['id']), reason)
        return self._public(self.store.get(db, MOMENT, row['id']))

    def _finish(self, row, status, reason, text=''):
        with self.store.transaction(self.core.token) as db:
            invalid = self._guard(db, row)
            if invalid:
                _cancel_record(db, self.store.get(db, MOMENT, row['id']), invalid)
            else:
                current = self.store.get(db, MOMENT, row['id'])
                current.update(status=status, phase='complete', generated_at=self.clock(),
                    result={'id': row['id'], 'ok': True, 'status': status, 'text': text[:2400], 'reason': reason,
                            'summary': current['summary'], 'confidence': current['confidence'],
                            'analysis_started_at': current['analysis_started_at'], 'analysis_completed_at': current['analysis_completed_at'],
                            'review_started_at': current['review_started_at'], 'generated_at': self.clock(),
                            'delivery_status': 'not_delivered',
                            'text_sha256': hashlib.sha256(text[:2400].encode('utf-8')).hexdigest()})
                self.store.put(db, MOMENT, row['id'], current)
            return self._public(self.store.get(db, MOMENT, row['id']))


def _cancel_record(db, row, reason):
    if row is None:
        return
    row.update(status='cancelled', phase='complete', result={'id': row['id'], 'ok': True,
               'status': 'cancelled', 'text': '', 'reason': reason})
    Store.put(db, MOMENT, row['id'], row)
    # Core may finish after an earlier cancellation/forget callback. Clear its
    # response again at this channel's final fence, retaining the fingerprint.
    review = Store.get(db, 'channel_review', row.get('review_id')) if row.get('review_id') else None
    if review:
        review.update(status='cancelled', response=None, expires_at=0)
        Store.put(db, 'channel_review', row['review_id'], review)


def checked_review(store, db, row):
    """Historical output must still bind its exact canonical checked response."""
    expected = 'moment-review-' + digest([row['id'], row['fingerprint']])[:32]
    if row.get('review_id') != expected or not row.get('snapshot'):
        return False
    review = store.get(db, 'channel_review', expected)
    result = row.get('result') or {}
    response = (review or {}).get('response') or {}
    return bool(review and review.get('status') == 'completed' and review.get('expires_at', 0) > 0
                and review.get('generation') == row['snapshot']['generation']
                and review.get('sequence') == row['snapshot']['sequence']
                and result.get('text') and response.get('display_text') == result['text']
                and response.get('response_action') == 'reply')


def redact_removed_reviews(store, db, now):
    """Post-Core-review-erasure hook; retain independent capture and no-replay IDs."""
    count = 0
    for row in store.list(db, MOMENT, None):
        review = store.get(db, 'channel_review', row.get('review_id')) if row.get('review_id') else None
        revoked = review and review.get('status') == 'redacted'
        legacy = row['status'] == 'generated' and not checked_review(store, db, row)
        if revoked or legacy:
            row.update(status='cancelled', phase='complete', own_text_redacted=True,
                       text_receipt=None, audio_text_receipt=None, result={'id':row['id'],'ok':True,'status':'cancelled',
                            'text':'','reason':'checked_review_removed','delivery_status':'revoked'})
            row.setdefault('text_redacted_at', now)
            store.put(db, MOMENT, row['id'], row)
            count += 1
    return count


def invalidate_snapshot(db):
    for row in Store.list(db, MOMENT, None):
        _cancel_record(db, row, 'snapshot_no_replay')
        row.update(capture=None, snapshot=None, summary=None, confidence=None, text_receipt=None, audio_text_receipt=None)
        Store.put(db, MOMENT, row['id'], row)


def invalidate_source_authority(db, meta):
    for row in Store.list(db, MOMENT, None):
        snapshot = row.get('snapshot')
        if row['status'] == 'pending' and snapshot and (not meta['enabled'] or snapshot['source_epoch'] != meta['source_epoch']):
            _cancel_record(db, row, 'source_changed')


def redact_removed_sources(db):
    changed = 0
    for row in Store.list(db, MOMENT, None):
        if row.get('redacted'):
            continue
        removed = [sid for sid in row['source_ids']
                   if (Store.get(db, 'personal_source', sid) or {}).get('status') not in {'active', 'administrative'}]
        if removed:
            snapshot = row.get('snapshot')
            meta = Store.get(db, 'shared_meta', 'state')
            identity_only = set(removed) <= set(row.get('identity_source_ids', []))
            if meta and snapshot and meta['enabled'] and meta['source_epoch'] == snapshot['source_epoch'] and not identity_only:
                meta.update(enabled=False, source_epoch=meta['source_epoch'] + 1, latest_frame=None,
                            analysis_pending=None, remaining_frames=0, remaining_reviews=0, reason='moment_source_removed')
                Store.put(db, 'shared_meta', 'state', meta)
            review = Store.get(db, 'channel_review', row['review_id'])
            if review:
                review.update(status='redacted', response=None, expires_at=0)
                Store.put(db, 'channel_review', row['review_id'], review)
            _cancel_record(db, row, 'source_removed')
            row.update(capture=None, snapshot=None, summary=None, confidence=None, text_receipt=None, audio_text_receipt=None, redacted=True)
            Store.put(db, MOMENT, row['id'], row)
            changed += 1
    return changed
