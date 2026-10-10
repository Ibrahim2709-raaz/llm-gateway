import asyncio
from collections.abc import Awaitable
from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, ConfigDict
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import get_settings

router = APIRouter(prefix="/health", tags=["Health"])


class LivenessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"


class ReadinessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "unavailable"]
    checks: dict[str, Literal["ok", "unavailable"]]


@router.get("/live", response_model=LivenessResponse, operation_id="getLiveness")
async def liveness() -> LivenessResponse:
    return LivenessResponse()


async def _within_timeout(check: Awaitable[object], limit_seconds: float) -> bool:
    try:
        await asyncio.wait_for(check, timeout=limit_seconds)
    except Exception:
        return False
    return True


async def database_is_available(engine: AsyncEngine, limit_seconds: float) -> bool:
    async def check() -> None:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    return await _within_timeout(check(), limit_seconds)


async def redis_is_available(redis: Redis, limit_seconds: float) -> bool:
    return await _within_timeout(redis.ping(), limit_seconds)


@router.get("/ready", response_model=ReadinessResponse, operation_id="getReadiness")
async def readiness(request: Request, response: Response) -> ReadinessResponse:
    resources = request.app.state.resources
    timeout = get_settings().dependency_check_timeout_seconds
    database_ready, redis_ready = await asyncio.gather(
        database_is_available(resources.database_engine, timeout),
        redis_is_available(resources.redis, timeout),
    )
    checks: dict[str, Literal["ok", "unavailable"]] = {
        "postgresql": "ok" if database_ready else "unavailable",
        "redis": "ok" if redis_ready else "unavailable",
    }
    if not all((database_ready, redis_ready)):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(status="unavailable", checks=checks)
    return ReadinessResponse(status="ready", checks=checks)
