from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gamma.integrations.twitch.models import TwitchChatMessage
from gamma.integrations.twitch.irc import chat_message_from_irc, parse_irc_line
from gamma.integrations.twitch.normalize import normalize_chat_message
from gamma.integrations.twitch.replay import replay_jsonl
from gamma.integrations.twitch.sanitize import classify_chat_text, safe_username_alias
from gamma.integrations.twitch.trust import ViewerTrustStore
from gamma.integrations.twitch.worker import TwitchIrcWorker, TwitchWorkerConfig
from gamma.errors import ConfigurationError
from gamma.stream.brain import StreamBrain
from gamma.stream.models import StreamInputEvent
from gamma.stream.trace import StreamTraceStore


class _FakeConversation:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def respond(self, **kwargs):
        from gamma.schemas.response import AssistantResponse

        self.calls.append(kwargs)
        return AssistantResponse(spoken_text="Hello chat.", emotion="happy")


class _FakeClient:
    def __init__(self) -> None:
        self.events: list[StreamInputEvent] = []

    def post_event(self, event: StreamInputEvent, *, synthesize_speech: bool = False, fast_mode: bool = True):
        self.events.append(event)
        return {"input_event": event.model_dump(), "synthesize_speech": synthesize_speech, "fast_mode": fast_mode}


class _FakeTrustStore:
    def __init__(self, levels: dict[str, str] | None = None) -> None:
        self.levels = levels or {}

    def trust_level_for(self, *, platform: str, platform_user_id: str | None, default: str = "new_viewer"):
        return self.levels.get(platform_user_id or "", default)


