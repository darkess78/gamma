"""Owner-authorized Discord voice session lifecycle for Shana."""
import asyncio

__all__ = ["VoiceSessionController"]


class VoiceSessionController:
    _ALLOWED = frozenset({"join", "leave", "pause", "resume", "status"})

    def __init__(self, client, *, guild_id, channel_id, owner_check, voice_factory, sink_factory):
        self._client = client
        self._guild_id = self._validate_id(guild_id)
        self._channel_id = self._validate_id(channel_id)
        self._owner_check = owner_check
        self._voice_factory = voice_factory
        self._sink_factory = sink_factory
        self._lock = asyncio.Lock()
        self._voice = None
        self._sink = None
        self._state = "disconnected"

    @staticmethod
    def _validate_id(value):
        if not isinstance(value, str):
            raise ValueError("id must be a positive decimal string")
        digits = value.strip()
        if not digits or not digits.isdigit() or int(digits) <= 0:
            raise ValueError("id must be a positive decimal string")
        return int(digits)

    def _result(self, ok, reason=None):
        return {"ok": ok, "state": self._state, "reason": reason}

    def _is_owner(self, user_id):
        try:
            return self._owner_check(user_id) is True
        except Exception:
            return False

    def _channel(self):
        try:
            return self._client.get_channel(self._channel_id)
        except Exception:
            return None

    def _reset(self):
        self._sink = None
        self._voice = None
        self._state = "disconnected"

    def _retain_error(self):
        self._sink = None
        self._state = "error"

    async def _reconcile(self):
        if self._voice is not None and not self._voice.is_connected():
            self._reset()

    async def _handle(self, user_id, command):
        if not self._is_owner(user_id):
            return self._result(False, "owner_required")
        if command not in self._ALLOWED:
            return self._result(False, "unknown_command")
        await self._reconcile()
        if command == "join":
            return await self._join()
        if command == "leave":
            return await self._leave()
        if command == "pause":
            return await self._pause()
        if command == "resume":
            return await self._resume()
        return self._result(True)

    async def handle(self, user_id, command):
        async with self._lock:
            return await self._handle(user_id, command)

    async def _disconnect_voice(self, voice):
        if voice is None or not voice.is_connected():
            self._reset()
            return
        try:
            voice.stop_listening()
        except Exception:
            pass
        await voice.disconnect(force=True)
        self._reset()

    async def _join(self):
        if self._state == "error" and self._voice is not None:
            return self._result(False, "voice_error")
        if self._state == "listening":
            return self._result(True)
        if self._state == "paused":
            return await self._resume()
        channel = self._channel()
        if channel is None:
            self._reset()
            return self._result(False, "channel_unavailable")
        if getattr(getattr(channel, "guild", None), "id", None) != self._guild_id:
            self._reset()
            return self._result(False, "channel_not_allowed")
        try:
            voice = await channel.connect(cls=self._voice_factory, self_deaf=False)
        except Exception:
            self._reset()
            return self._result(False, "voice_error")
        self._voice = voice
        self._state = "paused"
        try:
            self._sink = self._sink_factory()
            voice.listen(self._sink)
        except asyncio.CancelledError:
            try:
                await self._disconnect_voice(voice)
            except Exception:
                self._retain_error()
            raise
        except Exception:
            try:
                await self._disconnect_voice(voice)
            except Exception:
                self._retain_error()
            return self._result(False, "voice_error")
        self._state = "listening"
        return self._result(True)

    async def _leave(self):
        if self._state == "disconnected" and self._voice is None:
            return self._result(True)
        try:
            await self._disconnect_voice(self._voice)
        except Exception:
            self._retain_error()
            return self._result(False, "voice_error")
        return self._result(True)

    async def _pause(self):
        if self._state == "paused":
            return self._result(True)
        voice = self._voice
        if voice is None or not voice.is_connected():
            self._reset()
            return self._result(False, "not_connected")
        try:
            voice.stop_listening()
        except Exception:
            return self._result(False, "voice_error")
        self._sink = None
        self._state = "paused"
        return self._result(True)

    async def _resume(self):
        if self._state == "listening":
            return self._result(True)
        voice = self._voice
        if voice is None or not voice.is_connected():
            self._reset()
            return self._result(False, "not_connected")
        try:
            sink = self._sink_factory()
            voice.listen(sink)
        except asyncio.CancelledError:
            try:
                voice.stop_listening()
            except Exception:
                pass
            try:
                await voice.disconnect(force=True)
            except Exception:
                pass
            self._retain_error()
            raise
        except Exception:
            try:
                voice.stop_listening()
            except Exception:
                pass
            try:
                await voice.disconnect(force=True)
            except Exception:
                pass
            self._retain_error()
            return self._result(False, "voice_error")
        self._sink = sink
        self._state = "listening"
        return self._result(True)
