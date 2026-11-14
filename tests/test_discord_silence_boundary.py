"""SDK speaking-stop hints must respect the existing bounded silence policy."""
import asyncio
from io import BytesIO
import struct
from types import SimpleNamespace as NS
import wave

import pytest

from gamma.coordination import discord as transport
from gamma.coordination.discord_capture import OwnerPCMBuffer
from test_coordination_discord_private import setup


def read_wave(item):
    with wave.open(BytesIO(item['wav']), 'rb') as stream:
        assert (stream.getframerate(), stream.getnchannels(), stream.getsampwidth()) == (16000, 1, 2)
        assert stream.getnframes() <= 12 * 16000
        assert len(item['wav']) <= 800_000
        return stream.readframes(stream.getnframes())


async def capture_case(monkeypatch, gap):
    voice_recv = pytest.importorskip('discord.ext.voice_recv')
    from discord.ext.voice_recv.router import SinkEventRouter

    clock = [100.0]
    monkeypatch.setattr(transport, '_voice_timing_clock', lambda: clock[0])
    adapter, _service, _dm, _room = setup()
    adapter.client.loop = asyncio.get_running_loop()
    adapter._controller = NS(_state='listening')
    sink = adapter._build_sink_type()()
    events = SinkEventRouter(sink, NS())
    owner = NS(id=1, bot=False)
    expected = OwnerPCMBuffer('1')
    first, second = struct.pack('<hh', 1000, 2000) * 960, struct.pack('<hh', 4000, 5000) * 960
    try:
        for packet in range(50):
            clock[0] = 100 + packet * .02
            sink.write(owner, voice_recv.VoiceData(NS(decrypted_data=b''), owner, pcm=first))
            expected.push('1', first, now=clock[0])
        last = clock[0]
        clock[0] = last + .2  # Installed SDK's actual speaking_timeout_delay.
        events._dispatch_to_listeners('voice_member_speaking_stop', owner)
        await asyncio.sleep(0)
        assert adapter._audio_queue.empty(), 'The 200ms SDK hint prematurely finalized a clause'
        for packet in range(50):
            clock[0] = last + gap + packet * .02
            sink.write(owner, voice_recv.VoiceData(NS(decrypted_data=b''), owner, pcm=second))
            expected.push('1', second, now=clock[0])
        last = clock[0]
        clock[0] = last + .2
        events._dispatch_to_listeners('voice_member_speaking_stop', owner)
        clock[0] = last + .599
        sink.emit(sink.buffer.expire(now=clock[0]))
        await asyncio.sleep(0)
        assert adapter._audio_queue.empty()
        clock[0] = last + .601
        sink.emit(sink.buffer.expire(now=clock[0]))
        await asyncio.sleep(0)
        assert adapter._audio_queue.qsize() == 1
        _, item = adapter._audio_queue.get_nowait()
        adapter._audio_queue.task_done()
        reference = expected.expire(now=clock[0])
        assert len(reference) == 1
        assert read_wave(item) == read_wave(reference[0])
        assert len(read_wave(item)) == 100 * 320 * 2
        assert item['capture_reason'] == 'silence_timeout'
        sink.emit(sink.buffer.expire(now=clock[0] + 1))
        await asyncio.sleep(0)
        assert adapter._audio_queue.empty(), 'A later expiry emitted the same utterance twice'
    finally:
        sink.cleanup()


@pytest.mark.parametrize('gap', [.3, .5])
def test_installed_sdk_stop_then_next_clause_coalesces_all_pcm(monkeypatch, gap):
    asyncio.run(capture_case(monkeypatch, gap))


def test_isolated_question_endpoint_is_600ms_instead_of_sdk_200ms(monkeypatch):
    async def run():
        voice_recv = pytest.importorskip('discord.ext.voice_recv')
        from discord.ext.voice_recv.router import SinkEventRouter

        clock = [0.0]
        monkeypatch.setattr(transport, '_voice_timing_clock', lambda: clock[0])
        adapter, _service, _dm, _room = setup()
        adapter.client.loop = asyncio.get_running_loop()
        adapter._controller = NS(_state='listening')
        sink = adapter._build_sink_type()()
        owner = NS(id=1, bot=False)
        events = SinkEventRouter(sink, NS())
        try:
            sink.write(owner, voice_recv.VoiceData(NS(decrypted_data=b''), owner,
                       pcm=struct.pack('<hh', 1000, 2000) * 960))
            clock[0] = .2
            events._dispatch_to_listeners('voice_member_speaking_stop', owner)
            await asyncio.sleep(0)
            assert adapter._audio_queue.empty()
            clock[0] = .6
            sink.emit(sink.buffer.expire(now=clock[0]))
            await asyncio.sleep(0)
            assert adapter._audio_queue.qsize() == 1
            _, item = adapter._audio_queue.get_nowait()
            adapter._audio_queue.task_done()
            endpoint = item['capture_released_monotonic'] - item['capture_ended_monotonic']
            assert endpoint == pytest.approx(.6)
            assert endpoint - .2 == pytest.approx(.4)
            assert item['capture_reason'] == 'silence_timeout'
            read_wave(item)
        finally:
            sink.cleanup()
    asyncio.run(run())
