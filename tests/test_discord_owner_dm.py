from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from gamma.integrations.discord.runtime import DiscordRuntimeConfig
from gamma.integrations.discord.session import DiscordSession
from gamma.presence import apply_presence_to_stream_event
from gamma.stream.brain import StreamBrain, _is_owner_dm_event
from gamma.stream.models import StreamActor, StreamInputEvent

OWNER = "111"


class _Resolver:
    def resolve(self, speaker):
        return SimpleNamespace(is_owner=speaker.source == "discord" and speaker.platform_id == OWNER)


@pytest.fixture(autouse=True)
def _owner(monkeypatch):
    monkeypatch.setattr("gamma.identity.IdentityResolver", _Resolver)


def _dm(uid=OWNER, **metadata):
    return StreamInputEvent(
        kind="chat_message", text="hey, it's me", session_id=f"discord:dm:{uid}",
        actor=StreamActor(source="discord", platform_id=uid, display_name="Neety"),
        metadata={"discord_dm": True, "output_target_policy": "dashboard_monitor", "guild_id": "", **metadata},
    )


def test_only_a_verified_owner_dm_is_private():
    recipient = StreamBrain._recipient_for_event(_dm(), ingress_verified=True)
    assert recipient.kind == "owner_private" and recipient.allows_private_memory

    # A request body that only claims to be the owner's DM (no ingress secret) is not private.
    assert not StreamBrain._recipient_for_event(_dm(), ingress_verified=False).allows_private_memory
    # Someone else's DM, or a "DM" that names a guild, is not private either.
    assert not _is_owner_dm_event(_dm(uid="222"), ingress_verified=True)
    assert not _is_owner_dm_event(_dm(guild_id="100"), ingress_verified=True)
    # A normal server message from the owner stays shared.
    server = StreamInputEvent(kind="chat_message", text="hi", actor=StreamActor(source="discord", platform_id=OWNER),
                              metadata={"output_target_policy": "discord_call", "guild_id": "100"})
    assert StreamBrain._recipient_for_event(server, ingress_verified=True).kind == "shared"


def _message(uid, *, guild=None):
    return SimpleNamespace(content="hello", author=SimpleNamespace(id=int(uid), bot=False),
                           guild=SimpleNamespace(id=guild) if guild else None, channel=SimpleNamespace(id=999))


def test_session_reads_dms_from_the_owner_only():
    ingested = []

    async def ingest(message):
        ingested.append(message.author.id)
        return None

    async def send(message, text):
        pass

    def session(enabled):
        return DiscordSession(guild_id="100", text_channel_id="200", owner_user_id=OWNER, reply_enabled=True,
                              ingest=ingest, send=send, owner_dm_enabled=enabled)

    async def run():
        assert (await session(True).handle_message(_message(OWNER)))["ok"]
        assert (await session(True).handle_message(_message("222")))["ignored"]
        assert (await session(False).handle_message(_message(OWNER)))["ignored"]

    asyncio.run(run())
    assert ingested == [int(OWNER)]


def test_presence_keeps_an_owner_dm_private_and_respects_sleep(monkeypatch):
    config = DiscordRuntimeConfig(enabled=True, output_enabled=True, guild_id="100", text_channel_id="200",
                                  owner_user_id=OWNER)
    monkeypatch.setattr(DiscordRuntimeConfig, "from_app_config", classmethod(lambda cls, config_=None: config))
    awake, speak = apply_presence_to_stream_event(_dm(), synthesize_speech=True, state={"mode": "wake"})
    assert awake.metadata["output_target_policy"] == "dashboard_monitor"
    assert awake.metadata["public_output"] is False and awake.metadata["presence_suppressed"] is False
    assert speak is False
    asleep, _ = apply_presence_to_stream_event(_dm(), synthesize_speech=False, state={"mode": "sleep"})
    assert asleep.metadata["presence_suppressed"] is True


def test_config_reads_the_owner_dm_switch():
    assert DiscordRuntimeConfig.from_app_config({}).owner_dm_enabled is True
    assert DiscordRuntimeConfig.from_app_config({"discord": {"owner_dm_enabled": False}}).owner_dm_enabled is False
