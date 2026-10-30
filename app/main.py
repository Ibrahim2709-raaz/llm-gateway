from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.router import api_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.resources import close_resources, create_resources
from app.middleware.request_id import RequestIdMiddleware
from app.middleware.security import RequestBodyLimitMiddleware, SecurityHeadersMiddleware


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
        await close_resources(resources)
        logger.info("gateway_stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Multi-provider LLM gateway",
        docs_url="/docs" if settings.app_env != "production" else None,
        redoc_url="/redoc" if settings.app_env != "production" else None,
        openapi_url="/openapi.json" if settings.app_env != "production" else None,
        lifespan=lifespan,
    )
    application.add_middleware(
        RequestBodyLimitMiddleware,
        max_body_bytes=settings.max_request_body_bytes,
    )
    application.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=settings.trusted_host_list,
    )
    application.add_middleware(RequestIdMiddleware)
    application.add_middleware(
        SecurityHeadersMiddleware,
        include_hsts=settings.app_env == "production",
    )
    application.include_router(api_router)
    portal_directory = Path(__file__).parent / "static" / "portal"
    application.mount(
        "/portal",
        StaticFiles(directory=portal_directory, html=True),
        name="portal",
    )
    register_exception_handlers(application)
    return application


app = create_app()
