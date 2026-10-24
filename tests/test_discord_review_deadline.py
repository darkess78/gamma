from concurrent.futures import TimeoutError
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gamma.config import settings
from gamma.safety.llm_reviewer import LLMReviewDecision
from gamma.stream.brain import StreamBrain
from gamma.stream.models import StreamActor, StreamInputEvent


@pytest.mark.parametrize("source,action", [("discord","allow"),("twitch","skip")])
def test_cold_reviewer_can_finish_for_discord_without_extending_live_stream_deadline(source,action):
    class Future:
        def result(self,timeout):
            if timeout<10:raise TimeoutError()
            return LLMReviewDecision(action="allow",reason="safe",confidence=1)
        def cancel(self):pass
        def done(self):return True
    class Executor:
        def __init__(self,**kwargs):pass
        def submit(self,*args):return Future()
        def shutdown(self,**kwargs):pass
    brain=SimpleNamespace(_safety_reviewer=SimpleNamespace(review=lambda text:None))
    event=StreamInputEvent(kind="chat_message",text="hello",actor=StreamActor(source=source,platform_id="1"))
    with patch.object(settings,"speech_filter_llm_enabled",True),patch.object(settings,"stream_safety_review_timeout_seconds",5),patch.object(settings,"stream_safety_review_timeout_action","skip"),patch('gamma.stream.brain.ThreadPoolExecutor',Executor):
        result=StreamBrain._review_stream_output_with_llm(brain,event,"Hello")
    assert result["action"]==action
    assert result["playback_approved"] is (action=="allow")
