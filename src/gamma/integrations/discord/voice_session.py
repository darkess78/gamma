"""Bounded Shana voice conversations using the existing STT/stream/TTS API."""
from __future__ import annotations

import asyncio
import contextlib
import io
from pathlib import Path
import time

from ...config import settings
from .adapter import DiscordVoiceUtterance
from .audio_buffer import PCMUtteranceBuffer
from .delivery import approved_reply
from .secure_receive import voice_client_class
from .voice_controls import VoiceSessionController


class DiscordVoiceSession:
    def __init__(self, client, config, runtime, stream_client, *, logger):
        from discord.ext import voice_recv

        self.client, self.config = client, config
        self.runtime, self.stream_client = runtime, stream_client
        self.logger = logger
        self.queue = asyncio.Queue(maxsize=4)
        self.processor = None
        self.expirer = None
        self.sinks = set()
        self.generation = 0
        self.stats = {"utterances": 0, "dropped": 0, "replies": 0, "failures": 0}
        session = self

        class UtteranceSink(voice_recv.AudioSink):
            def __init__(self):
                super().__init__()
                self.buffer = PCMUtteranceBuffer()
                self.closed = False
                self.generation = session.generation
                session.sinks.add(self)

            def wants_opus(self):
                return False

            def _emit(self, items):
                if not self.closed:
                    for item in items:
                        session.client.loop.call_soon_threadsafe(session._enqueue, self, item)

            def write(self, user, data):
                if self.closed or user is None or getattr(user, "bot", False):
                    return
                voice = session.controller._voice
                if voice is None or voice.is_playing():
                    return
                self._emit(self.buffer.push(str(user.id), data.pcm, now=time.monotonic()))

            @voice_recv.AudioSink.listener()
            def on_voice_member_speaking_stop(self, member):
                if member is not None:
                    item = self.buffer.flush(str(member.id))
                    if item is not None:
                        self._emit([item])

            def cleanup(self):
                self.closed = True
                self.buffer.clear()

        self.sink_type = UtteranceSink
        self.controller = VoiceSessionController(
            client, guild_id=config.guild_id, channel_id=config.voice_channel_id,
            owner_check=lambda uid: str(uid) == config.owner_user_id,
            voice_factory=voice_client_class(), sink_factory=UtteranceSink,
        )

    def _enqueue(self, sink, item):
        voice = self.controller._voice
        if sink.closed or sink.generation != self.generation or voice is None or voice.is_playing():
            return
        if len(item.get("wav", b"")) < 48000:
            return
        try:
            self.queue.put_nowait((self.generation, item))
        except asyncio.QueueFull:
            self.stats["dropped"] += 1

    async def command(self, uid, command):
        if str(uid) != self.config.owner_user_id:
            return {"ok": False, "reason": "owner_required"}
        if command in {"leave", "pause"}:
            self.generation += 1
            voice = self.controller._voice
            if voice is not None:
                voice.stop()
            while not self.queue.empty():
                self.queue.get_nowait()
                self.queue.task_done()
        result = await self.controller.handle(str(uid), command)
        if result.get("ok") and result.get("state") == "listening":
            self._ensure_tasks()
        return result

    def _ensure_tasks(self):
        if self.processor is None or self.processor.done():
            self.processor = asyncio.create_task(self._process(), name="shana-discord-voice")
        if self.expirer is None or self.expirer.done():
            self.expirer = asyncio.create_task(self._expire(), name="shana-discord-silence")

    async def _expire(self):
        while True:
            await asyncio.sleep(0.2)
            for sink in tuple(self.sinks):
                if sink.closed:
                    self.sinks.discard(sink)
                else:
                    sink._emit(sink.buffer.expire(now=time.monotonic()))

    def _transcribe(self, wav):
        import httpx

        headers = {}
        if settings.api_auth_enabled and self.stream_client.bearer_token:
            headers["Authorization"] = f"Bearer {self.stream_client.bearer_token}"
        response = httpx.post(
            self.stream_client.base_url + "/v1/voice/transcribe", headers=headers,
            files={"audio_file": ("discord.wav", io.BytesIO(wav), "audio/wav")},
            timeout=httpx.Timeout(45, connect=5),
        )
        response.raise_for_status()
        payload = response.json()
        text = payload.get("transcript") if isinstance(payload, dict) else None
        return text.strip() if isinstance(text, str) and len(text) <= 16000 else ""

    async def _process(self):
        while True:
            generation, item = await self.queue.get()
            try:
                if generation != self.generation:
                    continue
                text = await asyncio.to_thread(self._transcribe, item["wav"])
                if not text or generation != self.generation:
                    continue
                guild = self.client.get_guild(int(self.config.guild_id))
                member = guild.get_member(int(item["user_id"])) if guild else None
                event = self.runtime.normalize_voice(DiscordVoiceUtterance(
                    transcript=text, user_id=item["user_id"],
                    display_name=getattr(member, "display_name", None),
                    guild_id=self.config.guild_id, channel_id=self.config.voice_channel_id,
                    metadata={"output_target_policy": "discord_call", "public_output": True},
                ))
                self.stats["utterances"] += 1
                result = await asyncio.to_thread(
                    self.stream_client.post_event, event, synthesize_speech=True, fast_mode=True,
                )
                if generation == self.generation and approved_reply(result):
                    response = result.get("assistant_response", {})
                    if response.get("delivery_mode") == "speech":
                        await self._play(response.get("audio_path"), generation)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.stats["failures"] += 1
                self.logger.warning("Shana Discord voice turn failed; no reply delivered")
            finally:
                self.queue.task_done()

    async def _play(self, audio_path, generation):
        import discord

        if not isinstance(audio_path, str) or not audio_path:
            return
        candidate = Path(audio_path)
        if candidate.is_symlink():
            return
        path = candidate.resolve()
        if path.parent != settings.audio_output_dir.resolve() or not path.is_file():
            return
        if path.stat().st_size > 20 * 1024 * 1024:
            return
        voice = self.controller._voice
        if voice is None or not voice.is_connected() or generation != self.generation or voice.is_playing():
            return
        completed = asyncio.get_running_loop().create_future()
        loop = asyncio.get_running_loop()

        def after(error):
            # A stop may invalidate playback before the waiting coroutine resumes.
            generation_valid = generation == self.generation

            def finish():
                if not completed.done():
                    if error:
                        completed.set_exception(RuntimeError("Discord playback failed"))
                    else:
                        completed.set_result(generation_valid)
            loop.call_soon_threadsafe(finish)

        # discord.py's FFmpegAudio launcher sets CREATE_NO_WINDOW on Windows.
        source = discord.FFmpegPCMAudio(str(path))
        try:
            voice.play(source, after=after)
            if await asyncio.wait_for(completed, timeout=60):
                self.stats["replies"] += 1
        finally:
            voice.stop()
            source.cleanup()

    async def close(self):
        self.generation += 1
        for task in (self.processor, self.expirer):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        await self.controller.handle(self.config.owner_user_id, "leave")
