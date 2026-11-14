"""Opt-in Discord transport for the existing isolated private interaction owner.

This adapter never constructs a Core or loads production Discord configuration.
The caller supplies an authenticated interaction service and an explicit owner
DM pair. Voice additionally requires a pinned room containing only that owner
and this bot. Provider acceptance and playback callbacks do not prove a human
read or heard the result.
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
from collections import Counter, deque
import inspect
import io
import math
import re
import time
import uuid
from time import monotonic as _monotonic

from .discord_scope import admit_event, discord_id, validate_scope
from .interaction_models import InteractionObservation
from .discord_capture import OwnerPCMBuffer
from ..integrations.discord.delivery import message_chunks
from ..integrations.discord.voice_controls import VoiceSessionController

_heartbeat_clock = time.monotonic
_voice_timing_clock = time.monotonic

_VOICE_OUTCOMES = frozenset({'generated', 'generating', 'busy', 'silent', 'ignored',
    'cancelled', 'deferred', 'failed', 'partial', 'expired', 'dropped',
    'pending_or_uncertain_no_replay', 'unclassified'})
_VOICE_REASONS = frozenset({'private_interaction_capacity', 'private_foreground_capacity',
    'empty_observation', 'likely_playback_echo', 'unverified_speaker', 'voice_disabled',
    'cancelled_before_core', 'cancelled_after_core', 'playback_source_changed_during_transcription',
    'cancelled_before_synthesis', 'cancelled_before_release', 'provider_or_turn_failed_no_replay',
    'receipt_expired_no_replay', 'voice_session_closed', 'voice_epoch_or_privacy_changed',
    'transport_request_failed', 'transport_wait_cancelled', 'audio_queue_full'})


async def _call(method, *args, **kwargs):
    if inspect.iscoroutinefunction(method):
        return await method(*args, **kwargs)
    return await asyncio.to_thread(method, *args, **kwargs)


def _sdk_id(value):
    # SDK IDs are ints. Do not accept booleans or user-supplied numeric coercions.
    return discord_id(str(value)) if type(value) is int else discord_id(value)


def _private_dm(channel, owner_id):
    kind = getattr(channel, 'type', None)
    kind = getattr(kind, 'value', kind)
    recipient = getattr(channel, 'recipient', None)
    try:
        recipients = getattr(channel, 'recipients', None)
        if recipients is not None:
            if len(recipients) != 1:
                return False
            if recipient is not None and _sdk_id(recipient.id) != _sdk_id(recipients[0].id):
                return False
            recipient = recipients[0]
        return (type(kind) is int and kind == 1 and recipient is not None
                and _sdk_id(recipient.id) == owner_id)
    except (AttributeError, ValueError):
        return False


def _playback_timeout(output):
    """Allow the validated chunk duration plus a bounded transport margin."""
    duration = output.get('audio_duration_seconds')
    if type(duration) in {int, float} and math.isfinite(duration) and 0 < duration <= 90:
        return min(100, duration + 10)
    return 60


def _stop_playback(voice):
    """The receive extension's stop() also closes the microphone receiver."""
    stop_playing = getattr(voice, 'stop_playing', None)
    if callable(stop_playing):
        stop_playing()
    else:
        # Ordinary discord.py VoiceClient.stop() only stops playback.
        voice.stop()


