from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..conversation.service import ConversationService
from ..errors import ConfigurationError, ConversationError, ExternalServiceError, GammaError
from ..schemas.conversation import ConversationRequest
from ..schemas.response import AssistantResponse

router = APIRouter()
conversation_service = ConversationService()


@router.get("/")
def root() -> dict[str, str]:
    return {"message": "gamma backend scaffold"}


@router.get("/v1/assistant/demo", response_model=AssistantResponse)
def assistant_demo() -> AssistantResponse:
    return AssistantResponse(
        spoken_text="Hey. Gamma's scaffold is alive.",
        emotion="neutral",
        motions=[],
        tool_calls=[],
        memory_candidates=[],
    )


@router.get("/v1/memory/stats")
def memory_stats() -> dict[str, str | int]:
    try:
        return conversation_service.memory_stats()
    except ConfigurationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except GammaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/v1/conversation/respond", response_model=AssistantResponse)
def conversation_respond(request: ConversationRequest) -> AssistantResponse:
    try:
        return conversation_service.respond(
            user_text=request.user_text,
            session_id=request.session_id,
            synthesize_speech=request.synthesize_speech,
        )
    except ConversationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConfigurationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ExternalServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except GammaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
