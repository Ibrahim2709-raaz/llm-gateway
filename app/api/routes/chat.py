from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AuthenticatedClient, authenticate_api_key
from app.cache.service import RedisResponseCache
from app.core.config import get_settings
from app.db.session import get_database_session
from app.observability.metrics import METRICS
from app.rate_limits.dependencies import enforce_request_rate_limit
from app.rate_limits.service import RateLimitStatus
from app.routing.repository import DatabaseRoutingEvidenceRepository
from app.routing.rollout import IntelligentRoutingRollout
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
        metrics=METRICS,
        circuit_breaker=request.app.state.resources.provider_circuit_breaker,
        intelligent_routing_enabled=settings.intelligent_routing_enabled,
        intelligent_routing_shadow_enabled=settings.intelligent_routing_shadow_enabled,
        intelligent_routing_rollout=IntelligentRoutingRollout(
            percentage=settings.intelligent_routing_canary_percent,
            customer_allowlist=settings.intelligent_routing_customer_ids,
        ),
        evidence_repository=(
            DatabaseRoutingEvidenceRepository(session)
            if (
                settings.intelligent_routing_enabled
                or settings.intelligent_routing_shadow_enabled
            )
            else None
        ),
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