class PrivateDiscordAdapter:
    """SDK callback adapter. Instantiation and offline tests never log in."""

    def __init__(self, client, interaction, *, owner_user_id, dm_channel_id,
                 voice_guild_id=None, voice_channel_id=None, voice_factory=None,
                 sink_factory=None, audio_source_factory=None, allowed_mentions=None,
                 room_policy=None, dm_resolver=None):
        self.scope = validate_scope(owner_user_id, dm_channel_id, voice_guild_id, voice_channel_id)
        self.client, self.interaction = client, interaction
        self.dm_consumer = 'discord-dm'
        self.voice_consumer = 'discord-voice'
        self._dm = None
        self._started = False
        self._closed = False
        self._tasks = []
        self._text_lock = asyncio.Lock()
        self._voice_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._audio_queue = asyncio.Queue(maxsize=2)
        self._processing_utterances = 0
        self._sinks = set()
        self._controller = None
        self._voice_factory = voice_factory
        self._sink_factory = sink_factory
        self._audio_source_factory = audio_source_factory
        self._allowed_mentions = allowed_mentions
        self._room_policy = room_policy
        self._dm_resolver = dm_resolver
        self._voice_epoch = 0
        self._heartbeats = {}
        self._voice_requests = deque(maxlen=32)
        self._voice_playback = deque(maxlen=32)
        self._voice_outcomes = Counter()
        self.stats = {'text_delivered': 0, 'audio_played': 0, 'ignored': 0,
                      'dropped': 0, 'failures': 0}

    @staticmethod
    def _milliseconds(start, end):
        if (type(start) not in {int, float} or type(end) not in {int, float}
                or not math.isfinite(start) or not math.isfinite(end)):
            return None
        elapsed = (end - start) * 1000
        return round(elapsed, 3) if 0 <= elapsed <= 600_000 else None

    def voice_timing_snapshot(self):
        """Bounded transport diagnostics. Never include PCM, transcripts or results."""
        receive_active = None
        voice = getattr(self._controller, '_voice', None)
        if voice is not None:
            try:
                from discord.ext import voice_recv
                if isinstance(voice, voice_recv.VoiceRecvClient):
                    receive_active = bool(voice.is_listening())
            except Exception:
                pass
        return {'receive_active': receive_active,
                'queued_utterances': self._audio_queue.qsize(),
                'processing_utterances': self._processing_utterances,
                'recent_requests': [dict(row) for row in self._voice_requests],
                'recent_playback': [dict(row) for row in self._voice_playback],
                'outcomes': dict(self._voice_outcomes)}

    def _record_voice_request(self, item, receipt, *, finished=None):
        receipt = receipt if isinstance(receipt, dict) else {}
        status = receipt.get('status')
        status = status if isinstance(status, str) and status in _VOICE_OUTCOMES else 'unclassified'
        row = {'request_id': item['_voice_request_id'], 'observed_at': time.time(),
               'status': status}
        reason = receipt.get('reason')
        if reason is not None:
            row['reason'] = reason if isinstance(reason, str) and reason in _VOICE_REASONS else 'other'
        end = _voice_timing_clock() if finished is None else finished
        for key, first, last in (
                ('capture_ms', item.get('capture_started_monotonic'), item.get('capture_ended_monotonic')),
                ('end_of_speech_ms', item.get('capture_ended_monotonic'), item.get('capture_released_monotonic')),
                ('dispatch_ms', item.get('capture_released_monotonic'), item.get('_voice_enqueued_at')),
                ('queue_ms', item.get('_voice_enqueued_at'), item.get('_voice_dequeued_at')),
                ('request_ms', item.get('_voice_request_started_at'), end)):
            value = self._milliseconds(first, last)
            if value is not None:
                row[key] = value
        capture_reason = item.get('capture_reason')
        if isinstance(capture_reason, str) and capture_reason in {'speaking_stop', 'silence_timeout', 'max_duration'}:
            row['capture_reason'] = capture_reason
        timings = receipt.get('timing_ms')
        timings = timings if isinstance(timings, dict) else {}
        for key in ('stt_ms', 'foreground_wait_ms', 'core_ms', 'synthesis_wait_ms', 'total_ms', 'first_audio_ready_ms'):
            value = timings.get(key, receipt.get(key) if key == 'first_audio_ready_ms' else None)
            if type(value) in {int, float} and math.isfinite(value) and 0 <= value <= 600_000:
                row['backend_' + key] = round(value, 3)
        self._voice_requests.append(row)
        self._voice_outcomes[status] += 1

    def _record_voice_playback(self, output, event, receipt=None):
        identifier = output.get('id')
        if not isinstance(identifier, str) or not re.fullmatch(r'output-[a-f0-9]{32}', identifier):
            return
        row = {'output_id': identifier, 'event': event, 'observed_at': time.time()}
        request_id = output.get('request_id', output.get('utterance_id'))
        if isinstance(request_id, str) and re.fullmatch(r'discord-voice-[a-f0-9]{32}', request_id):
            row['request_id'] = request_id
        server_time = receipt.get(event + '_at') if isinstance(receipt, dict) else None
        if type(server_time) in {int, float} and math.isfinite(server_time):
            row['server_at'] = server_time
        self._voice_playback.append(row)

    async def start(self):
        async with self._lifecycle_lock:
            if self._closed:
                raise ValueError('Discord adapter is closed')
            if self._started:
                return
            await _call(self.interaction.connect, self.dm_consumer)
            self._started = True
            self._tasks = [asyncio.create_task(self._poll_loop()),
                           asyncio.create_task(self._poll_loop(voice=True)),
                           asyncio.create_task(self._audio_loop()),
                           asyncio.create_task(self._audio_loop()),
                           asyncio.create_task(self._expire_loop())]

    async def disconnected(self):
        async with self._lifecycle_lock:
            self._voice_epoch += 1
            self._dm = None
            self._started = False
            if self._controller is not None and self._controller._voice is not None:
                self._controller._voice.stop()
            await self._stop_tasks()
            try:
                for consumer in (self.dm_consumer, self.voice_consumer):
                    with contextlib.suppress(Exception):
                        await _call(self.interaction.disconnect, consumer)
            finally:
                if self._controller is not None:
                    await self._controller.handle(self.scope['owner_user_id'], 'leave')

    async def _stop_tasks(self):
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        while not self._audio_queue.empty():
            _epoch, item = self._audio_queue.get_nowait()
            item['_voice_dequeued_at'] = _voice_timing_clock()
            self._record_voice_request(item, {'status': 'cancelled', 'reason': 'voice_session_closed'})
            self._audio_queue.task_done()

    async def close(self):
        if self._closed:
            return
        self._closed = True
        await self.disconnected()

    def _pinned_message(self, message):
        try:
            channel, author = message.channel, message.author
            kind = getattr(channel, 'type', None)
            kind = getattr(kind, 'value', kind)
            return (getattr(message, 'guild', None) is None
                    and getattr(message, 'webhook_id', None) is None
                    and type(kind) is int and kind == 1
                    and admit_event(self.scope, user_id=_sdk_id(author.id),
                                    channel_id=_sdk_id(channel.id), is_bot=author.bot)
                    and bool(discord_id(_sdk_id(message.id))))
        except (AttributeError, ValueError):
            return False

    def _allowed_message(self, message):
        return self._pinned_message(message) and _private_dm(message.channel, self.scope['owner_user_id'])

    async def prepare_message(self, message):
        """Resolve SDK 2.7's unhydrated DM only after exact envelope admission."""
        if not self._pinned_message(message):
            return None
        if self._allowed_message(message):
            return message
        if self._dm_resolver is None:
            return None
        channel = await self._dm_resolver()
        if (not _private_dm(channel, self.scope['owner_user_id'])
                or _sdk_id(channel.id) != self.scope['dm_channel_id']):
            return None
        prepared = copy.copy(message)
        prepared.channel = channel
        return prepared

    async def on_message(self, message):
        message = await self.prepare_message(message) if self._started else None
        if message is None:
            self.stats['ignored'] += 1
            return {'ok': False, 'reason': 'private_owner_dm_required'}
        text = getattr(message, 'content', None)
        if not isinstance(text, str) or not text.strip() or len(text) > 8000:
            return {'ok': False, 'reason': 'invalid_content'}
        self._dm = message.channel
        if text.strip().startswith('!shana'):
            parts = text.strip().split()
            if len(parts) != 2 or parts[0] != '!shana':
                return {'ok': False, 'reason': 'unknown_command'}
            return await self.voice_command(self.scope['owner_user_id'], parts[1])
        observation = InteractionObservation(
            audience='owner_private', request_id='discord-dm-' + _sdk_id(message.id),
            session_id='discord-private', consumer_id=self.dm_consumer,
            text=text, channel='discord_dm', speaker='owner', address_cue='direct', confidence=1.0)
        result = await _call(self.interaction.observe, observation)
        await self.deliver_text_once()
        return result

    async def _current(self, output, consumer):
        now = _heartbeat_clock()
        if consumer not in self._heartbeats or now - self._heartbeats[consumer] >= 10:
            # Polling renews the existing lease. It never reconnects an expired
            # consumer and an offered later chunk remains available in order.
            await _call(self.interaction.next_output, consumer)
            self._heartbeats[consumer] = now
        state = await _call(self.interaction.status, consumer)
        current = next((row for row in state.get('outputs', []) if row.get('id') == output.get('id')), None)
        return (state.get('generation') == output.get('generation')
                and state.get('output_epoch') == output.get('output_epoch')
                and state.get('awake') is not False
                and state.get('consumer', {}).get('connected') is True
                and current is not None and current.get('status') in {'generated', 'offered', 'attempted', 'started'}
                and output.get('consumer_id') == consumer)

    async def _ack(self, output, consumer, status, receipt_id=None):
        receipt = await _call(self.interaction.acknowledge, consumer_id=consumer,
                              output_id=output['id'], generation=output['generation'],
                              output_epoch=output['output_epoch'], status=status,
                              receipt_id=receipt_id)
        if consumer == self.voice_consumer:
            self._record_voice_playback(output, status, receipt)
        return receipt

    async def deliver_text_once(self):
        async with self._text_lock:
            if not self._started:
                return
            payload = await _call(self.interaction.next_output, self.dm_consumer)
            self._heartbeats[self.dm_consumer] = _heartbeat_clock()
            channel = self._dm
            if channel is None or not _private_dm(channel, self.scope['owner_user_id']):
                return
            if _sdk_id(channel.id) != self.scope['dm_channel_id']:
                return
            output = payload.get('output')
            if not output or output.get('channel') != 'discord_dm':
                return
            text = output.get('display_text')
            if not isinstance(text, str) or not text.strip():
                return
            try:
                # Explicit no-mentions object is required, never SDK defaults.
                if self._allowed_mentions is None:
                    raise ValueError('Discord mention suppression is required')
                await self._ack(output, self.dm_consumer, 'attempted')
                for chunk in message_chunks(text):
                    if not await self._current(output, self.dm_consumer):
                        return
                    receipt = await channel.send(chunk, allowed_mentions=self._allowed_mentions)
                    receipt_id = _sdk_id(receipt.id)
                await self._ack(output, self.dm_consumer, 'delivered', receipt_id)
                self.stats['text_delivered'] += 1
            except asyncio.CancelledError:
                # An in-flight HTTP send can be uncertain. Never automatically replay.
                with contextlib.suppress(Exception):
                    await self._ack(output, self.dm_consumer, 'failed')
                raise
            except Exception:
                self.stats['failures'] += 1
                with contextlib.suppress(Exception):
                    await self._ack(output, self.dm_consumer, 'failed')

    def voice_room_private(self):
        try:
            channel = self.client.get_channel(int(self.scope['voice_channel_id']))
            if channel is None or _sdk_id(channel.guild.id) != self.scope['voice_guild_id']:
                return False
            bot_id = _sdk_id(self.client.user.id)
            if self._room_policy is not None and not self._room_policy(channel, self.scope['owner_user_id'], bot_id):
                return False
            members = {_sdk_id(member.id) for member in channel.members}
            return self.scope['owner_user_id'] in members and members <= {self.scope['owner_user_id'], bot_id}
        except (AttributeError, TypeError, ValueError):
            return False

    def _ensure_controller(self):
        if self._controller is not None:
            return
        if self.scope['voice_channel_id'] is None:
            raise ValueError('private Discord voice is not selected')
        if self._voice_factory is None:
            from ..integrations.discord.secure_receive import voice_client_class
            self._voice_factory = voice_client_class()
        if self._sink_factory is None:
            self._sink_factory = self._build_sink_type()
        self._controller = VoiceSessionController(
            self.client, guild_id=self.scope['voice_guild_id'],
            channel_id=self.scope['voice_channel_id'],
            owner_check=lambda value: type(value) is str and value == self.scope['owner_user_id'],
            voice_factory=self._voice_factory, sink_factory=self._sink_factory)

    async def voice_command(self, user_id, action):
        if type(user_id) is not str or user_id != self.scope['owner_user_id']:
            return {'ok': False, 'reason': 'owner_required'}
        if action not in {'join', 'leave', 'pause', 'resume', 'status'}:
            return {'ok': False, 'reason': 'unknown_command'}
        if action in {'join', 'resume'} and not self.voice_room_private():
            return {'ok': False, 'reason': 'private_owner_room_required'}
        if self.scope['voice_channel_id'] is None:
            return {'ok': False, 'reason': 'private_voice_not_selected'}
        self._ensure_controller()
        if action in {'leave', 'pause'}:
            self._voice_epoch += 1
            voice = self._controller._voice
            if voice is not None:
                voice.stop()
            await _call(self.interaction.disconnect, self.voice_consumer)
            while not self._audio_queue.empty():
                _epoch, item = self._audio_queue.get_nowait()
                item['_voice_dequeued_at'] = _voice_timing_clock()
                self._record_voice_request(item, {'status': 'cancelled', 'reason': 'voice_session_closed'})
                self._audio_queue.task_done()
        was_listening = self._controller._state == 'listening'
        result = await self._controller.handle(user_id, action)
        if result.get('ok') and result.get('state') == 'listening':
            if not self.voice_room_private():
                await self.voice_command(user_id, 'leave')
                return {'ok': False, 'reason': 'voice_audience_changed'}
            if not was_listening:
                await _call(self.interaction.connect, self.voice_consumer)
        return result

    async def on_voice_state_update(self, _member, before, after):
        selected = self.scope['voice_channel_id']
        if selected is None:
            return
        touched = {str(getattr(getattr(state, 'channel', None), 'id', '')) for state in (before, after)}
        if selected in touched and not self.voice_room_private() and self._controller is not None:
            await self.voice_command(self.scope['owner_user_id'], 'leave')

    def _build_sink_type(self):
        from discord.ext import voice_recv
        adapter = self

        class OwnerSink(voice_recv.AudioSink):
            def __init__(self):
                super().__init__()
                # Normalize before buffering: twelve seconds of mono 16 kHz PCM
                # stays below ingress limits without splitting ordinary questions.
                self.buffer = OwnerPCMBuffer(adapter.scope['owner_user_id'])
                self.epoch = adapter._voice_epoch
                self.closed = False
                adapter._sinks.add(self)

            def wants_opus(self):
                return False

            def emit(self, items):
                for item in items:
                    item = {**item, 'capture_released_monotonic': _voice_timing_clock()}
                    adapter.client.loop.call_soon_threadsafe(adapter._enqueue_audio, self, item)

            def write(self, user, data):
                if self.closed or user is None or getattr(user, 'bot', True):
                    return
                try:
                    if _sdk_id(user.id) != adapter.scope['owner_user_id']:
                        return
                except (AttributeError, ValueError):
                    return
                # Listening continues during playback. Shared transcript echo checks
                # and interruption policy decide whether the sound is a new turn.
                self.emit(self.buffer.push(adapter.scope['owner_user_id'], data.pcm, now=_voice_timing_clock()))

            @voice_recv.AudioSink.listener()
            def on_voice_member_speaking_stop(self, member):
                if member is not None and str(member.id) == adapter.scope['owner_user_id']:
                    # The SDK stops at 200ms; its event is only a hint. Keep the
                    # buffer's 600ms silence policy for ordinary clause pauses.
                    self.emit(self.buffer.expire(now=_voice_timing_clock()))

            def cleanup(self):
                self.closed = True
                self.buffer.clear()

        return OwnerSink

    def _enqueue_audio(self, sink, item):
        if (sink.closed or sink.epoch != self._voice_epoch or not self.voice_room_private()
                or self._controller is None or self._controller._state != 'listening'
                or not isinstance(item, dict) or item.get('user_id') != self.scope['owner_user_id']
                or not isinstance(item.get('wav'), bytes) or not 44 <= len(item['wav']) <= 800_000):
            return
        item = {**item, '_voice_request_id': 'discord-voice-' + uuid.uuid4().hex,
                '_voice_enqueued_at': _voice_timing_clock()}
        try:
            self._audio_queue.put_nowait((self._voice_epoch, item))
        except asyncio.QueueFull:
            self.stats['dropped'] += 1
            self._record_voice_request(item, {'status': 'dropped', 'reason': 'audio_queue_full'})

    async def _audio_loop(self):
        while True:
            epoch, item = await self._audio_queue.get()
            self._processing_utterances += 1
            try:
                item['_voice_dequeued_at'] = _voice_timing_clock()
                if epoch != self._voice_epoch or not self.voice_room_private():
                    self._record_voice_request(item, {'status': 'cancelled', 'reason': 'voice_epoch_or_privacy_changed'})
                    continue
                observation = InteractionObservation(
                    audience='owner_private', request_id=item['_voice_request_id'],
                    session_id='discord-private', consumer_id=self.voice_consumer,
                    channel='discord_voice', speaker='owner', address_cue='none', confidence=1.0)
                item['_voice_request_started_at'] = _voice_timing_clock()
                receipt = await _call(self.interaction.submit_audio, observation, item['wav'])
                self._record_voice_request(item, receipt)
            except asyncio.CancelledError:
                self._record_voice_request(item, {'status': 'cancelled', 'reason': 'transport_wait_cancelled'})
                raise
            except Exception:
                self.stats['failures'] += 1
                self._record_voice_request(item, {'status': 'failed', 'reason': 'transport_request_failed'})
            finally:
                self._processing_utterances -= 1
                self._audio_queue.task_done()

    async def _expire_loop(self):
        while True:
            await asyncio.sleep(.2)
            for sink in tuple(self._sinks):
                if sink.closed:
                    self._sinks.discard(sink)
                else:
                    sink.emit(sink.buffer.expire(now=_voice_timing_clock()))

    async def deliver_voice_once(self):
        async with self._voice_lock:
            if (self._controller is None or self._controller._state != 'listening'
                    or not self.voice_room_private()):
                return
            payload = await _call(self.interaction.next_output, self.voice_consumer)
            output = payload.get('output')
            if not output or output.get('channel') != 'discord_voice' or not output.get('audio_available'):
                return
            voice = self._controller._voice
            epoch = self._voice_epoch
            source = None
            try:
                audio, mime = await _call(self.interaction.audio, output['id'], self.voice_consumer,
                                          output['generation'], output['output_epoch'])
                if not await self._current(output, self.voice_consumer) or not self.voice_room_private():
                    return
                if self._audio_source_factory is None:
                    raise ValueError('Discord audio source factory is required')
                source = self._audio_source_factory(audio, mime)
                completed = asyncio.get_running_loop().create_future()
                loop = asyncio.get_running_loop()

                def after(error):
                    valid = epoch == self._voice_epoch
                    def finish():
                        if not completed.done():
                            completed.set_result((valid, error is None))
                    loop.call_soon_threadsafe(finish)

                if not self.voice_room_private() or not await self._current(output, self.voice_consumer):
                    return
                await self._ack(output, self.voice_consumer, 'attempted')
                voice.play(source, after=after)
                self._record_voice_playback(output, 'play_requested')
                await self._ack(output, self.voice_consumer, 'started')
                deadline = _monotonic() + _playback_timeout(output)
                while not completed.done():
                    if (epoch != self._voice_epoch or not self.voice_room_private()
                            or not await self._current(output, self.voice_consumer)):
                        _stop_playback(voice)
                        await self._ack(output, self.voice_consumer, 'cancelled')
                        return
                    if _monotonic() >= deadline:
                        raise TimeoutError('private Discord playback timed out')
                    await asyncio.wait({completed}, timeout=.05)
                valid, success = completed.result()
                if valid and success and self.voice_room_private() and await self._current(output, self.voice_consumer):
                    await self._ack(output, self.voice_consumer, 'played')
                    self.stats['audio_played'] += 1
                else:
                    await self._ack(output, self.voice_consumer, 'failed')
            except asyncio.CancelledError:
                with contextlib.suppress(Exception):
                    await self._ack(output, self.voice_consumer, 'cancelled')
                raise
            except Exception:
                self.stats['failures'] += 1
                with contextlib.suppress(Exception):
                    await self._ack(output, self.voice_consumer, 'failed')
            finally:
                _stop_playback(voice)
                if source is not None:
                    source.cleanup()

    async def _poll_loop(self, voice=False):
        while True:
            try:
                if voice:
                    await self.deliver_voice_once()
                else:
                    await self.deliver_text_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.stats['failures'] += 1
            await asyncio.sleep(.1)


