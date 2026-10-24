from unittest.mock import patch

import pytest

from gamma.integrations.discord.runtime import DiscordRuntimeConfig
from gamma.presence import apply_presence_to_stream_event, presence_state_for_mode
from gamma.stream.models import StreamActor, StreamInputEvent


@pytest.mark.parametrize("mode,allowed", [("sleep",False),("wake",True),("go_live",True),("break",False)])
@pytest.mark.parametrize("kind", ["chat_message","mic_transcript"])
def test_discord_modes_preserve_isolated_target(mode,allowed,kind):
    config=DiscordRuntimeConfig(enabled=True,output_enabled=True,voice_enabled=True,guild_id="100",text_channel_id="200",voice_channel_id="300")
    event=StreamInputEvent(kind=kind,text="Shana hello",actor=StreamActor(source="discord",platform_id="1"),metadata={"guild_id":"100","channel_id":"300" if kind=="mic_transcript" else "200"})
    with patch.object(DiscordRuntimeConfig,"from_app_config",return_value=config):
        result,speech=apply_presence_to_stream_event(event,synthesize_speech=True,state=presence_state_for_mode(mode))
    assert result.metadata["output_target_policy"]=="discord_call"
    assert result.metadata["public_output"] is True
    assert result.metadata["presence_suppressed"] is (not allowed)
    assert speech is allowed


@pytest.mark.parametrize("guild,channel", [("wrong","200"),("100","wrong"),(None,"200")])
def test_unconfigured_discord_destination_is_suppressed(guild,channel):
    config=DiscordRuntimeConfig(enabled=True,output_enabled=True,guild_id="100",text_channel_id="200")
    event=StreamInputEvent(kind="chat_message",text="hello",actor=StreamActor(source="discord",platform_id="1"),metadata={"guild_id":guild,"channel_id":channel})
    with patch.object(DiscordRuntimeConfig,"from_app_config",return_value=config):
        result,speech=apply_presence_to_stream_event(event,synthesize_speech=True,state=presence_state_for_mode("wake"))
    assert result.metadata["presence_suppressed"] is True and speech is False
