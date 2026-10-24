from unittest.mock import Mock
from gamma.conversation.service import ConversationService
from gamma.identity.profile import SpeakerProfile
from gamma.schemas.response import ToolCall
from gamma.tools.base import ToolResult


def test_coding_tool_requires_trusted_owner_context_not_model_args():
    service=ConversationService.__new__(ConversationService)
    tool=Mock();tool.run.return_value=ToolResult(ok=True,output='queued',metadata={'status':'queued'})
    service._tools=Mock();service._tools.get.return_value=tool
    call=ToolCall(tool='coding_goal',args={'goal':'Improve the coding lessons','is_owner':True})
    for speaker in [None,SpeakerProfile('Guest','guest'),SpeakerProfile('Friend','trusted'),SpeakerProfile('Fake','owner',is_owner=False)]:
        results=service._execute_tool_calls([call],speaker=speaker)
        assert not results[0].ok and results[0].metadata['reason']=='owner_required'
    tool.run.assert_not_called()
    owner=SpeakerProfile('Owner','owner',is_owner=True)
    assert service._execute_tool_calls([call],speaker=owner)[0].ok
    assert 'is_owner' not in tool.run.call_args.kwargs


def test_owner_prefix_queues_concrete_goal_but_guest_does_not():
    service=ConversationService.__new__(ConversationService)
    owner=SpeakerProfile('Owner','owner',is_owner=True)
    calls=service._infer_tool_calls('coding goal: Improve the coding lessons with revision history',owner)
    assert calls[0].tool=='coding_goal' and calls[0].args['goal'].startswith('Improve')
    assert not any(c.tool=='coding_goal' for c in service._infer_tool_calls('coding goal: Improve the coding lessons',SpeakerProfile('Friend','trusted')))
