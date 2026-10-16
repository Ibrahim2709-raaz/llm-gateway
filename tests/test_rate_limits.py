from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import Request
from redis.asyncio import Redis
from redis.exceptions import ConnectionError

from app.auth.dependencies import AuthenticatedClient
from app.core.config import Settings
from app.core.errors import GatewayError
from app.rate_limits.dependencies import enforce_request_rate_limit
from app.rate_limits.service import (
    RateLimitBackendError,
    RateLimitStatus,
    RedisRateLimiter,
)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("count", "expected_remaining", "expected_allowed"),
    [(1, 2, True), (3, 0, True), (4, 0, False)],
)
async def test_redis_rate_limiter_enforces_fixed_window(
    count: int,
    expected_remaining: int,
    expected_allowed: bool,
) -> None:
    redis = Mock(spec=Redis)
    redis.eval = AsyncMock(return_value=[count, 45_001])
    client_id = uuid4()
    limiter = RedisRateLimiter(
        redis,
        limit=3,
        window_seconds=60,
        clock=lambda: 1_000.25,
    )

    result = await limiter.consume(client_id)

    assert result == RateLimitStatus(
        limit=3,
        remaining=expected_remaining,
        reset_after_seconds=46,
        reset_at=1046,
        allowed=expected_allowed,
    )
    redis.eval.assert_awaited_once()
    call = redis.eval.await_args
    assert call.args[1:] == (1, f"rate_limit:requests:{client_id}", "60000")


@pytest.mark.anyio
@pytest.mark.parametrize("redis_result", [None, [1], [0, 10], [1, -1]])
async def test_redis_rate_limiter_rejects_invalid_backend_results(
    redis_result: object,
) -> None:
    redis = Mock(spec=Redis)
    redis.eval = AsyncMock(return_value=redis_result)

    with pytest.raises(RateLimitBackendError):
        await RedisRateLimiter(redis, limit=10, window_seconds=60).consume(uuid4())


@pytest.mark.anyio
async def test_redis_rate_limiter_wraps_connection_errors() -> None:
    redis = Mock(spec=Redis)
    redis.eval = AsyncMock(side_effect=ConnectionError("unavailable"))

    with pytest.raises(RateLimitBackendError):
        await RedisRateLimiter(redis, limit=10, window_seconds=60).consume(uuid4())


def test_rate_limit_headers_include_retry_after_only_when_requested() -> None:
    rate_limit = RateLimitStatus(
        limit=60,
        remaining=0,
        reset_after_seconds=12,
        reset_at=1012,
        allowed=False,
    )

    assert rate_limit.headers() == {
        "X-RateLimit-Limit": "60",
        "X-RateLimit-Remaining": "0",
        "X-RateLimit-Reset": "1012",
    }
    assert rate_limit.headers(include_retry_after=True)["Retry-After"] == "12"


def make_request() -> Request:
    resources = SimpleNamespace(redis=object())
    application = SimpleNamespace(state=SimpleNamespace(resources=resources))
    return cast(Request, SimpleNamespace(app=application))


def make_client() -> AuthenticatedClient:
    return AuthenticatedClient(client_id=uuid4(), api_key_id=uuid4(), name="Example")


@pytest.mark.anyio
async def test_rate_limit_dependency_rejects_exhausted_customer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exhausted = RateLimitStatus(
        limit=1,
        remaining=0,
        reset_after_seconds=30,
        reset_at=1030,
        allowed=False,
    )

    async def consume(*_: object) -> RateLimitStatus:
        return exhausted

    monkeypatch.setattr(RedisRateLimiter, "consume", consume)

    with pytest.raises(GatewayError) as error:
        await enforce_request_rate_limit(make_request(), make_client(), Settings(_env_file=None))

    assert error.value.status_code == 429
    assert error.value.code == "rate_limit_exceeded"
    assert error.value.headers == exhausted.headers(include_retry_after=True)


@pytest.mark.anyio
async def test_rate_limit_dependency_fails_closed_when_redis_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def consume(*_: object) -> RateLimitStatus:
        raise RateLimitBackendError

    monkeypatch.setattr(RedisRateLimiter, "consume", consume)

    with pytest.raises(GatewayError) as error:
        await enforce_request_rate_limit(make_request(), make_client(), Settings(_env_file=None))

    assert error.value.status_code == 503
    assert error.value.code == "rate_limit_unavailable"


@pytest.mark.anyio
async def test_rate_limit_dependency_returns_allowed_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allowed = RateLimitStatus(
        limit=60,
        remaining=59,
        reset_after_seconds=60,
        reset_at=1060,
        allowed=True,
    )

    async def consume(*_: object) -> RateLimitStatus:
        return allowed

    monkeypatch.setattr(RedisRateLimiter, "consume", consume)

    result = await enforce_request_rate_limit(
        make_request(), make_client(), Settings(_env_file=None)
    )

    assert result is allowed
