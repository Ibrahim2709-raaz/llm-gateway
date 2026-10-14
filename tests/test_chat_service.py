from decimal import Decimal
from uuid import uuid4

import pytest

from app.core.errors import GatewayError
from app.providers.base import (
    ProviderRateLimitError,
    ProviderRequestError,
    ProviderResult,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.schemas.chat import ChatCompletionRequest, ChatMessage, RoutingOptions
from app.services.chat import ChatCompletionService


class SuccessfulProvider:
    name = "successful"
    model = "successful-1"

    async def complete(self, request: ChatCompletionRequest) -> ProviderResult:
        return ProviderResult(
            content=request.messages[-1].content,
            model=self.model,
            prompt_tokens=10,
            completion_tokens=5,
            finish_reason="stop",
            cost_usd=Decimal("0.25"),
        )


class FailingProvider:
    name = "failing"
    model = "failing-1"

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def complete(self, request: ChatCompletionRequest) -> ProviderResult:
        raise self.error


def make_request(
    *, model: str = "auto", allowed_providers: list[str] | None = None
) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model=model,
        messages=[ChatMessage(role="user", content="Hello")],
        routing=RoutingOptions(allowed_providers=allowed_providers),
    )


@pytest.mark.anyio
async def test_service_selects_explicit_model_and_reports_cost() -> None:
    provider = SuccessfulProvider()
    service = ChatCompletionService((provider,))

    result = await service.create(make_request(model=provider.model), uuid4())

    assert result.model == provider.model
    assert result.gateway.provider == provider.name
    assert result.gateway.cost_usd == 0.25
    assert result.gateway.routing_strategy == "explicit"


@pytest.mark.anyio
async def test_service_rejects_known_model_excluded_by_policy() -> None:
    provider = SuccessfulProvider()
    service = ChatCompletionService((provider,))

    with pytest.raises(GatewayError) as captured:
        await service.create(
            make_request(model=provider.model, allowed_providers=["another"]), uuid4()
        )

    assert captured.value.status_code == 503
    assert captured.value.code == "no_provider_available"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider_error", "status_code", "code"),
    [
        (ProviderRequestError("at least 16 tokens"), 400, "invalid_request"),
        (ProviderTimeoutError("timeout"), 504, "provider_timeout"),
        (ProviderRateLimitError("limited"), 502, "provider_error"),
        (ProviderUnavailableError("down"), 502, "provider_error"),
    ],
)
async def test_service_maps_provider_errors(
    provider_error: Exception, status_code: int, code: str
) -> None:
    service = ChatCompletionService((FailingProvider(provider_error),))

    with pytest.raises(GatewayError) as captured:
        await service.create(make_request(), uuid4())

    assert captured.value.status_code == status_code
    assert captured.value.code == code
