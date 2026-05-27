from __future__ import annotations

import unittest

from gamma.identity.profile import SpeakerProfile
from gamma.integrations.discord import DiscordMessage, DiscordRuntime, DiscordRuntimeConfig, DiscordVoiceUtterance, normalize_discord_message, normalize_discord_voice
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


if __name__ == "__main__":
    unittest.main()
