"""Bounded Discord conversation session with failclosed allowlisting."""

import asyncio

COMMAND_PREFIX = "!shana"
SUPPORTED_COMMANDS = {"join", "leave", "pause", "resume", "status"}


def _positive_decimal(value):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return False
    return number > 0


def _id_text(value):
    if value is None:
        return None
    try:
        return str(value)
    except Exception:
        return None


def _command_action(text):
    if not text:
        return None
    stripped = text.strip()
    if not stripped.startswith(COMMAND_PREFIX):
        return None
    remainder = stripped[len(COMMAND_PREFIX):]
    if not remainder or not remainder[0].isspace():
        return None
    parts = remainder.split()
    if len(parts) != 1:
        return None
    action = parts[0]
    if action not in SUPPORTED_COMMANDS:
        return None
    return action


class DiscordSession:
    def __init__(
        self,
        guild_id,
        text_channel_id,
        owner_user_id,
        reply_enabled,
        ingest,
        send,
        approved_reply=None,
        message_chunks=None,
        voice_command=None,
    ):
        if not _positive_decimal(guild_id):
            raise ValueError("guild_id must be a positive decimal identifier")
        if not _positive_decimal(text_channel_id):
            raise ValueError("text_channel_id must be a positive decimal identifier")
        if not _positive_decimal(owner_user_id):
            raise ValueError("owner_user_id must be a positive decimal identifier")

        self._guild_id = _id_text(guild_id)
        self._channel_id = _id_text(text_channel_id)
        self._owner_id = _id_text(owner_user_id)
        self._reply_enabled = bool(reply_enabled)
        self._ingest = ingest
        self._send = send
        self._approved_reply = approved_reply or (lambda result: result)
        self._message_chunks = message_chunks or (lambda text: [text])
        self._voice_command = voice_command
        self._busy = False

    def _allowed(self, message):
        try:
            author = getattr(message, "author", None)
            if author is None or bool(getattr(author, "bot", False)):
                return False
            text = getattr(message, "content", None)
            if text is None or not str(text).strip():
                return False
            guild = getattr(message, "guild", None)
            guild_id = getattr(guild, "id", None) if guild else None
            if _id_text(guild_id) != self._guild_id:
                return False
            channel = getattr(message, "channel", None)
            channel_id = getattr(channel, "id", None) if channel else None
            if _id_text(channel_id) != self._channel_id:
                return False
            return _id_text(getattr(author, "id", None)) is not None
        except Exception:
            return False

    def _is_owner(self, message):
        try:
            author = getattr(message, "author", None)
            return author is not None and _id_text(getattr(author, "id", None)) == self._owner_id
        except Exception:
            return False

    async def _deliver(self, message, text_reply):
        delivered = 0
        try:
            chunks = list(self._message_chunks(text_reply))
        except Exception:
            return {"ok": False, "reason": "delivery_error"}
        for chunk in chunks:
            try:
                await self._send(message, chunk)
            except asyncio.CancelledError:
                raise
            except Exception:
                return {"ok": False, "reason": "delivery_error"}
            delivered += 1
        return {"ok": True, "delivered": delivered}

    async def handle_message(self, message):
        if not self._allowed(message):
            return {"ok": False, "ignored": True, "reason": "ignored"}

        text = str(getattr(message, "content", "")).strip()
        action = _command_action(text)

        if text.startswith(COMMAND_PREFIX):
            if action is None:
                return {"ok": False, "ignored": False, "reason": "unknown_command"}
            if not self._is_owner(message):
                return {"ok": False, "ignored": False, "reason": "owner_required"}
            return await self._handle_voice_command(message, action)

        if self._busy:
            return {"ok": False, "ignored": False, "reason": "busy"}

        self._busy = True
        try:
            result = await self._ingest(message)

            if result is None:
                return {"ok": True, "ignored": False, "result": None}

            if not isinstance(result, dict) or not result.get("ok") or result.get("ignored"):
                return {
                    "ok": False,
                    "ignored": bool(isinstance(result, dict) and result.get("ignored")),
                    "reason": "input_error",
                }

            try:
                reply = self._approved_reply(result.get("result"))
            except Exception:
                return {"ok": False, "ignored": False, "reason": "input_error"}

            if not self._reply_enabled or reply is None:
                return {"ok": True, "ignored": False, "result": result}

            try:
                text_reply = str(reply)
            except Exception:
                return {"ok": False, "ignored": False, "reason": "input_error"}

            delivery = await self._deliver(message, text_reply)
            if not delivery.get("ok"):
                return {"ok": False, "ignored": False, "reason": delivery.get("reason", "delivery_error")}

            return {"ok": True, "ignored": False, "result": result}
        except asyncio.CancelledError:
            raise
        except Exception:
            return {"ok": False, "ignored": False, "reason": "input_error"}
        finally:
            self._busy = False

    async def _handle_voice_command(self, message, action):
        if self._voice_command is None:
            return {"ok": False, "ignored": False, "reason": "unknown_command"}
        try:
            owner_id = self._owner_id
            result = await self._voice_command(owner_id, action)
        except asyncio.CancelledError:
            raise
        except Exception:
            return {"ok": False, "ignored": False, "reason": "unknown_command"}

        if not isinstance(result, dict) or not result.get("ok"):
            reason = "unknown_command"
            if isinstance(result, dict) and result.get("reason"):
                reason = str(result["reason"])
            return {"ok": False, "ignored": False, "reason": reason}

        if self._reply_enabled:
            reply = result.get("state") or result.get("reason") or "ok"
            try:
                text_reply = str(reply)
            except Exception:
                return {"ok": False, "ignored": False, "reason": "delivery_error"}
            delivery = await self._deliver(message, text_reply)
            if not delivery.get("ok"):
                return {"ok": False, "ignored": False, "reason": delivery.get("reason", "delivery_error")}

        return {"ok": True, "ignored": False, "result": result}
