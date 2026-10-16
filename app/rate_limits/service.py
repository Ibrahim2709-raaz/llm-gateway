import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError

FIXED_WINDOW_SCRIPT = """
local current = redis.call("INCR", KEYS[1])
if current == 1 then
    redis.call("PEXPIRE", KEYS[1], ARGV[1])
end
local ttl = redis.call("PTTL", KEYS[1])
if ttl < 0 then
    redis.call("PEXPIRE", KEYS[1], ARGV[1])
    ttl = tonumber(ARGV[1])
end
return {current, ttl}
"""


class RateLimitBackendError(Exception):
    """Raised when a safe rate-limit decision cannot be made."""


@dataclass(frozen=True, slots=True)
class RateLimitStatus:
    limit: int
    remaining: int
    reset_after_seconds: int
    reset_at: int
    allowed: bool

    def headers(self, *, include_retry_after: bool = False) -> dict[str, str]:
        headers = {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(self.remaining),
            "X-RateLimit-Reset": str(self.reset_at),
        }
        if include_retry_after:
            headers["Retry-After"] = str(self.reset_after_seconds)
        return headers


class RedisRateLimiter:
    def __init__(
        self,
        redis: Redis,
        *,
        limit: int,
        window_seconds: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.redis = redis
        self.limit = limit
        self.window_seconds = window_seconds
        self.clock = clock

    async def consume(self, client_id: UUID) -> RateLimitStatus:
        key = f"rate_limit:requests:{client_id}"
        window_milliseconds = self.window_seconds * 1000
        try:
            raw_result = await cast(
                Awaitable[object],
                self.redis.eval(
                    FIXED_WINDOW_SCRIPT,
                    1,
                    key,
                    str(window_milliseconds),
                ),
            )
            count, ttl_milliseconds = self._parse_result(raw_result)
        except (RedisError, TypeError, ValueError) as error:
            raise RateLimitBackendError from error

        reset_after_seconds = max(1, math.ceil(ttl_milliseconds / 1000))
        return RateLimitStatus(
            limit=self.limit,
            remaining=max(0, self.limit - count),
            reset_after_seconds=reset_after_seconds,
            reset_at=math.ceil(self.clock() + ttl_milliseconds / 1000),
            allowed=count <= self.limit,
        )

    @staticmethod
    def _parse_result(raw_result: object) -> tuple[int, int]:
        if not isinstance(raw_result, (list, tuple)) or len(raw_result) != 2:
            raise ValueError("Unexpected Redis rate-limit response")
        count = int(raw_result[0])
        ttl_milliseconds = int(raw_result[1])
        if count < 1 or ttl_milliseconds < 0:
            raise ValueError("Invalid Redis rate-limit values")
        return count, ttl_milliseconds
