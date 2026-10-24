import asyncio
import threading
from unittest.mock import Mock, patch

from gamma.conversation.service import ConversationService
from gamma.identity.profile import SpeakerProfile
from gamma.tools.base import ToolResult


def test_owner_coding_goal_queues_without_conversation_inference():
    service=ConversationService()
    service._identity.resolve=Mock(return_value=SpeakerProfile('Owner','owner',is_owner=True))
    service._llm_adapter=Mock(side_effect=AssertionError('Coding receipt must not wait for inference'))
    tool=Mock();tool.run.return_value=ToolResult(ok=True,output='queued',metadata={'status':'queued','job_id':'work-proof'})
    service._tools=Mock();service._tools.get.return_value=tool
    service._remember_assistant_state=Mock()
    with patch('gamma.conversation.service.build_system_prompt',return_value='prompt'), \
         patch.object(service,'_append_timing_log'), \
         patch('gamma.conversation.service.settings.speech_filter_llm_enabled',False):
        result=service.respond('coding goal: Improve editable coding lesson history',fast_mode=True)
    assert result.tool_results[0].ok and 'queued' in result.display_text.lower()
    tool.run.assert_called_once_with(goal='Improve editable coding lesson history')
    service._llm_adapter.assert_not_called()


def test_monitor_input_does_not_block_event_loop():
    from gamma.dashboard import main
    entered=threading.Event();release=threading.Event()
    def blocking(payload):
        entered.set();assert release.wait(2);return {'ok':True}
    async def payload(request):return {'text':'hello'}
    manager=Mock();manager.submit_monitor_stream_input=blocking
    async def run():
        with patch.object(main,'_json_object_payload',payload),patch.object(main,'get_dashboard_service',return_value=manager):
            task=asyncio.create_task(main.dashboard_monitor_input(Mock()))
            try:
                await asyncio.sleep(0.05)
                assert entered.is_set() and not task.done()
            finally:release.set()
            assert (await task)['ok'] is True
    asyncio.run(run())
