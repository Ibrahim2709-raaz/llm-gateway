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
from app.providers.local import LocalOpenAIProvider
from app.schemas.chat import ChatCompletionRequest, ChatMessage


def make_provider(response: object | None = None) -> tuple[LocalOpenAIProvider, AsyncMock]:
    create = AsyncMock(return_value=response)
    client = cast(
        AsyncOpenAI,
        SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
    )
    provider = LocalOpenAIProvider(
        client,
        model="qwen3:8b",
        input_cost_per_million_usd=0.1,
        output_cost_per_million_usd=0.2,
    )
    return provider, create


def make_request() -> ChatCompletionRequest:
    return ChatCompletionRequest(
        messages=[
            ChatMessage(role="system", content="Be concise."),
            ChatMessage(role="tool", content="Redis is healthy."),
            ChatMessage(role="user", content="Summarize the status."),
        ],
        temperature=0.2,
        max_tokens=64,
    )


@pytest.mark.anyio
async def test_local_provider_uses_chat_completions_and_reports_usage() -> None:
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="All systems are healthy."),
                finish_reason="stop",
            )
        ],
        model="qwen3:8b",
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=25),
    )
    provider, create = make_provider(response)

    result = await provider.complete(make_request())

    assert result.content == "All systems are healthy."
    assert result.prompt_tokens == 100
    assert result.completion_tokens == 25
    assert result.cost_usd == Decimal("0.000015")
    assert result.finish_reason == "stop"
    create.assert_awaited_once_with(
        model="qwen3:8b",
        messages=[
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "Tool result:\nRedis is healthy."},
            {"role": "user", "content": "Summarize the status."},
        ],
        max_tokens=64,
        temperature=0.2,
        stream=False,
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("finish_reason", "expected"),
    [
        ("length", "length"),
        ("content_filter", "content_filter"),
        ("stop", "stop"),
        ("tool_calls", "stop"),
    ],
)
async def test_local_provider_normalizes_finish_reason(
    finish_reason: str,
    expected: str,
) -> None:
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="Response"),
                finish_reason=finish_reason,
            )
        ],
        model="qwen3:8b",
        usage=None,
    )
    provider, _ = make_provider(response)

    result = await provider.complete(make_request())

    assert result.finish_reason == expected
    assert result.prompt_tokens == 0
    assert result.completion_tokens == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response",
    [
        SimpleNamespace(choices=[], model="qwen3:8b", usage=None),
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=None),
                    finish_reason="stop",
                )
            ],
            model="qwen3:8b",
            usage=None,
        ),
    ],
)
async def test_local_provider_rejects_missing_text(response: object) -> None:
    provider, _ = make_provider(response)

    with pytest.raises(ProviderUnavailableError):
        await provider.complete(make_request())


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("sdk_error", "gateway_error"),
    [
        (
            openai.APITimeoutError(request=httpx.Request("POST", "http://localhost")),
            ProviderTimeoutError,
        ),
        (
            openai.RateLimitError(
                "limited",
                response=httpx.Response(
                    429,
                    request=httpx.Request("POST", "http://localhost"),
                ),
                body=None,
            ),
            ProviderRateLimitError,
        ),
        (
            openai.BadRequestError(
                "invalid",
                response=httpx.Response(
                    400,
                    request=httpx.Request("POST", "http://localhost"),
                ),
                body=None,
            ),
            ProviderRequestError,
        ),
        (
            openai.APIConnectionError(request=httpx.Request("POST", "http://localhost")),
            ProviderUnavailableError,
        ),
    ],
)
async def test_local_provider_sanitizes_sdk_errors(
    sdk_error: Exception,
    gateway_error: type[Exception],
) -> None:
    provider, create = make_provider()
    create.side_effect = sdk_error

    with pytest.raises(gateway_error):
        await provider.complete(make_request())
