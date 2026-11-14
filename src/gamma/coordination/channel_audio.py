"""Opt-in audio for canonical private stream decisions, reusing P5 transport."""
import re
from types import SimpleNamespace

from .audio_policy import speech_chunks
from .channel_audio_source import ChannelAudioOrigin, checked_stream_records, parse_channel_origin
from .personal import digest
from .store import OwnershipLost


class ChannelAudio:
    def __init__(self, interaction):
        from .interaction import PrivateInteraction
        if not isinstance(interaction, PrivateInteraction):
            raise ValueError('existing_private_interaction_required')
        self.interaction = interaction
        self.core, self.store, self.clock = interaction.core, interaction.store, interaction.clock
        self._closed = False
        with self.store.transaction(self.core.token) as db:
            for row in self.store.list(db, 'channel_audio_consent', None):
                row.update(enabled=False, epoch=row['epoch'] + 1, consumer_lease='', reason='owner_restart_no_replay')
                self.store.put(db, 'channel_audio_consent', row['id'], row)
            for row in self.store.list(db, 'channel_audio_request', None):
                if row['status'] in {'rendering', 'generated'}:
                    row.update(status='cancelled', reason='owner_restart_no_replay')
                    self.store.put(db, 'channel_audio_request', row['id'], row)
            interaction._expire(db)

    def connect(self, consumer_id):
        if self._closed:
            raise ValueError('channel_audio_closed')
        self.interaction.connect(consumer_id)
        with self.store.transaction(self.core.token) as db:
            consumer = self.interaction._consumer(db, consumer_id)
            old = self.store.get(db, 'channel_audio_consent', consumer_id) or {'epoch': 0}
            self.store.put(db, 'channel_audio_consent', consumer_id,
                {'id': consumer_id, 'generation': self.core.token[0], 'enabled': False,
                 'epoch': old['epoch'] + 1, 'consumer_lease': consumer['lease'], 'reason': 'listen_opt_in_required'})
            self.interaction._expire(db)
        return self.status(consumer_id)

    def enable(self, consumer_id, enabled):
        if type(enabled) is not bool:
            raise ValueError('channel_audio_consent_requires_boolean')
        with self.store.transaction(self.core.token) as db:
            consumer = self.interaction._consumer(db, consumer_id)
            if self._closed:
                raise ValueError('channel_audio_closed')
            row = self.store.get(db, 'channel_audio_consent', consumer_id)
            if not row:
                raise ValueError('channel_audio_connect_required')
            if row['enabled'] != enabled or row['consumer_lease'] != consumer['lease']:
                row.update(enabled=enabled, epoch=row['epoch'] + 1, consumer_lease=consumer['lease'],
                           generation=self.core.token[0], reason='owner_listen_enabled' if enabled else 'owner_listen_disabled')
                self.store.put(db, 'channel_audio_consent', consumer_id, row)
            self.interaction._expire(db)
        return self.status(consumer_id)

    def interrupt(self, consumer_id):
        with self.store.transaction(self.core.token) as db:
            self.interaction._consumer(db, consumer_id)
            row = self.store.get(db, 'channel_audio_consent', consumer_id)
            if row:
                row.update(epoch=row['epoch'] + 1, reason='owner_channel_audio_stop')
                self.store.put(db, 'channel_audio_consent', consumer_id, row)
            self.interaction._expire(db)
        return self.status(consumer_id)

    def disconnect(self, consumer_id):
        with self.store.transaction(self.core.token) as db:
            row = self.store.get(db, 'channel_audio_consent', consumer_id)
            if row:
                row.update(enabled=False, epoch=row['epoch'] + 1, reason='private_consumer_disconnected')
                self.store.put(db, 'channel_audio_consent', consumer_id, row)
            self.interaction._expire(db)
        self.interaction.disconnect(consumer_id)
        return {'ok': True, 'connected': False, 'listen_enabled': False}

    def status(self, consumer_id):
        interaction = self.interaction.status(consumer_id)
        with self.store.transaction(self.core.token) as db:
            row = self.store.get(db, 'channel_audio_consent', consumer_id)
            consumer = self.interaction._consumer(db, consumer_id, required=False)
            enabled = bool(not self._closed and row and row['enabled'] is True and consumer
                           and row['consumer_lease'] == consumer['lease'] and row['generation'] == self.core.token[0])
            return {'scope': 'owner_private', 'connected': consumer is not None,
                'listen_enabled': enabled, 'tts_available': self.interaction.synthesize is not None,
                'output_epoch': interaction['output_epoch'],
                'outputs': [o for o in interaction['outputs'] if o['channel'] in {'private_stream', 'shared_moment'}],
                'publication_authorized': False}

    def _claim(self, db, stream_output_id, consumer_id):
        consumer = self.interaction._consumer(db, consumer_id, touch=True)
        consent = self.store.get(db, 'channel_audio_consent', consumer_id)
        if (self._closed or not consent or consent['enabled'] is not True
            or consent['consumer_lease'] != consumer['lease'] or consent['generation'] != self.core.token[0]):
            raise ValueError('channel_audio_listen_not_enabled')
        output, session, event, review_id, review, text = checked_stream_records(
            self.store, db, stream_output_id, consumer_id, self.core.token, self.clock())
        request_id = 'channel-audio-' + stream_output_id.rsplit('-', 1)[-1]
        now = float(self.clock())
        deadline = min(review['expires_at'], session['expires_at'], now + 180)
        if now + 10 >= deadline:
            raise ValueError('checked_channel_delivery_window_too_short')
        origin = ChannelAudioOrigin(kind='private_stream', stream_output_id=stream_output_id,
            event_id=output['event_id'], session_id=session['id'], review_id=review_id,
            review_context_sha256=review['context_sha256'], response_sha256=digest(review['response']),
            text_sha256=digest(text), generation=self.core.token[0], sequence=output['sequence'],
            revision=session['revision'], stream_lease=session['consumer']['lease'],
            consumer_id=consumer_id, consumer_lease=consumer['lease'], consent_epoch=consent['epoch'],
            output_epoch=self.interaction._meta(db)['output_epoch'], created_at=now, expires_at=float(deadline))
        output.update(audio_request_id=request_id, expires_at=deadline)
        self.store.put(db, 'stream_rehearsal_output', stream_output_id, output)
        row = {'id': request_id, 'origin': origin.model_dump(), 'status': 'rendering',
               'fingerprint': digest(origin.model_dump()), 'reason': 'canonical_checked_stream_audio'}
        self.store.put(db, 'channel_audio_request', request_id, row)
        self.store.put(db, 'interaction_request', request_id,
            {'id': request_id, 'fingerprint': row['fingerprint'], 'receipt': None, 'source_kind': 'checked_channel',
             'created_at': now, 'expires_at': deadline, 'output_ids': []})
        meta = self.interaction._meta(db)
        meta['synthesizing_request_id'] = request_id
        self.store.put(db, 'interaction_meta', 'state', meta)
        response = {**review['response'], 'display_text': text, 'speech_text': text, 'delivery_mode': 'speech'}
        return row, response

    def render(self, stream_output_id, consumer_id):
        return self._render(stream_output_id, consumer_id, moment=False)

    def render_moment(self, moment_id, consumer_id):
        from .moment_audio_policy import MomentSpeechLimit
        try:
            return self._render(moment_id, consumer_id, moment=True)
        except MomentSpeechLimit:
            return {'ok': True, 'status': 'unavailable', 'reason': 'moment_speech_limit', 'output_id': None}

    def _render(self, stream_output_id, consumer_id, *, moment):
        pattern = r'[A-Za-z0-9_-]{1,80}' if moment else r'stream-output-[a-f0-9]{32}'
        if not isinstance(stream_output_id, str) or not re.fullmatch(pattern, stream_output_id):
            raise ValueError('invalid_checked_stream_output_id')
        self.interaction._identifier(consumer_id)
        from .channel_audio_moment_source import request_id as moment_request_id
        request_id = moment_request_id(stream_output_id) if moment else 'channel-audio-' + stream_output_id.rsplit('-', 1)[-1]
        if self.interaction.synthesize is None:
            return {'ok': True, 'status': 'unavailable', 'reason': 'private_tts_unavailable', 'output_id': None}
        # Background commentary yields rather than occupying a new queue while
        # ordinary private speech is synthesizing. These are the actual P5 locks.
        if not self.interaction._requests.acquire(blocking=False):
            return {'ok': True, 'status': 'busy', 'reason': 'private_interaction_capacity', 'output_id': None}
        synthesis_locked = self.interaction._synthesis.acquire(blocking=False)
        if not synthesis_locked:
            self.interaction._requests.release()
            return {'ok': True, 'status': 'busy', 'reason': 'foreground_synthesis_has_priority', 'output_id': None}
        output_ids = []
        claimed = False
        try:
            with self.store.transaction(self.core.token) as db:
                self.interaction._consumer(db, consumer_id, touch=True)
                self.interaction._expire(db)
                old = self.store.get(db, 'channel_audio_request', request_id)
                if old:
                    if old['origin']['consumer_id'] != consumer_id:
                        raise ValueError('checked_channel_belongs_to_another_consumer')
                    prior = self.store.get(db, 'interaction_request', request_id)
                    return prior.get('receipt') or {'ok': True, 'status': 'uncertain_no_replay', 'output_id': None}
                if any(output['consumer_id'] == consumer_id
                       and output['status'] not in {'played', 'delivered', 'cancelled', 'failed', 'expired'}
                       for output in self.store.list(db, 'interaction_output', None)):
                    return {'ok': True, 'status': 'busy', 'reason': 'private_audio_pending', 'output_id': None}
                if moment:
                    from .channel_audio_moment_source import claim_moment
                    row, response = claim_moment(self, db, stream_output_id, consumer_id)
                else:
                    row, response = self._claim(db, stream_output_id, consumer_id)
                claimed = True
                origin = parse_channel_origin(row['origin'])
            chunks = speech_chunks(response['speech_text'])
            obs = SimpleNamespace(request_id=request_id, consumer_id=consumer_id, channel=origin.kind)
            envelope = {'result': {'generation': self.core.token[0], 'assistant_response': response,
                                  'decision': {'decision': response['response_action'], 'reason': response['reason_code']}}}
            receipt = self.interaction._render_chunks(obs, response, chunks, None, envelope,
                epoch=origin.output_epoch, lease=origin.consumer_lease, sequence=origin.sequence,
                received=origin.created_at, output_ids=output_ids, source_kind='checked_channel')
            with self.store.transaction(self.core.token) as db:
                current = self.store.get(db, 'channel_audio_request', request_id)
                current.update(status='generated' if receipt['status'] == 'generated' else 'cancelled',
                               reason=receipt.get('reason', 'checked_audio_generated'))
                self.store.put(db, 'channel_audio_request', request_id, current)
                self.interaction._expire(db)
            return receipt
        except OwnershipLost:
            raise
        except Exception:
            if claimed:
                with self.store.transaction(self.core.token) as db:
                    row = self.store.get(db, 'channel_audio_request', request_id)
                    row.update(status='failed', reason='provider_failed_no_replay')
                    self.store.put(db, 'channel_audio_request', request_id, row)
                    receipt = {'ok': False, 'status': 'partial' if output_ids else 'failed',
                        'reason': 'provider_failed_no_replay', 'output_id': output_ids[0] if output_ids else None,
                        'output_ids': list(output_ids)}
                    self.interaction._save_receipt(request_id, receipt, db=db)
                    self.interaction._expire(db)
                return receipt
            raise
        finally:
            self.interaction._synthesis.release()
            self.interaction._requests.release()
            if claimed:
                try:
                    with self.store.transaction(self.core.token) as db:
                        meta = self.interaction._meta(db)
                        if meta.get('synthesizing_request_id') == request_id:
                            meta.pop('synthesizing_request_id', None)
                            self.store.put(db, 'interaction_meta', 'state', meta)
                except OwnershipLost:
                    pass

    def close(self):
        self._closed = True
        try:
            with self.store.transaction(self.core.token) as db:
                for row in self.store.list(db, 'channel_audio_consent', None):
                    row.update(enabled=False, epoch=row['epoch'] + 1, reason='channel_audio_shutdown')
                    self.store.put(db, 'channel_audio_consent', row['id'], row)
                self.interaction._expire(db)
        except OwnershipLost:
            pass


def invalidate_snapshot(db):
    from .channel_audio_source import invalidate_snapshot as invalidate
    return invalidate(db)
