"""When Shana answers in a Discord voice call."""
import pytest

from gamma.integrations.discord.addressing import FOLLOW_UP_SECONDS, decide, says_name
from gamma.integrations.discord.runtime import DiscordRuntimeConfig


def group(text, speaker="alice", last=None, ago=None, others=3, mode="addressed"):
    return decide(text, speaker_id=speaker, others_in_call=others, last_answered=last,
                  last_reply_at=None if ago is None else 100.0 - ago, now=100.0, mode=mode)


@pytest.mark.parametrize("text", ["Shana, what do you think?", "hey shauna how are you", "is that right Shanna",
                                  "what's Shana's favourite game"])
def test_her_name_gets_an_answer_even_in_a_group(text):
    assert says_name(text) and group(text).reason == "named"


def test_people_talking_to_each_other_are_left_alone():
    assert group("did you finish the boss fight yesterday").answer is False
    assert not says_name("I went to the shananigans bar")


def test_the_person_she_just_answered_can_keep_going():
    assert group("and what about the second one?", last="alice", ago=5).reason == "follow_up"
    assert group("and what about the second one?", last="bob", ago=5).answer is False
    assert group("and what about the second one?", last="alice", ago=FOLLOW_UP_SECONDS + 1).answer is False


def test_fillers_are_not_follow_ups():
    assert group("yeah", last="alice", ago=2).reason == "filler"
    assert group("Shana yeah").answer  # her name still counts


def test_one_on_one_calls_need_no_name():
    assert group("what should I cook tonight", others=1).reason == "one_on_one"


def test_everyone_mode_answers_all():
    assert group("did you finish the boss fight", mode="everyone").answer


def test_reply_mode_config():
    assert DiscordRuntimeConfig.from_app_config({"discord": {}}).voice_reply_mode == "addressed"
    assert DiscordRuntimeConfig.from_app_config({"discord": {"voice_reply_mode": "Everyone"}}).voice_reply_mode == "everyone"
    assert DiscordRuntimeConfig.from_app_config({"discord": {"voice_reply_mode": "nonsense"}}).voice_reply_mode == "addressed"


def _session(transcripts, members=3):
    import logging
    from types import SimpleNamespace
    from gamma.integrations.discord.voice_session import DiscordVoiceSession
    session = DiscordVoiceSession.__new__(DiscordVoiceSession)
    session.generation = 0
    session.stats = {"utterances": 0, "dropped": 0, "replies": 0, "failures": 0, "not_for_her": 0, "talked_over": 0}
    session.last_answered = session.last_reply_at = None
    session.logger = logging.getLogger("test")
    session.config = SimpleNamespace(guild_id="1", voice_channel_id="2", voice_reply_mode="addressed")
    channel = SimpleNamespace(members=[SimpleNamespace(bot=False) for _ in range(members)] + [SimpleNamespace(bot=True)])
    guild = SimpleNamespace(get_member=lambda uid: None, get_channel=lambda cid: channel)
    session.client = SimpleNamespace(get_guild=lambda gid: guild)
    session.runtime = SimpleNamespace(normalize_voice=lambda utterance: utterance)
    session.sent, session.played = [], []
    session.during_reply = lambda: None
    reply = {"assistant_response": {"delivery_mode": "speech", "audio_path": "x.wav"}}
    def post(event, **_):
        session.sent.append(event.transcript)
        session.during_reply()
        return reply
    session.stream_client = SimpleNamespace(post_event=post)
    texts = iter(transcripts)
    session._transcribe = lambda wav: next(texts)
    async def play(path, generation):
        session.played.append(path)
    session._play = play
    return session


def _run(session, steps):
    """steps: lists of speakers; each list is queued, then processed until the queue is empty."""
    import asyncio
    from unittest.mock import patch
    async def main():
        session.queue = asyncio.Queue()
        task = asyncio.create_task(session._process())
        for speakers in steps:
            for user in speakers:
                session.queue.put_nowait((0, {"wav": b"x", "user_id": user}))
            await asyncio.wait_for(session.queue.join(), 2)
        task.cancel()
    with patch("gamma.integrations.discord.voice_session.approved_reply", lambda result: True):
        asyncio.run(main())


def test_voice_session_only_sends_her_what_is_for_her():
    session = _session(["did you see the game", "Shana, did you see it?", "and who won?", "yeah"])
    _run(session, [["222"], ["111"], ["111"], ["111"]])  # one at a time, as people take turns
    assert session.sent == ["Shana, did you see it?", "and who won?"]
    assert session.stats["not_for_her"] == 2 and len(session.played) == 2 and session.stats["failures"] == 0


def test_a_follow_up_is_not_spoken_over_someone_who_started_talking():
    session = _session(["Shana, hi", "what's up?", "anyway as I was saying"])
    _run(session, [["111"]])
    session.during_reply = lambda: session.queue.put_nowait((0, {"wav": b"x", "user_id": "222"}))
    import asyncio
    from unittest.mock import patch
    async def main():
        session.queue = asyncio.Queue()
        task = asyncio.create_task(session._process())
        session.queue.put_nowait((0, {"wav": b"x", "user_id": "111"}))
        await asyncio.wait_for(session.queue.join(), 2)
        task.cancel()
    with patch("gamma.integrations.discord.voice_session.approved_reply", lambda result: True):
        asyncio.run(main())
    assert session.sent == ["Shana, hi", "what's up?"] and session.played == ["x.wav"]
    assert session.stats["talked_over"] == 1 and session.stats["not_for_her"] == 1  # bob wasn't talking to her
