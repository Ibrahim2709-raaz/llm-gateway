import hashlib
import json
from typing import Protocol
from uuid import UUID

import structlog
from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.schemas.chat import ChatCompletionRequest, ChatCompletionResponse

logger = structlog.get_logger(__name__)
CACHE_KEY_VERSION = "v1"


class ResponseCache(Protocol):
    async def get(
        self, client_id: UUID, request: ChatCompletionRequest
    ) -> ChatCompletionResponse | None: ...

    async def set(
        self,
        client_id: UUID,
        request: ChatCompletionRequest,
        response: ChatCompletionResponse,
    ) -> None: ...


def build_cache_key(client_id: UUID, request: ChatCompletionRequest) -> str:
    payload = request.model_dump(mode="json")
    routing = payload["routing"]
    if isinstance(routing, dict):
        routing.pop("disable_cache", None)
        allowed_providers = routing.get("allowed_providers")
        if isinstance(allowed_providers, list):
            routing["allowed_providers"] = sorted(allowed_providers)
    canonical_request = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    digest = hashlib.sha256(canonical_request.encode()).hexdigest()
    return f"response_cache:{CACHE_KEY_VERSION}:{client_id}:{digest}"


class RedisResponseCache:
    def __init__(self, redis: Redis, *, ttl_seconds: int) -> None:
        self.redis = redis
        self.ttl_seconds = ttl_seconds

    async def get(
        self, client_id: UUID, request: ChatCompletionRequest
    ) -> ChatCompletionResponse | None:
        cache_key = build_cache_key(client_id, request)
        try:
            cached_value = await self.redis.get(cache_key)
        except RedisError:
            logger.warning("response_cache_read_failed", client_id=str(client_id))
            return None
        if cached_value is None:
            return None
        try:
            return ChatCompletionResponse.model_validate_json(cached_value)
        except (ValidationError, ValueError, TypeError):
            logger.warning("response_cache_value_invalid", client_id=str(client_id))
            return None

    async def set(
        self,
        client_id: UUID,
        request: ChatCompletionRequest,
        response: ChatCompletionResponse,
    ) -> None:
        cache_key = build_cache_key(client_id, request)
        try:
            await self.redis.set(
                cache_key,
                response.model_dump_json(),
                ex=self.ttl_seconds,
            )
        except RedisError:
            logger.warning("response_cache_write_failed", client_id=str(client_id))
