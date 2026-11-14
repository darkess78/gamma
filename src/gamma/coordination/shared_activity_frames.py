"""Fresh full-frame receipts can renew exact same pixels without a second inference."""
from copy import deepcopy
import hashlib
import threading

from .personal import digest, identifier
from .shared_activity_policy import bounded_text, finite_number, normalize_observation
from .store import OwnershipLost


FRAME_LIMIT = 4096
MIN_FRAME_INTERVAL = .2


class SharedFrames:
    def __init__(self, activity):
        self.activity, self.store, self.core, self.clock = activity, activity.store, activity.core, activity.clock
        self.slots = threading.BoundedSemaphore(4)
        self.perception = threading.Lock()

    def _payload(self, metadata, raw, media_type):
        if not isinstance(raw, bytes) or not 8 <= len(raw) <= 800_000:
            raise ValueError('invalid_shared_image_size')
        signatures = {'image/png': raw.startswith(b'\x89PNG\r\n\x1a\n'),
                      'image/jpeg': raw.startswith(b'\xff\xd8\xff'),
                      'image/webp': raw.startswith(b'RIFF') and raw[8:12] == b'WEBP'}
        if not isinstance(media_type, str) or not signatures.get(media_type):
            raise ValueError('invalid_shared_image_format')
        if not isinstance(metadata, dict) or set(metadata) != {
                'observation_id', 'source_id', 'source_epoch', 'sequence', 'captured_at', 'kind'}:
            raise ValueError('invalid_shared_image_metadata')
        return {**metadata, 'summary': 'Image awaiting interpretation.', 'confidence': 0.0,
                'content_sha256': hashlib.sha256(raw).hexdigest()}

    def _admit(self, db, payload, media_type):
        meta = self.activity._meta(db)
        if not meta['enabled']:
            raise ValueError('shared_observation_disabled')
        row = normalize_observation(payload, source_id=meta['source_id'], source_epoch=meta['source_epoch'],
                                    source_kind=meta['kind'], now=self.clock())
        fingerprint = digest({'metadata': {key: row[key] for key in row if key not in {'summary', 'confidence'}},
                              'media_type': media_type})
        previous = self.store.get(db, 'shared_frame_receipt', row['observation_id'])
        if previous:
            if previous['fingerprint'] != fingerprint:
                raise ValueError('shared_observation_id_reused')
            obsolete = previous['scene_revision'] != meta['scene_revision']
            result = ({'ok': True, 'status': 'cancelled', 'reason': 'frame_no_replay'}
                      if previous.get('redacted') or previous.get('invalidated') or obsolete else deepcopy(previous['result']))
            return meta, row, {**result, 'duplicate': True}
        if self.store.get(db, 'shared_observation', row['observation_id']):
            raise ValueError('shared_observation_id_reused')
        if row['sequence'] <= meta['last_sequence']:
            raise ValueError('stale_shared_sequence')
        if meta['remaining_frames'] <= 0:
            raise ValueError('shared_frame_budget_exhausted')
        if len(self.store.list(db, 'shared_frame_receipt', None)) >= FRAME_LIMIT:
            raise ValueError('shared_frame_capacity_requires_maintenance')
        latest = meta.get('latest_frame')
        changed = latest is None or latest['content_sha256'] != row['content_sha256']
        if changed:
            meta['scene_revision'] += 1
        last_received = meta.get('last_frame_received_at')
        fast = last_received is not None and self.clock() - last_received < MIN_FRAME_INTERVAL
        # Changed bytes invalidate old meaning immediately, including a frame
        # that is too fast to start perception. Every admitted packet has full bytes.
        if changed or not fast:
            meta['latest_frame'] = {key: row[key] for key in row if key not in {'summary', 'confidence'}}
            meta['latest_frame'].update(media_type=media_type, received_at=self.clock(), scene_revision=meta['scene_revision'])
        if not fast:
            meta['last_frame_received_at'] = self.clock()
        meta.update(last_sequence=row['sequence'], remaining_frames=meta['remaining_frames'] - 1)
        result = {'ok': True, 'duplicate': False, 'status': 'rate_limited' if fast else 'received',
                  'observation_id': row['observation_id']}
        original = self.store.get(db, 'shared_observation', meta.get('last_observation_id')) if meta.get('last_observation_id') else None
        source_id = (original['personal_source_id'] if original and original.get('scene_revision') == meta['scene_revision']
                     and original['observation']['content_sha256'] == row['content_sha256'] else None)
        self.store.put(db, 'shared_frame_receipt', row['observation_id'], {
            'id': row['observation_id'], 'fingerprint': fingerprint, 'frame': deepcopy(meta['latest_frame']) if not fast or changed else None,
            'source_epoch': meta['source_epoch'], 'scene_revision': meta['scene_revision'],
            'content_sha256': row['content_sha256'], 'interpretation_source_id': source_id, 'result': result})
        self.store.put(db, 'shared_meta', 'state', meta)
        return meta, row, result if fast else None

    def _result(self, db, observation_id, result):
        receipt = self.store.get(db, 'shared_frame_receipt', observation_id)
        if receipt and not receipt.get('redacted') and not receipt.get('invalidated'):
            receipt['result'] = result
            self.store.put(db, 'shared_frame_receipt', observation_id, receipt)
        return result

    def current(self, db, meta, original):
        latest = meta.get('latest_frame')
        if (not latest or original.get('scene_revision') != meta['scene_revision']
                or latest['content_sha256'] != original['observation']['content_sha256']):
            return None
        current = {key: value for key, value in latest.items() if key not in {'received_at', 'scene_revision', 'media_type'}}
        current.update(summary=original['observation']['summary'], confidence=original['observation']['confidence'])
        try:
            normalize_observation(current, source_id=meta['source_id'], source_epoch=meta['source_epoch'],
                                  source_kind=meta['kind'], now=self.clock())
        except ValueError:
            return None
        result = deepcopy(original)
        result.update(observation=current, analyzed_observation=deepcopy(original['observation']),
                      freshness_frame=deepcopy(latest))
        return result

    def receive(self, metadata, raw, media_type):
        if not self.slots.acquire(blocking=False):
            return {'ok': True, 'status': 'busy', 'reason': 'frame_capacity'}
        acquired = False
        ticket = None
        try:
            payload = self._payload(metadata, raw, media_type)
            with self.store.transaction(self.core.token) as db:
                meta, row, early = self._admit(db, payload, media_type)
                if early:
                    return early
                current = self.activity._current(db, meta)
                if current and current['evidence_kind'] == 'vision_interpretation':
                    return self._result(db, row['observation_id'], {'ok': True, 'duplicate': False, 'status': 'refreshed',
                        'observation_id': row['observation_id'], 'interpretation_observation_id': current['id'],
                        'evidence_kind': 'same_pixels_received_again'})
                pending = meta.get('analysis_pending')
                if pending and pending['scene_revision'] == meta['scene_revision'] and pending['content_sha256'] == row['content_sha256']:
                    return self._result(db, row['observation_id'], {'ok': True, 'duplicate': False, 'status': 'refresh_received',
                        'observation_id': row['observation_id'], 'reason': 'same_pixels_perception_in_flight'})
            acquired = self.perception.acquire(blocking=False)
            if not acquired:
                with self.store.transaction(self.core.token) as db:
                    return self._result(db, row['observation_id'], {'ok': True, 'status': 'busy', 'reason': 'image_analysis_active',
                                                                 'observation_id': row['observation_id']})
            with self.store.transaction(self.core.token) as db:
                meta = self.activity._meta(db)
                latest = meta.get('latest_frame')
                if (not meta['enabled'] or meta['source_epoch'] != row['source_epoch'] or not latest
                        or latest['content_sha256'] != row['content_sha256']):
                    return self._result(db, row['observation_id'], {'ok': True, 'status': 'cancelled', 'reason': 'shared_context_changed'})
                if not callable(self.activity.analyze_image):
                    return self._result(db, row['observation_id'], {'ok': True, 'status': 'unavailable', 'reason': 'vision_analyzer_not_configured'})
                ticket = {'id': row['observation_id'], 'source_epoch': meta['source_epoch'], 'scene_revision': meta['scene_revision'],
                          'content_sha256': row['content_sha256'], 'started_at': self.clock()}
                meta['analysis_pending'] = ticket
                self.store.put(db, 'shared_meta', 'state', meta)
            try:
                analysis = self.activity.analyze_image(raw, media_type, metadata['kind'])
            except BaseException:
                with self.store.transaction(self.core.token) as db:
                    meta = self.activity._meta(db)
                    if meta.get('analysis_pending') == ticket:
                        meta.update(enabled=False, source_epoch=meta['source_epoch'] + 1, latest_frame=None,
                                    analysis_pending=None, reason='perception_failed_no_automatic_retry')
                        self.store.put(db, 'shared_meta', 'state', meta)
                raise
            if not isinstance(analysis, dict) or set(analysis) != {'summary', 'confidence'}:
                raise ValueError('invalid_shared_image_analysis')
            summary, confidence = bounded_text(analysis['summary'], 600), finite_number(analysis['confidence'], maximum=1)
            with self.store.transaction(self.core.token) as db:
                meta = self.activity._meta(db)
                if not meta['enabled']:
                    raise ValueError('shared_observation_disabled')
                latest = meta.get('latest_frame')
                if (meta['source_epoch'] != ticket['source_epoch'] or meta['scene_revision'] != ticket['scene_revision']
                        or not latest or latest['content_sha256'] != ticket['content_sha256']):
                    return self._result(db, row['observation_id'], {'ok': True, 'status': 'cancelled', 'reason': 'shared_context_changed'})
                check = {key: value for key, value in latest.items() if key not in {'received_at', 'scene_revision', 'media_type'}}
                normalize_observation({**check, 'summary': summary, 'confidence': confidence}, source_id=meta['source_id'],
                                      source_epoch=meta['source_epoch'], source_kind=meta['kind'], now=self.clock())
                source_id = identifier('src', 'shared', meta['source_epoch'], row['observation_id'])
                if len(self.store.list(db, 'personal_source', None)) >= self.core.personal.meta(db)['policy']['maximum_sources']:
                    raise ValueError('personal_source_limit')
                original = {**row, 'summary': summary, 'confidence': confidence}
                self.store.put(db, 'personal_source', source_id, {'id': source_id, 'source_kind': 'selected_screen_interpretation',
                    'origin_kind': 'capability_evidence', 'scope': 'owner_private', 'status': 'active', 'occurred_at': row['captured_at'],
                    'ingested_at': self.clock(), 'text': summary, 'text_sha256': digest(summary), 'assistant_text': '', 'confidence': confidence,
                    'image_sha256': row['content_sha256'], 'capture_observation_id': row['observation_id'], 'promoted': False,
                    'dependencies': {'sources': [], 'memories': []}})
                self.store.put(db, 'shared_observation', row['observation_id'], {'id': row['observation_id'], 'observation': original,
                    'personal_source_id': source_id, 'evidence_kind': 'vision_interpretation', 'received_at': self.clock(),
                    'fingerprint': digest(original), 'scene_revision': ticket['scene_revision'],
                    'analysis_started_at': ticket['started_at'], 'analysis_completed_at': self.clock()})
                meta['last_observation_id'] = row['observation_id']
                self.store.put(db, 'shared_meta', 'state', meta)
                for receipt in self.store.list(db, 'shared_frame_receipt', None):
                    if (receipt['source_epoch'] == ticket['source_epoch'] and receipt['scene_revision'] == ticket['scene_revision']
                            and receipt.get('content_sha256') == ticket['content_sha256'] and not receipt.get('redacted')):
                        receipt['interpretation_source_id'] = source_id
                        self.store.put(db, 'shared_frame_receipt', receipt['id'], receipt)
                self.activity._trim(db, 'shared_observation', 24)
                return self._result(db, row['observation_id'], {'ok': True, 'duplicate': False, 'status': 'interpreted',
                    'observation_id': row['observation_id'], 'evidence_kind': 'vision_interpretation'})
        finally:
            if acquired:
                try:
                    with self.store.transaction(self.core.token) as db:
                        meta = self.store.get(db, 'shared_meta', 'state')
                        if ticket is not None and meta.get('analysis_pending') == ticket:
                            meta['analysis_pending'] = None
                            self.store.put(db, 'shared_meta', 'state', meta)
                except OwnershipLost:
                    pass
                self.perception.release()
            self.slots.release()


def invalidate_snapshot(db):
    from .store import Store
    for receipt in Store.list(db, 'shared_frame_receipt', None):
        receipt.update(frame=None, invalidated=True,
                       result={'ok': True, 'status': 'cancelled', 'reason': 'snapshot_no_replay'})
        Store.put(db, 'shared_frame_receipt', receipt['id'], receipt)
