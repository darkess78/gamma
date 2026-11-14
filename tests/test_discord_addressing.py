"""Discord: @mentions and replies to Shana count as talking to her; more than one channel."""
from types import SimpleNamespace

from gamma.integrations.discord.runtime import DiscordRuntimeConfig
from gamma.integrations.discord.worker import addressed_content

BOT = "999"


def msg(content, *, mentions=(), replied_to=None):
    reference = SimpleNamespace(resolved=SimpleNamespace(author=SimpleNamespace(id=int(replied_to)))) if replied_to else None
    return SimpleNamespace(content=content, mentions=list(mentions), reference=reference)


def test_mentioning_the_bot_uses_her_name():
    assert addressed_content(msg("<@999> what's up"), BOT) == "Shana what's up"
    assert addressed_content(msg("<@!999> hi"), BOT) == "Shana hi"


def test_other_mentions_become_display_names():
    friend = SimpleNamespace(id=42, display_name="Mo")
    assert addressed_content(msg("<@42> is here", mentions=[friend]), BOT) == "@Mo is here"


def test_replying_to_shana_addresses_her():
    assert addressed_content(msg("why though", replied_to=BOT), BOT) == "Shana, why though"
    assert addressed_content(msg("ok shana", replied_to=BOT), BOT) == "ok shana"
    assert addressed_content(msg("why though", replied_to="123"), BOT) == "why though"


def test_extra_text_channels_from_config():
    config = DiscordRuntimeConfig.from_app_config({"discord_text_channel_id": "200", "discord_text_channel_ids": "300, 400,x"})
    assert config.text_channel_ids == {"200", "300", "400"}
