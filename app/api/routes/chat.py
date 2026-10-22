from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AuthenticatedClient, authenticate_api_key
from app.cache.service import RedisResponseCache
from app.core.config import get_settings
from app.db.session import get_database_session
from app.rate_limits.dependencies import enforce_request_rate_limit
from app.rate_limits.service import RateLimitStatus
from app.schemas.chat import ChatCompletionRequest, ChatCompletionResponse
from app.services.chat import ChatCompletionService
from app.usage.service import DatabaseUsageService

router = APIRouter(prefix="/v1/chat", tags=["Chat"])


def get_chat_service(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ChatCompletionService:
    settings = get_settings()
    response_cache = (
        RedisResponseCache(
            request.app.state.resources.redis,
            ttl_seconds=settings.response_cache_ttl_seconds,
        )
        if settings.response_cache_enabled
        else None
    )
    return ChatCompletionService(
        request.app.state.resources.providers,
        max_retries=settings.provider_max_retries,
        retry_base_delay_seconds=settings.provider_retry_base_delay_seconds,
        retry_max_delay_seconds=settings.provider_retry_max_delay_seconds,
        response_cache=response_cache,
        usage_tracker=DatabaseUsageService(session),
    )


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
    authenticated_client: Annotated[AuthenticatedClient, Depends(authenticate_api_key)],
    rate_limit: Annotated[RateLimitStatus, Depends(enforce_request_rate_limit)],
) -> ChatCompletionResponse:
    response.headers.update(rate_limit.headers())
    return await service.create(
        payload,
        UUID(request.state.request_id),
        authenticated_client.client_id,
    )
