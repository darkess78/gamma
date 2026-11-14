"""Thin private STT/TTS and delivery adapter. The Core makes every personal decision."""
import hashlib
import io
import os
import threading
import time
import uuid
import wave

from .audio_policy import likely_playback_echo, speech_chunks
from .interaction_models import InteractionAck, InteractionObservation
from .personal import digest
from .store import OwnershipLost, private_path


TERMINAL = {'played', 'delivered', 'cancelled', 'failed', 'expired'}
VOICE = {'private_voice', 'discord_voice'}
_OUTCOME_REASONS = {
    'generated': 'checked_output_generated', 'generating': 'checked_output_generating',
    'silent': 'core_selected_silence', 'deferred': 'core_not_completed',
    'failed': 'provider_or_turn_failed_no_replay', 'partial': 'partial_output_generated',
    'cancelled': 'output_cancelled', 'ignored': 'observation_ignored',
    'busy': 'private_capacity_unavailable', 'expired': 'receipt_expired_no_replay',
    'pending_or_uncertain_no_replay': 'pending_or_uncertain_no_replay',
}
_BACKEND_OUTCOME_REASONS = frozenset({
    'unverified_speaker', 'voice_disabled', 'private_interaction_capacity',
    'private_foreground_capacity', 'empty_observation', 'likely_playback_echo',
    'playback_source_changed_during_transcription', 'cancelled_before_core',
    'cancelled_after_core', 'cancelled_before_synthesis', 'cancelled_before_release',
    'provider_or_turn_failed_no_replay', 'source_removed',
    'checked_channel_source_unavailable', 'receipt_expired_no_replay',
    'foreground_wait_cancelled', 'foreground_wait_timeout',
})


def _final_outcome(receipt):
    """A bounded backend diagnosis, never model reasons, summaries or result text."""
    status = receipt.get('status')
    if status not in _OUTCOME_REASONS:
        return {'status': 'unknown', 'reason': 'unclassified_backend_outcome'}
    reason = receipt.get('reason')
    return {'status': status, 'reason': reason if reason in _BACKEND_OUTCOME_REASONS
            else _OUTCOME_REASONS[status]}


def validate_wave(data, *, maximum_seconds=12):
    """Accept bounded PCM WAV, never a path or codec that invokes a decoder process."""
    if not isinstance(data, bytes) or not 44 <= len(data) <= 4_000_000:
        raise ValueError('bounded PCM WAV bytes required')
    try:
        with wave.open(io.BytesIO(data), 'rb') as stream:
            channels, width, rate, count = (stream.getnchannels(), stream.getsampwidth(),
                                            stream.getframerate(), stream.getnframes())
            if channels not in {1, 2} or width != 2 or not 8000 <= rate <= 48000:
                raise ValueError('PCM WAV requires 16-bit mono/stereo at 8-48 kHz')
            if not 0 < count / rate <= maximum_seconds:
                raise ValueError('WAV duration outside private capture limit')
            frames = stream.readframes(count)
            if len(frames) != count * channels * width:
                raise ValueError('truncated PCM WAV')
            return count / rate
    except (wave.Error, EOFError) as exc:
        raise ValueError('invalid PCM WAV') from exc


