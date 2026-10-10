from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import Settings


@dataclass(frozen=True, slots=True)
class AppResources:
    database_engine: AsyncEngine
    redis: Redis


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
    return AppResources(database_engine=engine, redis=redis)
