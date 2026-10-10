from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.resources import create_resources
from app.middleware.request_id import RequestIdMiddleware


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logger = structlog.get_logger(__name__)
    resources = create_resources(settings)
    application.state.resources = resources
    logger.info("gateway_started", environment=settings.app_env)
    try:
        yield
    finally:
        await resources.redis.aclose()
        await resources.database_engine.dispose()
        logger.info("gateway_stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Multi-provider LLM gateway",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    application.add_middleware(RequestIdMiddleware)
    application.include_router(api_router)
    register_exception_handlers(application)
    return application


app = create_app()