class PrivateInteraction:
    """Synchronous adapter; HTTP/Discord callers run blocking work off their event loop.

    Backends have their own finite transport deadlines. A stop invalidates their
    results immediately, without claiming an already submitted request was killed.
    At most two callers occupy this adapter. Core decisions remain serialized;
    another audio caller can transcribe and wait boundedly, or finish older TTS.
    The adapter creates no threads or unbounded request queue.
    """
    def __init__(self, core, *, transcribe=None, synthesize=None, clock=time.time, monotonic=time.monotonic):
        self.core, self.store = core, core.store
        self.transcribe, self.synthesize, self.clock = transcribe, synthesize, clock
        self.monotonic = monotonic
        self.root = private_path(self.store.root / 'interaction-audio')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._flight = threading.Lock()
        self._requests = threading.BoundedSemaphore(2)
        self._synthesis = threading.Lock()
        self._closed = False
        with self.store.transaction(core.token) as db:
            meta = self.store.get(db, 'interaction_meta', 'state') or {
                'output_epoch': 0, 'voice_enabled': False, 'speech_enabled': False}
            meta.update(generation=core.token[0], output_epoch=meta['output_epoch'] + 1,
                        voice_enabled=False, speech_enabled=False)
            self.store.put(db, 'interaction_meta', 'state', meta)
            for output in self.store.list(db, 'interaction_output', None):
                if output['status'] not in TERMINAL:
                    output.update(status='cancelled', reason='owner_restart_no_replay')
                    self.store.put(db, 'interaction_output', output['id'], output)
            self._expire(db)

    @staticmethod
    def _identifier(value):
        import re
        if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', value):
            raise ValueError('invalid private consumer id')
        return value

    def _meta(self, db):
        return self.store.get(db, 'interaction_meta', 'state')

    def _capabilities(self, db, consumer):
        controls = (consumer or {}).get('voice_controls')
        return controls if controls is not None else self._meta(db)

    def control_consumer(self, consumer_id, action, value):
        """Explicit transport-local controls; never unmute another consumer."""
        if action not in {'voice_enabled', 'speech_enabled'} or type(value) is not bool:
            raise ValueError('invalid private consumer capability control')
        with self.store.transaction(self.core.token) as db:
            consumer = self._consumer(db, consumer_id, touch=True)
            controls = consumer.setdefault('voice_controls', {'voice_enabled': False, 'speech_enabled': False})
            controls[action] = value
            if not value:
                # Pending observations and cached audio bind the previous lease.
                consumer['lease'] = uuid.uuid4().hex
            self.store.put(db, 'interaction_consumer', consumer_id, consumer)
            self._expire(db)
        return self.status(consumer_id)

    def _consumer(self, db, consumer_id, *, required=True, touch=False):
        self._identifier(consumer_id)
        record = self.store.get(db, 'interaction_consumer', consumer_id)
        valid = (not self._closed and record and record['connected']
                 and record['generation'] == self.core.token[0] and self.clock() < record['expires_at'])
        if not valid:
            if required:
                raise ValueError('private consumer disconnected or expired; reconnect explicitly')
            return None
        if touch:
            record['expires_at'] = self.clock() + 60
            self.store.put(db, 'interaction_consumer', consumer_id, record)
        return record

    def _current(self, db, output):
        meta = self._meta(db)
        brain = self.store.get(db, 'brain', 'state')
        consumer = self._consumer(db, output['consumer_id'], required=False)
        return (not self._closed and output['generation'] == self.core.token[0]
                and output['output_epoch'] == meta['output_epoch'] and brain['awake']
                and output['sequence'] == brain['sequence'] and self.clock() < output['expires_at']
                and consumer is not None and consumer['lease'] == output['consumer_lease']
                and self._source_valid(db, output))

    def _source_valid(self, db, output):
        kind = output.get('source_kind', 'turn')
        if kind == 'checked_channel':
            from .channel_audio_source import current_channel_source
            return current_channel_source(self.store, db, output, self.core.token, self.clock())
        if kind != 'turn':
            return False
        turn = self.store.get(db, 'turn', output.get('request_id', output.get('id')))
        return turn is not None and turn.get('status') == 'completed' and not turn.get('redacted')

    def _expire(self, db):
        for request in self.store.list(db, 'interaction_request', None):
            original=digest(request)
            turn = self.store.get(db, 'turn', request['id'])
            if (turn and turn.get('redacted') or request.get('source_kind') == 'checked_channel'
                    and not self._source_valid(db, request)):
                request['receipt'] = {'ok': True, 'status': 'expired',
                    'reason': 'checked_channel_source_unavailable' if request.get('source_kind') == 'checked_channel' else 'source_removed',
                    'output_id': None}
                if 'final_outcome' in request:
                    request['final_outcome'] = _final_outcome(request['receipt'])
            elif request.get('receipt') and self.clock() >= request.get('expires_at', float('inf')):
                # Keep bounded numeric diagnostics and the original fixed outcome, never cached content.
                request['receipt'] = {'ok': True, 'status': 'expired', 'reason': 'receipt_expired_no_replay', 'output_id': None}
                for key in ('timing_ms', 'final_outcome'):
                    if key in request:
                        request['receipt'][key] = request[key]
            if digest(request)!=original:
                self.store.put(db, 'interaction_request', request['id'], request)
        for output in self.store.list(db, 'interaction_output', None):
            original=digest(output)
            turn = self.store.get(db, 'turn', output['request_id'])
            if not self._source_valid(db, output):
                # Operational output caches do not bypass personal source removal.
                source_reason = ('checked_channel_source_unavailable'
                    if output.get('source_kind') == 'checked_channel' else 'source_removed')
                previous_status = output['status']
                output.update(status=(previous_status if output.get('source_kind') == 'checked_channel'
                    and previous_status in TERMINAL else 'expired'), reason=source_reason, display_text='',
                              speech_text='', audio_available=False)
                record = self.store.get(db, 'interaction_request', output['request_id'])
                if record:
                    record['receipt'] = {'ok': True, 'status': 'expired', 'reason': source_reason, 'output_id': None}
                    if 'final_outcome' in record:
                        record['final_outcome'] = _final_outcome(record['receipt'])
                    self.store.put(db, 'interaction_request', record['id'], record)
                path = private_path(self.root / (output['id'] + '.wav'))
                if path.exists():
                    path.unlink()
            elif output['status'] not in TERMINAL and not self._current(db, output):
                output.update(status='expired', reason='source_sequence_consumer_or_owner_unavailable')
            if output['status'] in TERMINAL:
                path = private_path(self.root / (output['id'] + '.wav'))
                if path.exists():
                    path.unlink()
                output['audio_available'] = False
            if self.clock() >= output['expires_at']:
                output.update(display_text='', speech_text='')
            if digest(output)!=original:
                self.store.put(db, 'interaction_output', output['id'], output)

    @staticmethod
    def _public(output):
        return {key: value for key, value in output.items() if key in {
            'id', 'generation', 'output_epoch', 'channel', 'consumer_id', 'status',
            'display_text', 'speech_text', 'emotion', 'audio_available', 'audio_content_type',
            'created_at', 'expires_at', 'reason', 'receipt_id', 'utterance_id', 'chunk_index',
            'chunk_count', 'final_chunk', 'audio_duration_seconds', 'synthesis_ms',
            'first_audio_ready_ms', 'acknowledged_at', 'attempted_at', 'started_at',
            'played_at', 'delivered_at'}}

    def status(self, consumer_id=None):
        with self.store.transaction(self.core.token) as db:
            self._expire(db)
            brain, meta = self.store.get(db, 'brain', 'state'), self._meta(db)
            consumer = self._consumer(db, consumer_id, required=False) if consumer_id else None
            outputs = [output for output in self.store.list(db,'interaction_output',None)
                       if output['consumer_id']==consumer_id]
            outputs.sort(key=lambda output:(output['status'] not in TERMINAL,output['generation'],
                output['sequence'],output['created_at'],output.get('chunk_index',0)),reverse=True)
            capabilities = self._capabilities(db, consumer)
            return {**meta, 'voice_enabled': capabilities['voice_enabled'],
                    'speech_enabled': capabilities['speech_enabled'],
                    'awake': brain['awake'], 'focus': brain['focus'],
                    'consumer': {'id': consumer_id, 'connected': consumer is not None},
                    'outputs': [self._public(o) for o in outputs[:8]],
                    'stt_available': self.transcribe is not None, 'tts_available': self.synthesize is not None}

    def connect(self, consumer_id):
        self._identifier(consumer_id)
        if self._closed:
            raise ValueError('private interaction adapter is closed')
        with self.store.transaction(self.core.token) as db:
            record = {'id': consumer_id, 'generation': self.core.token[0], 'connected': True,
                      'lease': uuid.uuid4().hex, 'expires_at': self.clock() + 60}
            self.store.put(db, 'interaction_consumer', consumer_id, record)
            self._expire(db)
        return self.status(consumer_id)

    def disconnect(self, consumer_id):
        with self.store.transaction(self.core.token) as db:
            record = self._consumer(db, consumer_id, required=False)
            if record:
                record['connected'] = False
                self.store.put(db, 'interaction_consumer', consumer_id, record)
            self._expire(db)
        return self.status(consumer_id)

    def control(self, action, value=None):
        if action not in {'voice_enabled', 'speech_enabled'} or type(value) is not bool:
            raise ValueError('private capability control requires a boolean voice_enabled or speech_enabled')
        with self.store.transaction(self.core.token) as db:
            meta = self._meta(db)
            meta[action] = value
            if not value:
                meta['output_epoch'] += 1
                brain = self.store.get(db, 'brain', 'state')
                brain['sequence'] += 1
                self.store.put(db, 'brain', 'state', brain)
            self.store.put(db, 'interaction_meta', 'state', meta)
            self._expire(db)
        return self.status()

    def interrupt(self, consumer_id, reason='owner_stop', transcript=None):
        if reason not in {'owner_stop', 'barge_in', 'client_disconnect', 'privacy_changed'}:
            raise ValueError('unsupported private interruption reason')
        with self.store.transaction(self.core.token) as db:
            self._consumer(db, consumer_id, touch=True)
            self._expire(db)
            active = [o for o in self.store.list(db, 'interaction_output', None)
                      if o['consumer_id'] == consumer_id and o['status'] == 'started' and o['audio_available']]
            if reason == 'barge_in':
                if not isinstance(transcript, str) or not transcript.strip() or len(transcript) > 8000:
                    return {'interrupted': False, 'reason': 'transcript_required'}
                if any(likely_playback_echo(transcript, o['speech_text']) for o in active):
                    return {'interrupted': False, 'reason': 'likely_playback_echo'}
            meta, brain = self._meta(db), self.store.get(db, 'brain', 'state')
            meta['output_epoch'] += 1
            brain['sequence'] += 1
            self.store.put(db, 'interaction_meta', 'state', meta)
            self.store.put(db, 'brain', 'state', brain)
            for output in self.store.list(db, 'interaction_output', None):
                if output['status'] not in TERMINAL:
                    output.update(status='cancelled', reason=reason)
                    self.store.put(db, 'interaction_output', output['id'], output)
            return {'interrupted': True, 'reason': reason, 'generation': self.core.token[0],
                    'output_epoch': meta['output_epoch']}

    def observe(self, observation):
        return self._observe(InteractionObservation.model_validate(observation))

    def submit_audio(self, observation, audio_bytes):
        observation = InteractionObservation.model_validate(observation)
        if observation.channel not in VOICE:
            raise ValueError('audio requires a private voice channel')
        validate_wave(audio_bytes)
        return self._observe(observation, audio_bytes)

    def _observe(self, observation, audio_bytes=None):
        obs = observation.model_copy(deep=True)
        started = self.monotonic()
        timing_ms = {'stt_ms': 0.0, 'core_ms': 0.0, 'foreground_wait_ms': 0.0,
                     'synthesis_wait_ms': 0.0, 'total_ms': 0.0}
        def finish(receipt, db=None):
            return self._save_receipt(obs.request_id, receipt, db=db, timing_ms=timing_ms, started=started)
        fingerprint = digest({'observation': obs.model_dump(exclude={'capability_speech_allowed'}),
                              'audio_sha256': hashlib.sha256(audio_bytes).hexdigest() if audio_bytes else None})
        with self.store.transaction(self.core.token) as db:
            consumer = self._consumer(db, obs.consumer_id, touch=True)
            self._expire(db)
            old = self.store.get(db, 'interaction_request', obs.request_id)
            if old:
                if old['fingerprint'] != fingerprint:
                    raise ValueError('request id reused with different private observation')
                # A transport retry never regenerates speech, replays old audio or reruns uncertain inference.
                return old['receipt'] or {'ok': True, 'status': 'pending_or_uncertain_no_replay'}
            meta = self._meta(db)
            if obs.speaker != 'owner':
                return finish({'ok': True, 'status': 'ignored', 'reason': 'unverified_speaker', 'output_id': None}, db=db)
            capabilities = self._capabilities(db, consumer)
            if obs.channel in VOICE and not capabilities['voice_enabled']:
                return finish({'ok': True, 'status': 'ignored', 'reason': 'voice_disabled', 'output_id': None}, db=db)
            obs.capability_speech_allowed = bool(capabilities['speech_enabled'] and self.synthesize is not None and obs.channel in VOICE)
            epoch, lease = meta['output_epoch'], consumer['lease']
            # STT may outlast the playback it captured. Keep only this intake's
            # same-consumer acknowledged playback as an ephemeral observation.
            # It is never persisted as a new conversation or a speaker identity.
            playback_at_intake = [{**{key: output[key] for key in
                 ('id', 'request_id', 'generation', 'output_epoch', 'consumer_id', 'consumer_lease')},
                'text': output['speech_text'],
                'text_sha256': hashlib.sha256(output['speech_text'].encode()).hexdigest()}
                for output in self.store.list(db, 'interaction_output', None)
                 if obs.channel in VOICE and (output['consumer_id'] == obs.consumer_id
                     or output.get('source_kind') == 'checked_channel' and output['channel'] in {'private_stream', 'shared_moment'})
                and output['status'] == 'started' and output['audio_available'] and self._current(db, output)][:4]
        if not self._requests.acquire(blocking=False):
            return finish({'ok': True, 'status': 'busy', 'reason': 'private_interaction_capacity', 'output_id': None})
        # Text retains its immediate busy receipt. Admitted audio can transcribe
        # while an older decision owns the single Core slot, then wait boundedly.
        foreground_locked = False
        if audio_bytes is None:
            if not self._flight.acquire(blocking=False):
                self._requests.release()
                return finish({'ok': True, 'status': 'busy', 'reason': 'private_foreground_capacity', 'output_id': None})
            foreground_locked = True
        synthesis_locked = False
        output_ids = []
        received = self.clock()
        envelope = None
        request_reserved = False
        try:
            with self.store.transaction(self.core.token) as db:
                # Close the admission race when two same-ID audio callers arrive
                # before either has finished STT. Never overwrite or replay one.
                previous = self.store.get(db, 'interaction_request', obs.request_id)
                if previous:
                    if previous['fingerprint'] != fingerprint:
                        raise ValueError('request id reused with different private observation')
                    return previous['receipt'] or {'ok': True, 'status': 'pending_or_uncertain_no_replay'}
                self.store.put(db, 'interaction_request', obs.request_id,
                    {'id': obs.request_id, 'fingerprint': fingerprint, 'receipt': None,
                     'created_at': received, 'expires_at': received + 60, 'output_ids': []})
                request_reserved = True
            if audio_bytes is not None:
                if self.transcribe is None:
                    raise ValueError('private transcription provider unavailable')
                phase_started = self.monotonic()
                try:
                    obs.text = self.transcribe(audio_bytes)
                finally:
                    timing_ms['stt_ms'] = self._elapsed_ms(phase_started)
                if not isinstance(obs.text, str) or len(obs.text) > 8000:
                    raise ValueError('invalid bounded transcription')
            if not obs.text.strip():
                receipt = {'ok': True, 'status': 'ignored', 'reason': 'empty_observation', 'output_id': None}
                return finish(receipt)
            with self.store.transaction(self.core.token) as db:
                self._expire(db)
                if any(not self._intake_playback_valid(db, obs, playback) for playback in playback_at_intake):
                    return finish({'ok': True, 'status': 'cancelled',
                        'reason': 'playback_source_changed_during_transcription', 'output_id': None}, db=db)
                active = [o for o in self.store.list(db, 'interaction_output', None)
                          if o['consumer_id'] == obs.consumer_id and o['status'] == 'started' and o['audio_available']]
                pending_synthesis = self._meta(db).get('synthesizing_request_id') not in {None, obs.request_id}
                echo = any(likely_playback_echo(obs.text, playback['text']) for playback in playback_at_intake)
            if echo:
                return finish({'ok': True, 'status': 'ignored', 'reason': 'likely_playback_echo', 'output_id': None})
            if obs.channel in VOICE and (active or pending_synthesis):
                stopped = self.interrupt(obs.consumer_id, reason='barge_in', transcript=obs.text)
                epoch = stopped.get('output_epoch', epoch)
            if not foreground_locked:
                phase_started = self.monotonic()
                try:
                    foreground_locked, wait_reason = self._wait_for_foreground(obs, epoch, lease, playback_at_intake)
                finally:
                    timing_ms['foreground_wait_ms'] = self._elapsed_ms(phase_started)
                if not foreground_locked:
                    return finish({'ok': True,
                        'status': 'busy' if wait_reason == 'foreground_wait_timeout' else 'cancelled',
                        'reason': wait_reason, 'output_id': None})
            with self.store.transaction(self.core.token) as db:
                if not self._request_live(db, obs, epoch, lease):
                    return finish({'ok': True, 'status': 'cancelled', 'reason': 'cancelled_before_core', 'output_id': None}, db=db)
            phase_started = self.monotonic()
            try:
                envelope = self.core.input(obs.text, obs.session_id, obs.request_id,
                                           observation=obs.model_dump(exclude={'text'}))
            finally:
                timing_ms['core_ms'] = self._elapsed_ms(phase_started)
            response = envelope['result']['assistant_response']
            with self.store.transaction(self.core.token) as db:
                if not self._request_live(db, obs, epoch, lease):
                    return finish({'ok': True, 'status': 'cancelled', 'reason': 'cancelled_after_core', 'output_id': None}, db=db)
                turn = self.store.get(db, 'turn', obs.request_id)
                if not turn or turn.get('status') != 'completed' or turn.get('redacted'):
                    return finish({'ok': True, 'status': 'deferred', 'reason': response.get('reason_code'), 'output_id': None, 'result': envelope['result']}, db=db)
                sequence = turn['sequence']
            display, speech = response.get('display_text') or '', response.get('speech_text') or ''
            if response.get('response_action') not in {'reply', 'acknowledge'} or not display.strip():
                return finish({'ok': True, 'status': 'silent', 'output_id': None, 'result': envelope['result']})
            reason = None
            try:
                chunks = speech_chunks(speech) if speech and obs.capability_speech_allowed else ['']
            except ValueError as exc:
                chunks, reason = [''], str(exc)
            if not chunks:
                chunks = ['']
            with self.store.transaction(self.core.token) as db:
                meta = self._meta(db)
                if any(chunks):
                    meta['synthesizing_request_id'] = obs.request_id
                    self.store.put(db, 'interaction_meta', 'state', meta)
            # A later owner utterance can be transcribed on its existing caller
            # thread while synthesis runs. It never starts a parallel Core draft.
            self._flight.release(); foreground_locked = False
            if any(chunks):
                phase_started = self.monotonic()
                try:
                    synthesis_locked = self._synthesis.acquire(timeout=120)
                finally:
                    timing_ms['synthesis_wait_ms'] = self._elapsed_ms(phase_started)
                if not synthesis_locked:
                    raise TimeoutError('private synthesis capacity deadline')
            return self._render_chunks(obs, response, chunks, reason, envelope,
                epoch=epoch, lease=lease, sequence=sequence, received=received, output_ids=output_ids,
                timing_ms=timing_ms, started=started)
        except OwnershipLost:
            raise
        except Exception:
            if not request_reserved:
                raise
            try:
                failure = finish({'ok': False, 'status': 'partial' if output_ids else 'failed',
                    'reason': 'provider_or_turn_failed_no_replay', 'output_id': output_ids[0] if output_ids else None,
                    'output_ids': list(output_ids)})
            except OwnershipLost:
                raise
            if output_ids:
                return failure
            raise
        finally:
            if foreground_locked:
                self._flight.release()
            if synthesis_locked:
                self._synthesis.release()
            self._requests.release()
            try:
                with self.store.transaction(self.core.token) as db:
                    meta = self._meta(db)
                    if meta.get('synthesizing_request_id') == obs.request_id:
                        meta.pop('synthesizing_request_id', None)
                        self.store.put(db, 'interaction_meta', 'state', meta)
            except OwnershipLost:
                pass

    def _wait_for_foreground(self, obs, epoch, lease, playback_at_intake):
        """Wait for one unsent audio decision, never resubmit inference or replay it."""
        if self._flight.acquire(blocking=False):
            return True, None
        deadline = self.monotonic() + 30.0
        def live():
            with self.store.transaction(self.core.token) as db:
                return (self._request_live(db, obs, epoch, lease)
                        and self.store.get(db, 'brain', 'state')['awake']
                        and all(self._intake_playback_valid(db, obs, playback)
                                for playback in playback_at_intake))
        while True:
            if not live():
                return False, 'foreground_wait_cancelled'
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                return False, 'foreground_wait_timeout'
            if self._flight.acquire(timeout=min(.05, remaining)):
                try:
                    valid = live()
                except BaseException:
                    self._flight.release()
                    raise
                if valid:
                    return True, None
                self._flight.release()
                return False, 'foreground_wait_cancelled'

    def _render_chunks(self, obs, response, chunks, reason, envelope, *,
                       epoch, lease, sequence, received, output_ids, source_kind='turn', timing_ms=None, started=None):
        """Shared bounded renderer. Callers already hold the existing synthesis slot."""
        display = response.get('display_text') or ''
        def finish(receipt, db=None):
            return self._save_receipt(obs.request_id, receipt, db=db, timing_ms=timing_ms, started=started)
        total_duration, total_bytes, first_ready = 0.0, 0, None
        delivery = response.get('tts_metadata', {}).get('voice_delivery')
        if delivery is None:
            with self.store.transaction(self.core.token) as db:
                delivery = self.store.get(db, 'brain', 'state').get('voice_delivery',
                    {'pace': 1.0, 'loudness_db': 0.0})
        delivery = dict(delivery)
        for index, chunk in enumerate(chunks):
            with self.store.transaction(self.core.token) as db:
                if not self._release_live(db, obs, epoch, lease, sequence, received, source_kind=source_kind):
                    return finish({'ok': True, 'status': 'cancelled',
                        'reason': 'cancelled_before_synthesis', 'output_id': output_ids[0] if output_ids else None,
                        'output_ids': output_ids}, db=db)
            audio, mime, duration, delivery_metadata = None, None, 0.0, None
            synthesis_started = self.monotonic()
            if chunk:
                audio, mime = self.synthesize(chunk, response.get('emotion', 'neutral'))
                if mime != 'audio/wav':
                    raise ValueError('private playback requires bounded PCM WAV')
                from ..voice.delivery_audio import transform_wave
                audio, delivery_metadata = transform_wave(audio, pace=delivery['pace'],
                    loudness_db=delivery['loudness_db'])
                duration = validate_wave(audio, maximum_seconds=90 - total_duration)
                total_duration += duration; total_bytes += len(audio)
                if total_bytes > 4_000_000:
                    raise ValueError('private_utterance_audio_size_limit')
            synthesis_ms = self._elapsed_ms(synthesis_started)
            identifier = 'output-' + uuid.uuid4().hex
            with self.store.transaction(self.core.token) as db:
                self._expire(db)
                if not self._release_live(db, obs, epoch, lease, sequence, received, source_kind=source_kind):
                    return finish({'ok': True, 'status': 'cancelled',
                        'reason': 'cancelled_before_release', 'output_id': output_ids[0] if output_ids else None,
                        'output_ids': output_ids}, db=db)
                if first_ready is None and audio is not None:
                    first_ready = (self._elapsed_ms(started) if started is not None
                                   else round((self.clock() - received) * 1000, 3))
                    if timing_ms is not None:
                        timing_ms['first_audio_ready_ms'] = first_ready
                # A released chunk must have enough bounded time for its
                # already-generated predecessors and its own audio to play.
                delivery_tail=total_duration+10
                delivery_deadline = received + 180
                if source_kind == 'checked_channel':
                    delivery_deadline = min(delivery_deadline,
                        self.store.get(db, 'channel_audio_request', obs.request_id)['origin']['expires_at'])
                if self.clock()+delivery_tail>delivery_deadline:
                    raise ValueError('private_utterance_delivery_deadline')
                output = {'id': identifier, 'request_id': obs.request_id, 'generation': self.core.token[0],
                    'output_epoch': epoch, 'sequence': sequence, 'consumer_id': obs.consumer_id,
                    'consumer_lease': lease, 'channel': obs.channel, 'status': 'generated',
                    'display_text': display if index == 0 else chunk, 'speech_text': chunk if audio else '',
                    'emotion': response.get('emotion', 'neutral'), 'audio_available': audio is not None,
                    'audio_content_type': mime, 'created_at': self.clock(),
                    'expires_at': min(delivery_deadline,self.clock()+max(60,delivery_tail)),
                    'utterance_id': obs.request_id, 'chunk_index': index, 'chunk_count': len(chunks),
                    'final_chunk': index == len(chunks) - 1, 'audio_duration_seconds': duration,
                    'synthesis_ms': synthesis_ms, 'first_audio_ready_ms': first_ready}
                if delivery_metadata is not None:
                    output['voice_delivery'] = delivery_metadata
                if source_kind != 'turn':
                    output['source_kind'] = source_kind
                if reason:
                    output['reason'] = reason
                if audio is not None:
                    path = private_path(self.root / (identifier + '.wav'))
                    with path.open('xb') as stream:
                        stream.write(audio); stream.flush(); os.fsync(stream.fileno())
                    if os.name != 'nt':
                        path.chmod(0o600)
                    output['audio_sha256'] = hashlib.sha256(audio).hexdigest()
                self.store.put(db, 'interaction_output', identifier, output)
                output_ids.append(identifier)
                receipt = {'ok': True, 'status': 'generated' if index == len(chunks) - 1 else 'generating',
                    'output_id': output_ids[0], 'output_ids': list(output_ids), 'result': envelope['result'],
                    'first_audio_ready_ms': first_ready, 'speech_limit_reason': reason}
                record = self.store.get(db, 'interaction_request', obs.request_id)
                record['output_ids'] = list(output_ids)
                if len(chunks) > 1:
                    record['expires_at'] = received + 180
                self.store.put(db, 'interaction_request', obs.request_id, record)
                receipt = finish(receipt, db=db)
        return receipt

    def _intake_playback_valid(self, db, observation, playback):
        intake_consumer = self._consumer(db, observation.consumer_id, required=False)
        consumer = self._consumer(db, playback.get('consumer_id', observation.consumer_id), required=False)
        output = self.store.get(db, 'interaction_output', playback['id'])
        return (intake_consumer is not None and consumer is not None and consumer['lease'] == playback['consumer_lease']
                and self.core.token[0] == playback['generation']
                and self._meta(db)['output_epoch'] == playback['output_epoch']
                and output is not None and output['request_id'] == playback['request_id']
                and output['generation'] == playback['generation'] and output['output_epoch'] == playback['output_epoch']
                and self._source_valid(db, output)
                and hashlib.sha256(playback['text'].encode()).hexdigest() == playback['text_sha256'])

    def _release_live(self, db, obs, epoch, lease, sequence, received, *, source_kind='turn'):
        brain = self.store.get(db, 'brain', 'state')
        return (self._request_live(db, obs, epoch, lease) and brain['awake']
                and brain['sequence'] == sequence and self.clock() < received + 180
                and self._source_valid(db, {'request_id': obs.request_id, 'source_kind': source_kind}))

    def _request_live(self, db, obs, epoch, lease):
        consumer = self._consumer(db, obs.consumer_id, required=False)
        return (not self._closed and consumer is not None and consumer['lease'] == lease
                and self._meta(db)['output_epoch'] == epoch
                and (obs.channel not in VOICE or self._capabilities(db, consumer)['voice_enabled']))

    def _elapsed_ms(self, started):
        return round(max(0.0, (self.monotonic() - started) * 1000), 3)

    def _save_receipt(self, request_id, receipt, db=None, *, timing_ms=None, started=None):
        if db is None:
            with self.store.transaction(self.core.token) as connection:
                return self._save_receipt(request_id, receipt, db=connection,
                                          timing_ms=timing_ms, started=started)
        record = self.store.get(db, 'interaction_request', request_id)
        receipt = dict(receipt)
        if timing_ms is not None:
            timing_ms['total_ms'] = self._elapsed_ms(started)
            receipt['timing_ms'] = dict(timing_ms)
        receipt['final_outcome'] = _final_outcome(receipt)
        if record:
            record['receipt'] = receipt
            if 'timing_ms' in receipt:
                record['timing_ms'] = dict(receipt['timing_ms'])
            record['final_outcome'] = dict(receipt['final_outcome'])
            self.store.put(db, 'interaction_request', request_id, record)
        return receipt

    def next_output(self, consumer_id):
        with self.store.transaction(self.core.token) as db:
            self._consumer(db, consumer_id, touch=True)
            self._expire(db)
            outputs = [o for o in self.store.list(db, 'interaction_output', None)
                       if o['consumer_id'] == consumer_id and o['status'] in {'generated', 'offered'}]
            output = sorted(outputs, key=lambda o: (o['created_at'], o.get('chunk_index', 0)))[0] if outputs else None
            if output and output['status'] == 'generated':
                output['status'] = 'offered'
                self.store.put(db, 'interaction_output', output['id'], output)
            return {'generation': self.core.token[0], 'output_epoch': self._meta(db)['output_epoch'],
                    'output': self._public(output) if output else None}

    def audio(self, output_id, consumer_id, generation, output_epoch):
        with self.store.transaction(self.core.token) as db:
            self._consumer(db, consumer_id, touch=True)
            self._expire(db)
            output = self._find(db, output_id, consumer_id, generation, output_epoch)
            if not output['audio_available'] or output['status'] not in {'offered', 'started'}:
                raise ValueError('private audio is not currently released')
            data = private_path(self.root / (output_id + '.wav')).read_bytes()
            if hashlib.sha256(data).hexdigest() != output['audio_sha256']:
                raise ValueError('private audio integrity changed')
            return data, output['audio_content_type']

    def _find(self, db, output_id, consumer_id, generation, output_epoch):
        import re
        if not isinstance(output_id, str) or not re.fullmatch(r'output-[a-f0-9]{32}', output_id):
            raise ValueError('invalid private output id')
        output = self.store.get(db, 'interaction_output', output_id)
        if (not output or output['consumer_id'] != consumer_id or type(generation) is not int
                or type(output_epoch) is not int or output['generation'] != generation
                or output['output_epoch'] != output_epoch or not self._current(db, output)):
            raise ValueError('private output is stale or belongs to another consumer')
        return output

    def acknowledge(self, consumer_id, output_id, generation, output_epoch, status, receipt_id=None):
        ack = InteractionAck(audience='owner_private', consumer_id=consumer_id, output_id=output_id,
                             generation=generation, output_epoch=output_epoch, status=status, receipt_id=receipt_id)
        with self.store.transaction(self.core.token) as db:
            self._consumer(db, consumer_id, touch=True)
            self._expire(db)
            output = self._find(db, output_id, consumer_id, generation, output_epoch)
            if output['status'] == status:
                return self._public(output)
            if output['status'] in TERMINAL:
                raise ValueError('private output receipt is already final')
            if status == 'attempted' and output['status'] != 'offered':
                raise ValueError('private transport must be offered before an attempt')
            if status == 'started' and output['status'] not in {'offered', 'attempted'}:
                raise ValueError('private output must be offered before playback starts')
            if status == 'played' and (output['status'] != 'started' or not output['audio_available']):
                raise ValueError('played requires previously started audio')
            if status == 'delivered' and output['status'] not in {'offered', 'attempted'}:
                raise ValueError('delivered requires an offered transport output')
            now = self.clock()
            output.update(status=status, acknowledged_at=now)
            if status in {'attempted', 'started', 'played', 'delivered'}:
                output.setdefault(status + '_at', now)
            if ack.receipt_id:
                output['receipt_id'] = ack.receipt_id
            self.store.put(db, 'interaction_output', output_id, output)
            receipt = self._public(output)
            self._expire(db)
            return receipt

    def close(self):
        self._closed = True
        try:
            with self.store.transaction(self.core.token) as db:
                meta = self._meta(db)
                meta['output_epoch'] += 1
                self.store.put(db, 'interaction_meta', 'state', meta)
                brain = self.store.get(db, 'brain', 'state')
                brain['sequence'] += 1
                self.store.put(db, 'brain', 'state', brain)
                self._expire(db)
        except OwnershipLost:
            pass