def build_discord_client(interaction, *, owner_user_id, dm_channel_id,
                         voice_guild_id=None, voice_channel_id=None,
                         adapter_class=PrivateDiscordAdapter, room_policy=None):
    """Create unconnected SDK objects; caller must separately authorize login."""
    import discord

    intents = discord.Intents.none()
    intents.dm_messages = True
    if voice_channel_id is not None:
        intents.guilds = True
        intents.voice_states = True
    client = discord.Client(intents=intents)

    async def resolve_dm():
        return await client.create_dm(discord.Object(id=int(owner_user_id)))

    def audio_source(audio, mime):
        if mime not in {'audio/wav', 'audio/x-wav', 'audio/wave'} or not isinstance(audio, bytes) or len(audio) > 20 * 1024 * 1024:
            raise ValueError('unsupported private Discord audio')
        # discord.py FFmpegAudio uses CREATE_NO_WINDOW on Windows. No shell and
        # no file path supplied by a caller reaches the decoder.
        return discord.FFmpegPCMAudio(io.BytesIO(audio), pipe=True)

    adapter = adapter_class(client, interaction, owner_user_id=owner_user_id,
        dm_channel_id=dm_channel_id, voice_guild_id=voice_guild_id,
        voice_channel_id=voice_channel_id, allowed_mentions=discord.AllowedMentions.none(),
        audio_source_factory=audio_source, room_policy=room_policy, dm_resolver=resolve_dm)

    @client.event
    async def on_ready():
        await adapter.start()

    @client.event
    async def on_resumed():
        await adapter.start()

    @client.event
    async def on_disconnect():
        await adapter.disconnected()

    @client.event
    async def on_message(message):
        return await adapter.on_message(message)

    @client.event
    async def on_voice_state_update(member, before, after):
        await adapter.on_voice_state_update(member, before, after)

    return client, adapter
