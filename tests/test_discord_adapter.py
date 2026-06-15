from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import anyio

from gamma.identity.profile import SpeakerProfile
from gamma.integrations.discord import DiscordMessage, DiscordRuntime, DiscordRuntimeConfig, DiscordVoiceUtterance, normalize_discord_message, normalize_discord_voice
from gamma.integrations.discord.worker import DiscordTextWorker, read_discord_worker_state
from gamma.observability import configure_logging
from gamma.performer.models import PerformerOutputEvent


class _Resolver:
    def __init__(self, profile: SpeakerProfile) -> None:
        self.profile = profile
        self.calls = []

    def resolve(self, ctx):
        self.calls.append(ctx)
        return self.profile


class DiscordAdapterTest(unittest.TestCase):
    def test_discord_message_maps_identity_into_stream_input(self) -> None:
        resolver = _Resolver(SpeakerProfile(name="Owner", trust="owner", is_owner=True, resolved_via="discord"))

        event = normalize_discord_message(
            DiscordMessage(text="Shana can you hear me?", user_id="123", display_name="OwnerName", channel_id="c1", message_id="m1"),
            identity_resolver=resolver,  # type: ignore[arg-type]
        )

        self.assertEqual(event.kind, "chat_message")
        self.assertEqual(event.actor.source, "discord")
        self.assertEqual(event.actor.platform_id, "123")
        self.assertEqual(event.actor.display_name, "OwnerName")
        self.assertEqual(event.actor.roles, ["owner"])
        self.assertEqual(event.metadata["trust_level"], "owner")
        self.assertTrue(event.metadata["is_owner"])
        self.assertEqual(resolver.calls[0].source, "discord")
        self.assertEqual(resolver.calls[0].platform_id, "123")

    def test_discord_voice_maps_to_mic_transcript(self) -> None:
        resolver = _Resolver(SpeakerProfile(name="Guest", trust="guest", resolved_via="discord"))

        event = normalize_discord_voice(
            DiscordVoiceUtterance(transcript="hello from voice", user_id="456", display_name="GuestName"),
            identity_resolver=resolver,  # type: ignore[arg-type]
        )

        self.assertEqual(event.kind, "mic_transcript")
        self.assertEqual(event.text, "hello from voice")
        self.assertEqual(event.actor.roles, ["guest"])
        self.assertEqual(event.metadata["input_modality"], "voice")
        self.assertEqual(event.metadata["profile_name"], "Guest")

    def test_runtime_tracks_normalized_inputs(self) -> None:
        resolver = _Resolver(SpeakerProfile(name="Guest", trust="guest", resolved_via="discord"))
        runtime = DiscordRuntime(DiscordRuntimeConfig(enabled=True, bot_token="token"), identity_resolver=resolver)  # type: ignore[arg-type]

        event = runtime.normalize_message(DiscordMessage(text="hello", user_id="456", display_name="GuestName"))

        self.assertEqual(event.actor.source, "discord")
        self.assertEqual(runtime.status()["input_count"], 1)
        self.assertEqual(runtime.status()["last_input"]["actor"]["platform_id"], "456")

    def test_runtime_only_handles_discord_call_outputs_when_enabled(self) -> None:
        runtime = DiscordRuntime(DiscordRuntimeConfig(enabled=True, bot_token="token", output_enabled=True))

        ignored = runtime.handle_output_event(PerformerOutputEvent(type="subtitle_update", turn_id="turn-1", payload={"text": "public"}))
        handled = runtime.handle_output_event(
            PerformerOutputEvent(type="speech_started", turn_id="turn-2", target_policy="discord_call", payload={"text": "discord"})
        )

        self.assertFalse(ignored["handled"])
        self.assertTrue(handled["handled"])
        self.assertEqual(runtime.status()["output_count"], 1)

    def test_runtime_start_requires_token(self) -> None:
        runtime = DiscordRuntime(DiscordRuntimeConfig(enabled=True))

        result = runtime.start()

        self.assertFalse(result["ok"])
        self.assertIn("token", result["error"])

    def test_text_worker_posts_allowlisted_message_without_logging_content(self) -> None:
        class _StreamClient:
            def __init__(self) -> None:
                self.events = []

            def post_event(self, event, *, synthesize_speech: bool, fast_mode: bool):
                self.events.append((event, synthesize_speech, fast_mode))
                event.metadata["request_id"] = "request-1"
                return {"ok": True}

        async def _run() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                log_path = Path(temp_dir) / "discord.jsonl"
                logger = configure_logging(
                    f"discord-worker-{id(log_path)}",
                    log_path=log_path,
                    stderr=False,
                )
                stream_client = _StreamClient()
                worker = DiscordTextWorker(
                    DiscordRuntimeConfig(
                        enabled=True,
                        bot_token="private-discord-token",
                        guild_id="guild-1",
                        text_channel_id="channel-1",
                    ),
                    stream_client=stream_client,  # type: ignore[arg-type]
                    state_path=Path(temp_dir) / "state.json",
                    logger=logger,
                )
                message = SimpleNamespace(
                    id="message-1",
                    content="private discord message",
                    guild=SimpleNamespace(id="guild-1"),
                    channel=SimpleNamespace(id="channel-1"),
                    author=SimpleNamespace(
                        id="user-1",
                        bot=False,
                        display_name="Viewer",
                        roles=[SimpleNamespace(name="member")],
                    ),
                )

                result = await worker.handle_message(message)
                for handler in logger.handlers:
                    handler.flush()
                records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
                state = read_discord_worker_state(Path(temp_dir) / "state.json")

            self.assertTrue(result["ok"])
            event, synthesize_speech, fast_mode = stream_client.events[0]
            self.assertEqual(event.kind, "chat_message")
            self.assertEqual(event.actor.platform_id, "user-1")
            self.assertFalse(synthesize_speech)
            self.assertTrue(fast_mode)
            self.assertEqual(state["message_count"], 1)
            posted = next(record for record in records if record["event"] == "discord_text.event.posted")
            self.assertEqual(posted["message_id"], "message-1")
            self.assertEqual(posted["request_id"], "request-1")
            self.assertNotIn("private discord message", json.dumps(records))
            self.assertNotIn("private-discord-token", json.dumps(records))

        anyio.run(_run)

    def test_text_worker_ignores_other_channels_and_bots(self) -> None:
        async def _run() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                worker = DiscordTextWorker(
                    DiscordRuntimeConfig(
                        enabled=True,
                        bot_token="token",
                        guild_id="guild-1",
                        text_channel_id="channel-1",
                    ),
                    stream_client=Mock(),
                    state_path=Path(temp_dir) / "state.json",
                    logger=configure_logging(
                        f"discord-ignore-{id(temp_dir)}",
                        log_path=Path(temp_dir) / "discord.jsonl",
                        stderr=False,
                    ),
                )
                other_channel = SimpleNamespace(
                    id="message-1",
                    content="hello",
                    guild=SimpleNamespace(id="guild-1"),
                    channel=SimpleNamespace(id="channel-2"),
                    author=SimpleNamespace(id="user-1", bot=False),
                )
                bot_message = SimpleNamespace(
                    id="message-2",
                    content="hello",
                    guild=SimpleNamespace(id="guild-1"),
                    channel=SimpleNamespace(id="channel-1"),
                    author=SimpleNamespace(id="bot-1", bot=True),
                )

                channel_result = await worker.handle_message(other_channel)
                bot_result = await worker.handle_message(bot_message)

            self.assertEqual(channel_result["reason"], "channel_not_allowed")
            self.assertEqual(bot_result["reason"], "bot_author")

        anyio.run(_run)

    def test_text_worker_post_failure_is_durable_and_has_traceback(self) -> None:
        class _FailingStreamClient:
            def post_event(self, *_args, **_kwargs):
                raise RuntimeError("stream API unavailable")

        async def _run() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                log_path = Path(temp_dir) / "discord.jsonl"
                logger = configure_logging(
                    f"discord-post-failure-{id(log_path)}",
                    log_path=log_path,
                    stderr=False,
                )
                worker = DiscordTextWorker(
                    DiscordRuntimeConfig(
                        enabled=True,
                        bot_token="token",
                        guild_id="guild-1",
                        text_channel_id="channel-1",
                    ),
                    stream_client=_FailingStreamClient(),  # type: ignore[arg-type]
                    state_path=Path(temp_dir) / "state.json",
                    logger=logger,
                )
                message = SimpleNamespace(
                    id="message-1",
                    content="hello",
                    guild=SimpleNamespace(id="guild-1"),
                    channel=SimpleNamespace(id="channel-1"),
                    author=SimpleNamespace(id="user-1", bot=False, display_name="Viewer", roles=[]),
                )

                result = await worker.handle_message(message)
                for handler in logger.handlers:
                    handler.flush()
                records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]

            self.assertFalse(result["ok"])
            failure = next(record for record in records if record["event"] == "discord_text.event.post_failed")
            self.assertEqual(failure["error_class"], "RuntimeError")
            self.assertIn("Traceback", failure["traceback"])

        anyio.run(_run)

    def test_text_worker_records_disconnect_and_reconnect(self) -> None:
        class _Client:
            def __init__(self) -> None:
                self.handlers = {}

            def event(self, handler):
                self.handlers[handler.__name__] = handler
                return handler

        async def _run() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                log_path = Path(temp_dir) / "discord.jsonl"
                logger = configure_logging(
                    f"discord-reconnect-{id(log_path)}",
                    log_path=log_path,
                    stderr=False,
                )
                worker = DiscordTextWorker(
                    DiscordRuntimeConfig(
                        enabled=True,
                        bot_token="token",
                        guild_id="guild-1",
                        text_channel_id="channel-1",
                    ),
                    state_path=Path(temp_dir) / "state.json",
                    logger=logger,
                )
                client = _Client()
                worker._install_handlers(client)
                await client.handlers["on_ready"]()
                await client.handlers["on_disconnect"]()
                await client.handlers["on_ready"]()
                for handler in logger.handlers:
                    handler.flush()
                records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
                state = read_discord_worker_state(Path(temp_dir) / "state.json")

            events = [record["event"] for record in records]
            self.assertIn("discord_text.connection.disconnected", events)
            self.assertIn("discord_text.connection.reconnected", events)
            self.assertEqual(state["reconnect_count"], 1)
            self.assertTrue(state["connected"])

        anyio.run(_run)


if __name__ == "__main__":
    unittest.main()
