from decimal import Decimal
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError

from app.cache.service import RedisResponseCache, build_cache_key
from app.providers.base import ModelRoutingProfile, ProviderResult
from app.schemas.chat import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    RoutingOptions,
)
from app.services.chat import ChatCompletionService


def make_request(
    *,
    content: str = "secret prompt",
    allowed_providers: list[str] | None = None,
    disable_cache: bool = False,
) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        messages=[ChatMessage(role="user", content=content)],
        routing=RoutingOptions(
            allowed_providers=allowed_providers,
            disable_cache=disable_cache,
        ),
    )


def make_response(request_id: UUID) -> ChatCompletionResponse:
    return ChatCompletionResponse.model_validate(
        {
            "id": "chatcmpl_cached",
            "created": 1_790_707_200,
            "model": "test-1",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "cached response"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
            "gateway": {
                "request_id": request_id,
                "provider": "test",
                "cost_usd": 0.01,
                "cached": False,
                "routing_strategy": "balanced",
                "task_complexity": "simple",
                "estimated_cost_usd": 0.02,
                "attempts": [{"provider": "test", "model": "test-1", "outcome": "success"}],
            },
        }
    )


def test_cache_key_is_canonical_private_and_customer_isolated() -> None:
    client_id = uuid4()
    first = make_request(allowed_providers=["openai", "anthropic"])
    reordered = make_request(
        allowed_providers=["anthropic", "openai"],
        disable_cache=True,
    )

    first_key = build_cache_key(client_id, first)
    reordered_key = build_cache_key(client_id, reordered)

    assert first_key == reordered_key
    assert "secret prompt" not in first_key
    assert build_cache_key(uuid4(), first) != first_key
    assert build_cache_key(client_id, make_request(content="different")) != first_key


@pytest.mark.anyio
async def test_redis_cache_reads_valid_response() -> None:
    response = make_response(uuid4())
    redis = Mock(spec=Redis)
    redis.get = AsyncMock(return_value=response.model_dump_json())
    cache = RedisResponseCache(redis, ttl_seconds=300)

    cached = await cache.get(uuid4(), make_request())

    assert cached == response


@pytest.mark.anyio
@pytest.mark.parametrize("cached_value", [None, "not-json", "{}"])
async def test_redis_cache_treats_missing_or_invalid_values_as_misses(
    cached_value: str | None,
) -> None:
    redis = Mock(spec=Redis)
    redis.get = AsyncMock(return_value=cached_value)
    cache = RedisResponseCache(redis, ttl_seconds=300)

    assert await cache.get(uuid4(), make_request()) is None


@pytest.mark.anyio
async def test_redis_cache_writes_with_expiry() -> None:
    client_id = uuid4()
    request = make_request()
    response = make_response(uuid4())
    redis = Mock(spec=Redis)
    redis.set = AsyncMock(return_value=True)
    cache = RedisResponseCache(redis, ttl_seconds=123)

    await cache.set(client_id, request, response)

    redis.set.assert_awaited_once_with(
        build_cache_key(client_id, request),
        response.model_dump_json(),
        ex=123,
    )


@pytest.mark.anyio
async def test_redis_cache_fails_open_on_backend_errors() -> None:
    redis = Mock(spec=Redis)
    redis.get = AsyncMock(side_effect=ConnectionError("down"))
    redis.set = AsyncMock(side_effect=ConnectionError("down"))
    cache = RedisResponseCache(redis, ttl_seconds=300)

    assert await cache.get(uuid4(), make_request()) is None
    await cache.set(uuid4(), make_request(), make_response(uuid4()))


class CountingProvider:
    name = "counting"
    model = "counting-1"
    routing_profile = ModelRoutingProfile(
        input_cost_per_million_usd=Decimal("1"),
        output_cost_per_million_usd=Decimal("1"),
        estimated_latency_ms=10,
        quality_score=0.8,
    )

    def __init__(self) -> None:
        self.call_count = 0

    async def complete(self, request: ChatCompletionRequest) -> ProviderResult:
        self.call_count += 1
        return ProviderResult(
            content="provider response",
            model=self.model,
            prompt_tokens=2,
            completion_tokens=3,
            finish_reason="stop",
            cost_usd=Decimal("0.01"),
        )


class MemoryCache:
    def __init__(self) -> None:
        self.values: dict[str, ChatCompletionResponse] = {}
        self.get_count = 0
        self.set_count = 0

    async def get(
        self, client_id: UUID, request: ChatCompletionRequest
    ) -> ChatCompletionResponse | None:
        self.get_count += 1
        return self.values.get(build_cache_key(client_id, request))

    async def set(
        self,
        client_id: UUID,
        request: ChatCompletionRequest,
        response: ChatCompletionResponse,
    ) -> None:
        self.set_count += 1
        self.values[build_cache_key(client_id, request)] = response


@pytest.mark.anyio
async def test_chat_service_returns_isolated_cache_hit_without_provider_call() -> None:
    provider = CountingProvider()
    cache = MemoryCache()
    service = ChatCompletionService((provider,), response_cache=cache)
    client_id = uuid4()
    first_request_id = uuid4()
    second_request_id = uuid4()

    first = await service.create(make_request(), first_request_id, client_id)
    second = await service.create(make_request(), second_request_id, client_id)

    assert provider.call_count == 1
    assert first.gateway.cached is False
    assert second.gateway.cached is True
    assert second.gateway.request_id == second_request_id
    assert second.gateway.cost_usd == 0
    assert second.gateway.attempts == []
    assert second.id != first.id
    assert second.choices == first.choices

    await service.create(make_request(), uuid4(), uuid4())
    assert provider.call_count == 2


@pytest.mark.anyio
async def test_disable_cache_bypasses_reads_and_writes() -> None:
    provider = CountingProvider()
    cache = MemoryCache()
    service = ChatCompletionService((provider,), response_cache=cache)
    request = make_request(disable_cache=True)
    client_id = uuid4()

    await service.create(request, uuid4(), client_id)
    await service.create(request, uuid4(), client_id)

    assert provider.call_count == 2
    assert cache.get_count == 0
    assert cache.set_count == 0
