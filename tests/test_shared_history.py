"""Shared channels remember their own recent turns, and only their own."""
from gamma.stream.brain import StreamBrain
from gamma.stream.models import StreamActor, StreamInputEvent
from tests.test_conversation_recipient import (  # noqa: F401
    FACT, LEGACY, PRIVATE_WORK, Dispatcher, Reviewer, fixture_state, prompt_layers,
)


def chat(text, *, source="twitch", user="viewer-1", name="Ruby", session="mixed"):
    target = {"discord": "discord_call"}.get(source)
    metadata = {"output_target_policy": target} if target else {}
    # High priority bypasses stream pacing, so every message here gets a reply.
    return StreamInputEvent(kind="chat_message", text=text, session_id=session, priority=30,
                            actor=StreamActor(source=source, platform_id=user, display_name=name),
                            metadata=metadata)


def brain_for(service):
    return StreamBrain(conversation=service, output_dispatcher=Dispatcher(), safety_reviewer=Reviewer())


def last_prompt(llm):
    drafts = [c for c in llm.calls if c["purpose"] == "conversation_draft"]
    return drafts[-1]["system"]


def test_twitch_gets_its_own_recent_chat_with_names(fixture_state):
    service = fixture_state.service
    brain = brain_for(service)
    brain.handle_event(chat("Shana, my cat is called Biscuit"))
    brain.handle_event(chat("Shana, what was my cat called?"))
    prompt = last_prompt(service._llm)
    assert "# Shared Channel History" in prompt
    assert "Ruby: Shana, my cat is called Biscuit" in prompt
    assert all(FACT not in p and PRIVATE_WORK not in p and LEGACY not in p for p in prompt_layers(service._llm))


def test_discord_and_stream_histories_never_mix(fixture_state):
    service = fixture_state.service
    brain = brain_for(service)
    brain.handle_event(chat("Shana, DISCORD_ONLY_CANARY", source="discord", user="d-1", name="Mo"))
    brain.handle_event(chat("Shana, hello from twitch"))
    assert "DISCORD_ONLY_CANARY" not in last_prompt(service._llm)
    brain.handle_event(chat("Shana, TWITCH_ONLY_CANARY"))
    brain.handle_event(chat("Shana, back on discord", source="discord", user="d-1", name="Mo"))
    discord_prompt = last_prompt(service._llm)
    assert "TWITCH_ONLY_CANARY" not in discord_prompt
    assert "Mo: Shana, DISCORD_ONLY_CANARY" in discord_prompt


def test_discord_text_stays_out_of_stream_chat_memory(fixture_state):
    brain = brain_for(fixture_state.service)
    brain.handle_event(chat("Shana, DISCORD_ONLY_CANARY", source="discord", user="d-1", name="Mo"))
    assert "DISCORD_ONLY_CANARY" not in (brain._assemble_recent_context() or "")
