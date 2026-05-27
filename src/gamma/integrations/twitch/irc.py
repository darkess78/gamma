from __future__ import annotations

from pydantic import BaseModel, Field

from .models import TwitchChatMessage


class TwitchIrcMessage(BaseModel):
    tags: dict[str, str] = Field(default_factory=dict)
    prefix: str | None = None
    command: str
    params: list[str] = Field(default_factory=list)

    @property
    def trailing(self) -> str | None:
        return self.params[-1] if self.params else None


def parse_irc_line(line: str) -> TwitchIrcMessage:
    rest = line.rstrip("\r\n")
    tags: dict[str, str] = {}
    prefix = None
    if rest.startswith("@"):
        raw_tags, _, rest = rest.partition(" ")
        tags = _parse_tags(raw_tags[1:])
    if rest.startswith(":"):
        prefix, _, rest = rest.partition(" ")
        prefix = prefix[1:] or None
    command, _, rest = rest.partition(" ")
    params: list[str] = []
    while rest:
        if rest.startswith(":"):
            params.append(rest[1:])
            break
        param, _, rest = rest.partition(" ")
        if param:
            params.append(param)
        rest = rest.lstrip()
    return TwitchIrcMessage(tags=tags, prefix=prefix, command=command.upper(), params=params)


def chat_message_from_irc(message: TwitchIrcMessage) -> TwitchChatMessage | None:
    if message.command != "PRIVMSG" or len(message.params) < 2:
        return None
    return TwitchChatMessage(
        text=message.params[-1],
        platform_user_id=message.tags.get("user-id") or None,
        display_name=message.tags.get("display-name") or _display_name_from_prefix(message.prefix),
        message_id=message.tags.get("id") or None,
        badges=_parse_badges(message.tags.get("badges", "")),
        tags=dict(message.tags),
    )


def _parse_tags(raw_tags: str) -> dict[str, str]:
    tags: dict[str, str] = {}
    for item in raw_tags.split(";"):
        if not item:
            continue
        key, separator, value = item.partition("=")
        tags[key] = _unescape_tag_value(value) if separator else ""
    return tags


def _parse_badges(raw_badges: str) -> dict[str, str]:
    badges: dict[str, str] = {}
    for item in raw_badges.split(","):
        if not item:
            continue
        name, _, version = item.partition("/")
        badges[name] = version
    return badges


def _unescape_tag_value(value: str) -> str:
    return (
        value.replace(r"\s", " ")
        .replace(r"\:", ";")
        .replace(r"\\", "\\")
        .replace(r"\r", "\r")
        .replace(r"\n", "\n")
    )


def _display_name_from_prefix(prefix: str | None) -> str | None:
    if not prefix:
        return None
    user, _, _host = prefix.partition("!")
    return user or None

