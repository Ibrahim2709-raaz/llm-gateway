from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from app.auth.dependencies import AuthenticatedClient, authenticate_api_key
from app.rate_limits.dependencies import enforce_request_rate_limit
from app.rate_limits.service import RateLimitStatus
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
    response: Response,
    service: Annotated[ChatCompletionService, Depends(get_chat_service)],
    _authenticated_client: Annotated[AuthenticatedClient, Depends(authenticate_api_key)],
    rate_limit: Annotated[RateLimitStatus, Depends(enforce_request_rate_limit)],
) -> ChatCompletionResponse:
    response.headers.update(rate_limit.headers())
    return await service.create(payload, UUID(request.state.request_id))
