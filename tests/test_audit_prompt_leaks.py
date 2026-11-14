"""Audit repros: owner or friend data that reaches a model prompt on a shared turn.

Each test is one confirmed leak path, marked xfail(strict=True) until it is
fixed, so the suite stays green and a fix flips the test to XPASS (which
strict mode reports as a failure, prompting removal of the marker).

Everything runs in the eval sandbox with a recording fake model: no real
model, GPU or data. Run with ``--runxfail`` to see the leak assertions.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlmodel import Session

from gamma.evals.memory_privacy import RecordingModel, _Dispatcher, _Reviewer
from gamma.evals.sandbox import sandbox

OWNER_NAME = "ZQOWNERREALNAME"
FRIEND_NAME = "ZQFRIENDREALNAME"
OWNER_DISCORD_ID = "111111"


@contextmanager
def _world(*, users_toml: str | None = None):
    with sandbox(full=True, owner_name=OWNER_NAME) as box:
        if users_toml is not None:
            (box.config / "users.toml").write_text(users_toml, encoding="utf-8")
        from gamma.conversation.service import ConversationService

        service = ConversationService()  # reads users.toml now
        model = RecordingModel("ZQPRIVATEREPLY", "ZQPRIVATEOBJECTIVE")
        service._llm = model
        yield box, service, model


def _brain(service):
    from gamma.stream.brain import StreamBrain

    return StreamBrain(conversation=service, output_dispatcher=_Dispatcher(), safety_reviewer=_Reviewer())


def _event(*, kind, source, user, name, target, session, text, extra=None):
    from gamma.stream.models import StreamActor, StreamInputEvent

    metadata = {"output_target_policy": target, "raw_text": text, "trust_level": "regular"}
    if name:
        metadata["safe_display_name"] = name
    if source == "twitch":
        metadata["input_safety"] = {"safe_prompt_text": text}
    metadata.update(extra or {})
    return StreamInputEvent(kind=kind, text=text, session_id=session, priority=30,
                            actor=StreamActor(source=source, platform_id=user, display_name=name),
                            metadata=metadata)


def _draft_prompts(model) -> list[str]:
    return [c["system"] for c in model.calls if c["purpose"] == "conversation_draft"]


def _all_surfaces(model) -> str:
    return "\n".join(text for _label, text in model.surfaces())


def _seed_person(service, *, name: str, trust: str, platform: str, platform_user_id: str) -> None:
    from gamma.memory.models import KnownPerson, PersonIdentity

    with Session(service._memory._engine) as db:
        person = KnownPerson(name=name, trust=trust, relationship_to_user="friend")
        db.add(person)
        db.flush()
        db.add(PersonIdentity(person_id=person.id, platform=platform, platform_user_id=platform_user_id))
        db.commit()


# ---------------------------------------------------------------------------
# Finding 1: the resolved speaker name (owner's configured name, or the name
# the owner gave a person record) is journaled as speaker_name on shared turns
# that carry no chat speaker_label, and replayed with named_speakers=True in
# "# Shared Channel History" for that audience.
# ---------------------------------------------------------------------------

_OWNER_DISCORD_USERS = (
    f'[owner]\nname = "{OWNER_NAME}"\ntrust = "owner"\n\n'
    f'[owner.platform_ids]\ndiscord = "{OWNER_DISCORD_ID}"\n'
)


def _scenario_owner_discord_voice(service, model):
    """The owner speaks in the Discord voice call; a viewer speaks next."""
    brain = _brain(service)
    brain.handle_event(_event(kind="mic_transcript", source="discord", user=OWNER_DISCORD_ID, name=None,
                              target="discord_call", session="discord-voice",
                              text="Shana, I am back in the ZQCALLMARK call."), synthesize_speech=False)
    model.calls.clear()
    brain.handle_event(_event(kind="mic_transcript", source="discord", user="222222", name=None,
                              target="discord_call", session="discord-voice",
                              text="Shana, who was just talking?"), synthesize_speech=False)
    return OWNER_NAME


def _scenario_owner_monitor_to_stream(service, model):
    """The owner sends an owner_command from the Monitor with stream output, twice."""
    brain = _brain(service)
    from gamma.stream.models import StreamActor, StreamInputEvent

    def owner_command(text):
        return StreamInputEvent(kind="owner_command", text=text, session_id="monitor-local-stream", priority=20,
                                actor=StreamActor(source="local_monitor", platform_id="owner", display_name="Owner",
                                                  roles=["owner", "operator"]),
                                metadata={"output_target_policy": "stream_public", "local_monitor": True})

    brain.handle_event(owner_command("Shana, say hi to ZQCALLMARK chat."), synthesize_speech=False, owner_channel=True)
    model.calls.clear()
    brain.handle_event(owner_command("Shana, now tell chat a joke."), synthesize_speech=False, owner_channel=True)
    return OWNER_NAME


def _scenario_friend_twitch_redeem(service, model):
    """A friend the owner linked by Twitch id redeems a reward; another viewer chats next."""
    _seed_person(service, name=FRIEND_NAME, trust="trusted", platform="twitch", platform_user_id="friend-tw")
    brain = _brain(service)
    brain.handle_event(_event(kind="redeem", source="twitch", user="friend-tw", name="coolfriend",
                              target="stream_public", session="twitch",
                              text="Shana, play the ZQCALLMARK song"), synthesize_speech=False)
    model.calls.clear()
    brain.handle_event(_event(kind="chat_message", source="twitch", user="viewer-x", name="ViewerX",
                              target="stream_public", session="twitch",
                              text="Shana, who redeemed that?"), synthesize_speech=False)
    return FRIEND_NAME


@pytest.mark.xfail(strict=True, reason="AUDIT: resolved speaker real name replayed in shared channel history")
@pytest.mark.parametrize("scenario", [_scenario_owner_discord_voice, _scenario_owner_monitor_to_stream,
                                      _scenario_friend_twitch_redeem],
                         ids=["owner_discord_voice", "owner_monitor_to_stream", "friend_twitch_redeem"])
def test_shared_history_never_names_speaker_by_private_profile_name(scenario):
    users = _OWNER_DISCORD_USERS if scenario is _scenario_owner_discord_voice else None
    with _world(users_toml=users) as (_box, service, model):
        secret_name = scenario(service, model)
        prompts = _draft_prompts(model)
        assert prompts, "the second shared turn never reached the model"
        assert "kind: shared" in prompts[0]
        # Recall control: the first turn really is in this audience's history.
        assert "# Shared Channel History" in prompts[0] and "ZQCALLMARK" in prompts[0]
        assert secret_name not in _all_surfaces(model), (
            f"{secret_name} (private profile name) reached a shared prompt")


# ---------------------------------------------------------------------------
# Finding 2: an image turn from an unknown HTTP caller puts the absolute host
# path of the stored upload (Gamma PC home directory / OS username) in the
# model input.
# ---------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="AUDIT: absolute host image path in unknown caller's model input")
def test_unknown_image_turn_does_not_show_host_paths(monkeypatch):
    from gamma.config import settings
    from gamma.schemas.response import VisionAnalysis

    with _world() as (box, service, model):
        image_dir = box.root / "home" / "ZQHOSTUSER" / "gamma-main" / "data" / "images"
        image_dir.mkdir(parents=True)
        monkeypatch.setattr(settings, "image_input_dir", image_dir)
        monkeypatch.setattr(service._vision, "analyze_image",
                            lambda **_kwargs: VisionAnalysis(summary="A cat on a desk.", confidence=0.9))
        service.respond_with_image(user_text="What is in this picture?", image_bytes=b"\x89PNG\r\n\x1a\nfake",
                                   image_media_type="image/png", image_filename="cat.png")
        drafts = [c for c in model.calls if c["purpose"] == "conversation_draft"]
        assert drafts and "kind: unknown" in drafts[0]["system"]
        assert "A cat on a desk." in drafts[0]["input"]  # recall control: vision context did arrive
        assert "ZQHOSTUSER" not in _all_surfaces(model), "host filesystem path reached an unknown caller's prompt"
