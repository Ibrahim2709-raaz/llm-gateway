from dataclasses import dataclass

from openai import AsyncOpenAI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import Settings
from app.execution.circuit_breaker import ProviderCircuitBreaker
from app.providers.base import ChatProvider
from app.providers.mock import MockProvider
from app.providers.openai import OpenAIProvider


@dataclass(frozen=True, slots=True)
class AppResources:
    database_engine: AsyncEngine
    redis: Redis
    providers: tuple[ChatProvider, ...]
    openai_client: AsyncOpenAI | None
    provider_circuit_breaker: ProviderCircuitBreaker


def create_resources(settings: Settings) -> AppResources:
    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
    )
    redis = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=settings.dependency_check_timeout_seconds,
        socket_timeout=settings.dependency_check_timeout_seconds,
    )
    openai_client: AsyncOpenAI | None = None
    providers: list[ChatProvider] = []
    if settings.openai_enabled and settings.openai_api_key is not None:
        openai_client = AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            timeout=settings.openai_timeout_seconds,
            max_retries=0,
        )
        providers.append(
            OpenAIProvider(
                openai_client,
                model=settings.openai_model,
                input_cost_per_million_usd=settings.openai_input_cost_per_million_usd,
                output_cost_per_million_usd=settings.openai_output_cost_per_million_usd,
                estimated_latency_ms=settings.openai_estimated_latency_ms,
                quality_score=settings.openai_quality_score,
            )
        )
    providers.append(MockProvider())
    return AppResources(
        database_engine=engine,
        redis=redis,
        providers=tuple(providers),
        openai_client=openai_client,
        provider_circuit_breaker=ProviderCircuitBreaker(
            failure_threshold=settings.provider_circuit_breaker_failure_threshold,
            recovery_seconds=settings.provider_circuit_breaker_recovery_seconds,
        ),
    )
