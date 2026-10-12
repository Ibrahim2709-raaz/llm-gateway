from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import status

from app.core.errors import GatewayError
from app.providers.base import ChatProvider
from app.schemas.chat import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    Choice,
    GatewayMetadata,
    ResponseMessage,
    RoutingAttempt,
    TokenUsage,
)


class ChatCompletionService:
    def __init__(self, provider: ChatProvider) -> None:
        self.provider = provider

    async def create(
        self, request: ChatCompletionRequest, request_id: UUID
    ) -> ChatCompletionResponse:
        self._validate_provider(request)
        result = await self.provider.complete(request)
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
                provider=self.provider.name,
                cost_usd=0,
                cached=False,
                routing_strategy=routing_strategy,
                attempts=[
                    RoutingAttempt(
                        provider=self.provider.name,
                        model=result.model,
                        outcome="success",
                    )
                ],
            ),
        )

    def _validate_provider(self, request: ChatCompletionRequest) -> None:
        if request.model not in {"auto", self.provider.model}:
            raise GatewayError(
                f"Model '{request.model}' was not found.",
                code="model_not_found",
                error_type="invalid_request_error",
                status_code=status.HTTP_404_NOT_FOUND,
                param="model",
            )

        allowed_providers = request.routing.allowed_providers
        if allowed_providers is not None and self.provider.name not in allowed_providers:
            raise GatewayError(
                "No provider satisfies the request policy.",
                code="no_provider_available",
                error_type="service_unavailable_error",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                param="routing.allowed_providers",
            )
