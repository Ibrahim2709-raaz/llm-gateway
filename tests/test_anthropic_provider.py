from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import anthropic
import httpx2
import pytest
from anthropic import AsyncAnthropic

from app.providers.anthropic import AnthropicProvider
from app.providers.base import (
    ProviderRateLimitError,
    ProviderRequestError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.schemas.chat import ChatCompletionRequest, ChatMessage


def make_provider(response: object | None = None) -> tuple[AnthropicProvider, AsyncMock]:
    create = AsyncMock(return_value=response)
    client = cast(AsyncAnthropic, SimpleNamespace(messages=SimpleNamespace(create=create)))
    provider = AnthropicProvider(
        client,
        model="claude-sonnet-5-5",
        input_cost_per_million_usd=2,
        output_cost_per_million_usd=10,
    )
    return provider, create


def make_request(
    messages: list[ChatMessage] | None = None,
) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        messages=messages
        or [
            ChatMessage(role="system", content="Be concise."),
            ChatMessage(role="tool", content="Database is healthy."),
            ChatMessage(role="user", content="Summarize the status."),
        ],
        temperature=0.2,
        max_tokens=64,
    )


@pytest.mark.anyio
async def test_anthropic_provider_uses_messages_api_and_reports_usage() -> None:
    response = SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking="Internal"),
            SimpleNamespace(type="text", text="All systems are healthy."),
        ],
        model="claude-sonnet-5-5",
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=100, output_tokens=25),
    )
    provider, create = make_provider(response)

    result = await provider.complete(make_request())

    assert result.content == "All systems are healthy."
    assert result.prompt_tokens == 100
    assert result.completion_tokens == 25
    assert result.cost_usd == Decimal("0.00045")
    assert result.finish_reason == "stop"
    create.assert_awaited_once_with(
        model="claude-sonnet-5-5",
        max_tokens=64,
        messages=[
            {"role": "user", "content": "Tool result:\nDatabase is healthy."},
            {"role": "user", "content": "Summarize the status."},
        ],
        system="Be concise.",
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("stop_reason", "expected"),
    [
        ("max_tokens", "length"),
        ("model_context_window_exceeded", "length"),
        ("refusal", "content_filter"),
        ("stop_sequence", "stop"),
    ],
)
async def test_anthropic_provider_normalizes_stop_reason(
    stop_reason: str,
    expected: str,
) -> None:
    response = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="Response")],
        model="claude-sonnet-5-5",
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=0, output_tokens=0),
    )
    provider, _ = make_provider(response)

    result = await provider.complete(make_request())

    assert result.finish_reason == expected


@pytest.mark.anyio
@pytest.mark.parametrize(
    "messages",
    [
        [ChatMessage(role="system", content="Only instructions")],
        [ChatMessage(role="assistant", content="Prefilled response")],
    ],
)
async def test_anthropic_provider_rejects_unsupported_conversation(
    messages: list[ChatMessage],
) -> None:
    provider, create = make_provider()

    with pytest.raises(ProviderRequestError):
        await provider.complete(make_request(messages))

    create.assert_not_awaited()


@pytest.mark.anyio
async def test_anthropic_provider_rejects_empty_text_output() -> None:
    response = SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking="Internal")],
        model="claude-sonnet-5-5",
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )
    provider, _ = make_provider(response)

    with pytest.raises(ProviderUnavailableError):
        await provider.complete(make_request())


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("sdk_error", "gateway_error"),
    [
        (
            anthropic.APITimeoutError(
                request=httpx2.Request("POST", "https://api.anthropic.com")
            ),
            ProviderTimeoutError,
        ),
        (
            anthropic.RateLimitError(
                "limited",
                response=httpx2.Response(
                    429,
                    request=httpx2.Request("POST", "https://api.anthropic.com"),
                ),
                body=None,
            ),
            ProviderRateLimitError,
        ),
        (
            anthropic.BadRequestError(
                "invalid",
                response=httpx2.Response(
                    400,
                    request=httpx2.Request("POST", "https://api.anthropic.com"),
                ),
                body=None,
            ),
            ProviderRequestError,
        ),
        (
            anthropic.APIConnectionError(
                request=httpx2.Request("POST", "https://api.anthropic.com")
            ),
            ProviderUnavailableError,
        ),
    ],
)
async def test_anthropic_provider_sanitizes_sdk_errors(
    sdk_error: Exception,
    gateway_error: type[Exception],
) -> None:
    provider, create = make_provider()
    create.side_effect = sdk_error

    with pytest.raises(gateway_error):
        await provider.complete(make_request())
