from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.auth.dependencies import AuthenticatedClient, authenticate_api_key
from app.schemas.chat import ChatCompletionRequest, ChatCompletionResponse
from app.services.chat import ChatCompletionService

router = APIRouter(prefix="/v1/chat", tags=["Chat"])


def get_chat_service(request: Request) -> ChatCompletionService:
    return ChatCompletionService(request.app.state.resources.providers)


@router.post(
    "/completions",
    response_model=ChatCompletionResponse,
    operation_id="createChatCompletion",
)
async def create_chat_completion(
    payload: ChatCompletionRequest,
    request: Request,
    service: Annotated[ChatCompletionService, Depends(get_chat_service)],
    _authenticated_client: Annotated[AuthenticatedClient, Depends(authenticate_api_key)],
) -> ChatCompletionResponse:
    return await service.create(payload, UUID(request.state.request_id))
