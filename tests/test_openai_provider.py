from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import httpx
import openai
import pytest
from openai import AsyncOpenAI

from app.providers.base import (
    ProviderRateLimitError,
    ProviderRequestError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.providers.openai import OpenAIProvider
from app.schemas.chat import ChatCompletionRequest, ChatMessage


def make_provider(response: object | None = None) -> tuple[OpenAIProvider, AsyncMock]:
    create = AsyncMock(return_value=response)
    client = cast(AsyncOpenAI, SimpleNamespace(responses=SimpleNamespace(create=create)))
    provider = OpenAIProvider(
        client,
        model="gpt-5.6-terra",
        input_cost_per_million_usd=2,
        output_cost_per_million_usd=12,
    )
    return provider, create


def make_request(*, max_tokens: int = 64) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        messages=[
            ChatMessage(role="system", content="Be concise."),
            ChatMessage(role="tool", content="Database is healthy."),
        ],
        temperature=0.2,
        max_tokens=max_tokens,
    )


@pytest.mark.anyio
async def test_openai_provider_uses_responses_api_and_reports_usage() -> None:
    response = SimpleNamespace(
        output_text="Redis is ready.",
        model="gpt-5.6-terra",
        usage=SimpleNamespace(input_tokens=100, output_tokens=25),
        incomplete_details=None,
    )
    provider, create = make_provider(response)

    result = await provider.complete(make_request())

    assert result.content == "Redis is ready."
    assert result.prompt_tokens == 100
    assert result.completion_tokens == 25
    assert result.cost_usd == Decimal("0.0005")
    assert result.finish_reason == "stop"
    create.assert_awaited_once_with(
        model="gpt-5.6-terra",
        input=[
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "Tool result:\nDatabase is healthy."},
        ],
        max_output_tokens=64,
        temperature=0.2,
        store=False,
    )


@pytest.mark.anyio
async def test_openai_provider_reports_output_limit() -> None:
    response = SimpleNamespace(
        output_text="Partial",
        model="gpt-5.6-terra",
        usage=None,
        incomplete_details=SimpleNamespace(reason="max_output_tokens"),
    )
    provider, _ = make_provider(response)

    result = await provider.complete(make_request())

    assert result.finish_reason == "length"
    assert result.prompt_tokens == 0
    assert result.completion_tokens == 0


@pytest.mark.anyio
async def test_openai_provider_rejects_too_small_output_limit() -> None:
    provider, create = make_provider()

    with pytest.raises(ProviderRequestError):
        await provider.complete(make_request(max_tokens=15))

    create.assert_not_awaited()


@pytest.mark.anyio
async def test_openai_provider_rejects_empty_output() -> None:
    response = SimpleNamespace(
        output_text="",
        model="gpt-5.6-terra",
        usage=None,
        incomplete_details=None,
    )
    provider, _ = make_provider(response)

    with pytest.raises(ProviderUnavailableError):
        await provider.complete(make_request())


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("sdk_error", "gateway_error"),
    [
        (
            openai.APITimeoutError(request=httpx.Request("POST", "https://api.openai.com")),
            ProviderTimeoutError,
        ),
        (
            openai.RateLimitError(
                "limited",
                response=httpx.Response(
                    429,
                    request=httpx.Request("POST", "https://api.openai.com"),
                ),
                body=None,
            ),
            ProviderRateLimitError,
        ),
        (
            openai.APIConnectionError(request=httpx.Request("POST", "https://api.openai.com")),
            ProviderUnavailableError,
        ),
    ],
)
async def test_openai_provider_sanitizes_sdk_errors(
    sdk_error: Exception, gateway_error: type[Exception]
) -> None:
    provider, create = make_provider()
    create.side_effect = sdk_error

    with pytest.raises(gateway_error):
        await provider.complete(make_request())
