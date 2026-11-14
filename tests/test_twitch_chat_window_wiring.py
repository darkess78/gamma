"""Twitch chat reaches the chat window queue: worker -> client -> /v1/stream/events?chat_window=true."""
import json
from unittest.mock import patch

import pytest

from gamma.integrations.twitch.client import GammaStreamClient
from gamma.integrations.twitch.worker import TwitchIrcWorker, TwitchWorkerConfig

LINE = "@badges=;display-name=Viewer;id=m1;user-id=u1 :viewer!viewer@viewer.tmi.twitch.tv PRIVMSG #shana :Shana hi"


class Trust:
    def trust_level_for(self, *, platform, platform_user_id, default="new_viewer"):
        return default


class People:
    def resolve_person_identity(self, platform, platform_id):
        return None


class Client:
    def __init__(self):
        self.calls = []

    def post_event(self, event, **kwargs):
        self.calls.append(kwargs)
        return {"status": "queued", "queue_id": "q1"}


@pytest.mark.parametrize("on", [True, False])
def test_worker_asks_for_the_chat_window_only_when_enabled(tmp_path, on):
    client = Client()
    worker = TwitchIrcWorker(config=TwitchWorkerConfig(channel="#shana", bot_username="bot", oauth_token="oauth:x",
                                                       chat_window=on),
                             client=client, trust_store=Trust(), state_path=tmp_path / "state.json", memory=People())
    worker.handle_line(LINE)
    assert client.calls[0].get("chat_window", False) is on


def test_client_puts_the_flag_on_the_request():
    seen = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps({"status": "queued"}).encode()
    def urlopen(request, timeout):
        seen.append(request.full_url)
        return Response()
    from gamma.stream.models import StreamActor, StreamInputEvent
    event = StreamInputEvent(kind="chat_message", text="hi", actor=StreamActor(source="twitch", platform_id="u1"))
    with patch("urllib.request.urlopen", urlopen):
        GammaStreamClient(base_url="http://shana", bearer_token="").post_event(event, chat_window=True)
        GammaStreamClient(base_url="http://shana", bearer_token="").post_event(event)
    assert "chat_window=true" in seen[0] and "chat_window" not in seen[1]


def test_setting_defaults_to_on(monkeypatch):
    from gamma.config import Settings, settings
    for key, value in {"twitch_channel": "shana", "twitch_bot_username": "bot", "twitch_oauth_token": "oauth:x"}.items():
        monkeypatch.setattr(settings, key, value)
    assert Settings.__dataclass_fields__["twitch_chat_window_enabled"].default is True
    assert TwitchWorkerConfig.from_settings().chat_window is settings.twitch_chat_window_enabled
