from types import SimpleNamespace
from unittest.mock import Mock

from gamma.schemas.response import AssistantResponse
from gamma.stream.brain import StreamBrain
from gamma.stream.models import StreamActor, StreamInputEvent


def test_text_only_discord_reply_receives_output_review():
    event = StreamInputEvent(kind="chat_message", text="hello", actor=StreamActor(source="discord", platform_id="123"))
    response = AssistantResponse(display_text="A text reply", speech_text="", delivery_mode="text_only")
    reviewer = Mock(return_value={"action": "allow", "blocked": False, "playback_approved": True})
    brain = SimpleNamespace(_review_stream_output_with_llm=reviewer)
    result = StreamBrain._review_stream_output(brain, event, response)
    reviewer.assert_called_once_with(event, "A text reply")
    assert result["playback_approved"] is True
