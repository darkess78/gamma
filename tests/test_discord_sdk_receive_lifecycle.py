"""Installed SDK receive/player lifecycle, with network and model boundaries offline."""
import asyncio
from io import BytesIO
import struct
from types import SimpleNamespace as NS

import pytest

from gamma.coordination import discord as transport
from gamma.integrations.discord.secure_receive import voice_client_class
from test_coordination_discord_private import setup


class OfflineSocket:
    def sendto(self, data, address):
        return len(data)


class OfflineWebSocket:
    async def speak(self, _state):
        pass


class OfflineConnection:
    mode = 'aead_xchacha20_poly1305_rtpsize'
    secret_key = bytes(32)
    endpoint_ip = '127.0.0.1'
    voice_port = 1
    timeout = 1

    def __init__(self):
        self.socket = OfflineSocket()
        self.ws = OfflineWebSocket()
        self.listeners = []

    def is_connected(self):
        return True

    def wait(self, timeout=None):
        return True

    def add_socket_listener(self, callback):
        self.listeners.append(callback)

    def remove_socket_listener(self, callback):
        self.listeners.remove(callback)


async def until(predicate, message, seconds=2):
    deadline = asyncio.get_running_loop().time() + seconds
    while not predicate() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(.005)
    assert predicate(), message


async def sdk_session(*, long_first=False):
    discord = pytest.importorskip('discord')
    pytest.importorskip('discord.ext.voice_recv')
    from discord.ext.voice_recv import rtp

    source_count = [0]
    def source_factory(*_):
        source_count[0] += 1
        frames = 100 if long_first and source_count[0] == 1 else 2
        return discord.PCMAudio(BytesIO(struct.pack('<hh', 500, 800) * 960 * frames))

    adapter, service, _dm, room = setup(audio_source_factory=source_factory)
    loop = asyncio.get_running_loop()
    owner = NS(id=1, bot=False)
    room.guild.get_member = lambda user_id: owner if user_id == 1 else None
    adapter.client.loop = loop
    adapter.client.get_user = lambda user_id: owner if user_id == 1 else None
    adapter.client.dispatch = lambda *_args, **_kwargs: None

    # Construct the real SDK class without a gateway handshake. The actual
    # SecureAudioReader, Opus decoder/router, AudioPlayer, stop methods and
    # OwnerSink run. Only connection/socket sends and application inference
    # are replaced. Authored Opus enters at the already-decrypted router edge.
    voice = object.__new__(voice_client_class())
    voice.client, voice.channel, voice.loop = adapter.client, room, loop
    voice._connection = OfflineConnection()
    voice._reader = discord.utils.MISSING
    voice._player = None
    voice._event_listeners = {}
    voice._ssrc_to_id = {2468: 1}
    voice._id_to_ssrc = {1: 2468}
    voice.encoder = discord.utils.MISSING
    sent = []
    voice.send_audio_packet = lambda data, **kwargs: sent.append(len(data))
    adapter._ensure_controller()
    adapter._controller._voice, adapter._controller._state = voice, 'listening'
    sink = adapter._build_sink_type()()
    adapter._controller._sink = sink
    writes = [0]
    original_write = sink.write
    def write(user, data):
        writes[0] += 1
        original_write(user, data)
    sink.write = write
    voice.listen(sink)
    reader = voice._reader
    service.connect(adapter.voice_consumer)

    async def submit(observation, audio):
        service.observations.append(observation.model_dump())
        service.output(adapter.voice_consumer, 'discord_voice')
        return {'status': 'generated'}
    service.submit_audio = submit
    processor = asyncio.create_task(adapter._audio_loop())
    expirer = asyncio.create_task(adapter._expire_loop())
    sequence = [0]
    encoder = discord.opus.Encoder()

    async def utterance():
        assert voice.is_listening(), 'Playback cleanup shut down the SDK receiver'
        before = len(service.observations)
        written = writes[0]
        for _ in range(12):
            seq = sequence[0]; sequence[0] += 1
            opus = encoder.encode(struct.pack('<hh', 1000, 2000) * 960, 960)
            packet = rtp.decode_rtp(struct.pack('>BBHII', 0x80, 120, seq, seq * 960, 2468) + opus)
            packet.decrypted_data = opus
            reader.speaking_timer.notify(packet.ssrc)
            reader.packet_router.feed_rtp(packet)
        await until(lambda: writes[0] >= written + 8, 'Real SDK decoder did not reach OwnerSink')
        await until(lambda: len(service.observations) == before + 1, 'OwnerSink did not emit the next utterance')
        await asyncio.wait_for(adapter._audio_queue.join(), 2)

    async def close():
        processor.cancel(); expirer.cancel()
        await asyncio.gather(processor, expirer, return_exceptions=True)
        voice.stop()
        await until(lambda: sink.closed, 'Real reader did not clean up its sink')
        for thread in (reader.packet_router, reader.event_router, reader.speaking_timer):
            await asyncio.to_thread(thread.join, .6)

    return adapter, service, voice, reader, sink, sent, utterance, close


def test_real_sdk_receiver_survives_eof_and_two_follow_up_utterances():
    async def run():
        adapter, service, voice, reader, sink, sent, utterance, close = await sdk_session()
        try:
            for turn in range(3):
                await utterance()
                await adapter.deliver_voice_once()
                assert voice.is_listening(), 'Successful playback EOF stopped receiving audio'
                assert adapter.voice_timing_snapshot()['receive_active'] is True
                assert not sink.closed and reader.callback in voice._connection.listeners
                assert len(service.observations) == turn + 1
            assert adapter.stats['audio_played'] == 3 and sent
            assert [row['status'] for row in service.acks].count('played') == 3
        finally:
            await close()
    asyncio.run(run())


def test_real_sdk_receiver_survives_cancelled_playback_and_next_question():
    async def run():
        adapter, service, voice, reader, sink, sent, utterance, close = await sdk_session(long_first=True)
        try:
            await utterance()
            playback = asyncio.create_task(adapter.deliver_voice_once())
            await until(voice.is_playing, 'Actual SDK AudioPlayer did not start')
            service.epoch += 1  # An admitted newer turn invalidates the old output.
            await asyncio.wait_for(playback, 2)
            assert service.acks[-1]['status'] == 'cancelled'
            assert voice.is_listening(), 'Cancelling one output stopped the receiver'
            assert adapter.voice_timing_snapshot()['receive_active'] is True
            assert not sink.closed and reader.callback in voice._connection.listeners
            await utterance()
            await adapter.deliver_voice_once()
            assert len(service.observations) == 2 and adapter.stats['audio_played'] == 1
        finally:
            await close()
    asyncio.run(run())


def test_receive_diagnostic_uses_real_sdk_and_intentional_pause_stops_it():
    async def run():
        adapter, service, voice, reader, sink, sent, utterance, close = await sdk_session()
        try:
            assert adapter.voice_timing_snapshot()['receive_active'] is True
            result = await adapter.voice_command('1', 'pause')
            assert result['state'] == 'paused'
            assert adapter.voice_timing_snapshot()['receive_active'] is False
            await until(lambda: sink.closed, 'Pause must clean up the receive sink')
            assert not voice._connection.listeners
        finally:
            await close()
    asyncio.run(run())


def test_receive_diagnostic_is_unknown_for_absent_or_fake_receiver():
    adapter, _service, _dm, _room = setup()
    assert adapter.voice_timing_snapshot()['receive_active'] is None
    adapter._controller = NS(_voice=NS(is_listening=lambda: 'SECRET'))
    assert adapter.voice_timing_snapshot()['receive_active'] is None
