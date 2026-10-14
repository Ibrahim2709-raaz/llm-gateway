from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import status

from app.core.errors import GatewayError
from app.providers.base import (
    ChatProvider,
    ProviderRateLimitError,
    ProviderRequestError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
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
    def __init__(self, providers: tuple[ChatProvider, ...]) -> None:
        self.providers = providers

    async def create(
        self, request: ChatCompletionRequest, request_id: UUID
    ) -> ChatCompletionResponse:
        provider = self._select_provider(request)
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
                attempts=[
                    RoutingAttempt(
                        provider=provider.name,
                        model=result.model,
                        outcome="success",
                    )
                ],
            ),
        )

    def _select_provider(self, request: ChatCompletionRequest) -> ChatProvider:
        allowed_providers = request.routing.allowed_providers
        eligible = [
            provider
            for provider in self.providers
            if allowed_providers is None or provider.name in allowed_providers
        ]
        if request.model != "auto":
            eligible = [provider for provider in eligible if provider.model == request.model]
        if eligible:
            return eligible[0]

        known_model = any(provider.model == request.model for provider in self.providers)
        if request.model != "auto" and not known_model:
            raise GatewayError(
                f"Model '{request.model}' was not found.",
                code="model_not_found",
                error_type="invalid_request_error",
                status_code=status.HTTP_404_NOT_FOUND,
                param="model",
            )

        raise GatewayError(
            "No provider satisfies the request policy.",
            code="no_provider_available",
            error_type="service_unavailable_error",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            param="routing.allowed_providers",
        )
