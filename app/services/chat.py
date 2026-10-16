from datetime import UTC, datetime
from uuid import UUID, uuid4

import structlog
from fastapi import status

from app.core.errors import GatewayError
from app.providers.base import (
    ChatProvider,
    ProviderRateLimitError,
    ProviderRequestError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.routing.service import ProviderRouter
from app.schemas.chat import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    Choice,
    GatewayMetadata,
    ResponseMessage,
    RoutingAttempt,
    TokenUsage,
)

logger = structlog.get_logger(__name__)


class ChatCompletionService:
    def __init__(self, providers: tuple[ChatProvider, ...]) -> None:
        self.providers = providers
        self.router = ProviderRouter(providers)

    async def create(
        self, request: ChatCompletionRequest, request_id: UUID
    ) -> ChatCompletionResponse:
        routing_decision = self.router.route(request)
        provider = routing_decision.provider
        logger.info(
            "provider_route_selected",
            request_id=str(request_id),
            provider=provider.name,
            model=provider.model,
            strategy="explicit" if request.model != "auto" else request.routing.strategy,
            task_complexity=routing_decision.task_complexity,
            estimated_cost_usd=str(routing_decision.estimated_cost_usd),
        )
        try:
            result = await provider.complete(request)
        except ProviderRequestError as exc:
            raise GatewayError(
                str(exc),
                code="invalid_request",
                error_type="invalid_request_error",
                status_code=status.HTTP_400_BAD_REQUEST,
                param="max_tokens",
            ) from exc
        except ProviderTimeoutError as exc:
            raise GatewayError(
                "The provider request timed out.",
                code="provider_timeout",
                error_type="provider_error",
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            ) from exc
        except (ProviderRateLimitError, ProviderUnavailableError) as exc:
            raise GatewayError(
                "The provider could not complete the request.",
                code="provider_error",
                error_type="provider_error",
                status_code=status.HTTP_502_BAD_GATEWAY,
            ) from exc
        total_tokens = result.prompt_tokens + result.completion_tokens
        routing_strategy = "explicit" if request.model != "auto" else request.routing.strategy

        return ChatCompletionResponse(
            id=f"chatcmpl_{uuid4().hex}",
            created=int(datetime.now(UTC).timestamp()),
            model=result.model,
            choices=[
                Choice(
                    index=0,
                    message=ResponseMessage(content=result.content),
                    finish_reason=result.finish_reason,
                )
            ],
            usage=TokenUsage(
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                total_tokens=total_tokens,
            ),
            gateway=GatewayMetadata(
                request_id=request_id,
                provider=provider.name,
                cost_usd=float(result.cost_usd),
                cached=False,
                routing_strategy=routing_strategy,
                task_complexity=routing_decision.task_complexity,
                estimated_cost_usd=float(routing_decision.estimated_cost_usd),
                attempts=[
                    RoutingAttempt(
                        provider=provider.name,
                        model=result.model,
                        outcome="success",
                    )
                ],
            ),
        )
