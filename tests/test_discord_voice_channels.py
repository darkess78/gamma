from __future__ import annotations

import asyncio
from types import SimpleNamespace

from gamma.integrations.discord.runtime import DiscordRuntimeConfig
from gamma.integrations.discord.voice_controls import VoiceSessionController


class _Voice:
    def __init__(self, channel):
        self.channel = channel
        self.connected = True
        self.moves = []

    def is_connected(self):
        return self.connected

    def listen(self, sink):
        pass

    def stop_listening(self):
        pass

    async def move_to(self, channel):
        self.moves.append(channel.id)
        self.channel = channel

    async def disconnect(self, *, force):
        self.connected = False


class _Channel:
    def __init__(self, channel_id, guild_id=100):
        self.id = channel_id
        self.guild = SimpleNamespace(id=guild_id)
        self.joins = 0

    async def connect(self, *, cls, self_deaf):
        self.joins += 1
        return _Voice(self)


def _controller(channels, allowed=("400", "500")):
    client = SimpleNamespace(get_channel=lambda cid: channels.get(cid))
    return VoiceSessionController(client, guild_id="100", channel_id="300", owner_check=lambda uid: uid == "1",
                                  voice_factory=None, sink_factory=lambda: object(), allowed_channel_ids=allowed)


def test_join_goes_to_the_owners_allowed_channel_and_falls_back_to_main():
    channels = {cid: _Channel(cid) for cid in (300, 400, 500, 999)}

    async def run():
        ctl = _controller(channels)
        assert (await ctl.handle("1", "join", channel_id=400))["ok"]
        assert ctl.active_channel_id == 400 and channels[400].joins == 1
        # Already connected: a join from another allowed channel moves her instead of reconnecting.
        assert (await ctl.handle("1", "join", channel_id=500))["ok"]
        assert ctl.active_channel_id == 500 and ctl._voice.moves == [500]
        await ctl.handle("1", "leave")
        # A channel outside the allowed list falls back to the main one.
        assert (await ctl.handle("1", "join", channel_id=999))["ok"]
        assert ctl.active_channel_id == 300 and channels[999].joins == 0
        # Strangers still cannot move her.
        assert (await ctl.handle("2", "join", channel_id=400))["reason"] == "owner_required"

    asyncio.run(run())


def test_config_reads_extra_voice_channels():
    cfg = DiscordRuntimeConfig.from_app_config(
        {"discord": {"voice_channel_id": "300", "voice_channel_ids": ["400", "500"], "text_channel_id": "10"}}
    )
    assert cfg.voice_channel_ids == {"300", "400", "500"}
