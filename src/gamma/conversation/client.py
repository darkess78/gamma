"""Compatibility client for CLI inputs into the managed Shana brain."""
from ..dashboard.shana_client import ShanaApiClient
from ..schemas.conversation import ConversationRequest
from ..schemas.response import AssistantResponse


class ConversationClient:
    def respond(self, user_text, session_id=None, synthesize_speech=False,
                speaker_ctx=None, fast_mode=False, evaluation_mode=False):
        request = ConversationRequest(user_text=user_text, session_id=session_id,
            synthesize_speech=synthesize_speech, speaker=speaker_ctx,
            fast_mode=fast_mode, evaluation_mode=evaluation_mode)
        client = ShanaApiClient(timeout=180)
        try:
            value = client.post("/v1/conversation/respond", request.model_dump(mode="json"), timeout=180)
            return AssistantResponse.model_validate(value)
        finally:
            client.close()