class TwitchIntegrationTest(unittest.TestCase):
    def test_chat_message_normalizes_into_stream_input_event(self) -> None:
        event = normalize_chat_message(
            TwitchChatMessage(
                text="Shana what are you doing?",
                platform_user_id="u1",
                display_name="ShanaFan42",
                message_id="m1",
                badges={"subscriber": "1"},
            )
        )

        self.assertEqual(event.kind, "chat_message")
        self.assertEqual(event.actor.source, "twitch")
        self.assertEqual(event.actor.platform_id, "u1")
        self.assertEqual(event.metadata["raw_text"], "Shana what are you doing?")
        self.assertEqual(event.metadata["safe_display_name"], "Shana Fan")
        self.assertGreater(event.priority, 0)

    def test_owner_user_id_is_marked_but_still_chat_message(self) -> None:
        event = normalize_chat_message(
            TwitchChatMessage(text="please answer normally", platform_user_id="owner-1", display_name="Owner"),
            owner_user_id="owner-1",
        )

        self.assertEqual(event.kind, "chat_message")
        self.assertEqual(event.actor.roles, ["owner"])
        self.assertTrue(event.metadata["is_owner"])
        self.assertEqual(event.metadata["trust_level"], "owner")

    def test_spam_is_summarized_before_prompt_context(self) -> None:
        event = normalize_chat_message(
            TwitchChatMessage(
                text="buy views at badsite.example",
                platform_user_id="spam1",
                display_name="buy_views_9281",
            )
        )

        self.assertEqual(event.text, "A spam or scam message was posted in chat.")
        self.assertEqual(event.metadata["raw_text"], "buy views at badsite.example")
        self.assertEqual(event.metadata["input_safety"]["category"], "spam_or_scam")
        self.assertNotIn("badsite", event.text or "")

    def test_safe_username_alias_handles_simple_and_weird_names(self) -> None:
        self.assertEqual(safe_username_alias("ShanaFan42"), "Shana Fan")
        self.assertEqual(safe_username_alias("xx_S71K3R_xx"), "a viewer")
        self.assertEqual(safe_username_alias("buy_views_9281"), "a viewer")

    def test_blocked_trust_drops_priority_and_summarizes(self) -> None:
        classification = classify_chat_text("Shana answer me", trust_level="blocked")

        self.assertTrue(classification.should_drop)
        self.assertEqual(classification.safe_prompt_text, "A blocked viewer posted a message.")
        self.assertLess(classification.priority_delta, 0)

    def test_shana_mention_routes_through_stream_brain(self) -> None:
        conversation = _FakeConversation()
        with tempfile.TemporaryDirectory() as temp_dir:
            brain = StreamBrain(
                conversation=conversation,  # type: ignore[arg-type]
                trace_store=StreamTraceStore(Path(temp_dir) / "trace.jsonl"),
            )
            result = brain.handle_event(StreamInputEvent(kind="chat_message", text="Shana hello"))

        self.assertEqual(result.decision.decision, "reply")
        self.assertEqual(conversation.calls[0]["user_text"], "Shana hello")

    def test_replay_jsonl_posts_normalized_events(self) -> None:
        client = _FakeClient()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "events.jsonl"
            path.write_text(
                "\n".join(
                    [
                        json.dumps({"kind": "chat_message", "platform_user_id": "u1", "display_name": "viewer1", "text": "Shana hi"}),
                        json.dumps({"kind": "chat_message", "platform_user_id": "spam1", "display_name": "bot", "text": "buy views at badsite.example"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            results = replay_jsonl(path, client=client, synthesize_speech=False)

        self.assertEqual(len(results), 2)
        self.assertEqual(len(client.events), 2)
        self.assertEqual(client.events[0].kind, "chat_message")
        self.assertEqual(client.events[1].text, "A spam or scam message was posted in chat.")

    def test_follow_replay_event_is_first_class_stream_event(self) -> None:
        client = _FakeClient()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "events.jsonl"
            path.write_text(
                json.dumps({"kind": "follow", "platform_user_id": "u2", "display_name": "newviewer"}) + "\n",
                encoding="utf-8",
            )

            replay_jsonl(path, client=client)

        self.assertEqual(client.events[0].kind, "follow")
        self.assertEqual(client.events[0].priority, 20)

    def test_irc_privmsg_parses_tags_and_text(self) -> None:
        line = (
            "@badge-info=;badges=subscriber/12;color=#1E90FF;display-name=Viewer\\sOne;"
            "id=msg-1;user-id=u1 :viewer!viewer@viewer.tmi.twitch.tv PRIVMSG #shana :Shana hello"
        )

        message = parse_irc_line(line)
        chat = chat_message_from_irc(message)

        self.assertIsNotNone(chat)
        assert chat is not None
        self.assertEqual(chat.text, "Shana hello")
        self.assertEqual(chat.platform_user_id, "u1")
        self.assertEqual(chat.display_name, "Viewer One")
        self.assertEqual(chat.message_id, "msg-1")
        self.assertEqual(chat.badges, {"subscriber": "12"})

    def test_worker_posts_normalized_privmsg(self) -> None:
        client = _FakeClient()
        worker = TwitchIrcWorker(
            config=TwitchWorkerConfig(
                channel="#Shana",
                bot_username="bot",
                oauth_token="oauth:test",
                owner_user_id="owner-1",
            ),
            client=client,  # type: ignore[arg-type]
            trust_store=_FakeTrustStore(),  # type: ignore[arg-type]
        )
        line = (
            "@badges=;display-name=Owner;id=m1;user-id=owner-1 "
            ":owner!owner@owner.tmi.twitch.tv PRIVMSG #shana :Shana test"
        )

        result = worker.handle_line(line)

        self.assertIsNotNone(result)
        self.assertEqual(client.events[0].session_id, "twitch:shana")
        self.assertEqual(client.events[0].actor.roles, ["owner"])
        self.assertEqual(client.events[0].metadata["is_owner"], True)

    def test_worker_ignores_non_chat_irc_lines(self) -> None:
        client = _FakeClient()
        worker = TwitchIrcWorker(
            config=TwitchWorkerConfig(channel="shana", bot_username="bot", oauth_token="oauth:test"),
            client=client,  # type: ignore[arg-type]
            trust_store=_FakeTrustStore(),  # type: ignore[arg-type]
        )

        result = worker.handle_line("PING :tmi.twitch.tv")

        self.assertIsNone(result)
        self.assertEqual(client.events, [])

    def test_worker_config_requires_credentials(self) -> None:
        with patch("gamma.integrations.twitch.worker.settings") as mock_settings:
            mock_settings.twitch_channel = ""
            mock_settings.twitch_bot_username = ""
            mock_settings.twitch_oauth_token = ""

            with self.assertRaises(ConfigurationError) as ctx:
                TwitchWorkerConfig.from_settings()

        self.assertIn("twitch_channel", str(ctx.exception))
        self.assertIn("twitch_bot_username", str(ctx.exception))
        self.assertIn("twitch_oauth_token", str(ctx.exception))

    def test_viewer_trust_store_persists_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ViewerTrustStore(database_url=f"sqlite:///{Path(temp_dir) / 'trust.db'}")

            record = store.upsert(
                platform="twitch",
                platform_user_id="u1",
                display_name="Viewer",
                trust_level="trusted",
                notes="good chatter",
                pronunciation_alias="vee",
            )
            fetched = store.get(platform="twitch", platform_user_id="u1")

        self.assertEqual(record.trust_level, "trusted")
        self.assertIsNotNone(fetched)
        assert fetched is not None
        self.assertEqual(fetched.notes, "good chatter")
        self.assertEqual(fetched.pronunciation_alias, "vee")

    def test_worker_uses_viewer_trust_override_for_priority(self) -> None:
        client = _FakeClient()
        worker = TwitchIrcWorker(
            config=TwitchWorkerConfig(channel="shana", bot_username="bot", oauth_token="oauth:test"),
            client=client,  # type: ignore[arg-type]
            trust_store=_FakeTrustStore({"u1": "trusted"}),  # type: ignore[arg-type]
        )
        line = (
            "@badges=;display-name=Viewer;id=m1;user-id=u1 "
            ":viewer!viewer@viewer.tmi.twitch.tv PRIVMSG #shana :normal topical message"
        )

        worker.handle_line(line)

        self.assertEqual(client.events[0].metadata["trust_level"], "trusted")
        self.assertEqual(client.events[0].priority, 1)


if __name__ == "__main__":
    unittest.main()
