from typing import Annotated

import structlog
from fastapi import Depends, Request, status

from app.auth.dependencies import AuthenticatedClient, authenticate_api_key
from app.core.config import Settings, get_settings
from app.core.errors import GatewayError
from app.observability.metrics import METRICS
from app.rate_limits.service import RateLimitBackendError, RateLimitStatus, RedisRateLimiter

logger = structlog.get_logger(__name__)


async def enforce_request_rate_limit(
    request: Request,
    client: Annotated[AuthenticatedClient, Depends(authenticate_api_key)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RateLimitStatus:
    limiter = RedisRateLimiter(
        request.app.state.resources.redis,
        limit=settings.request_rate_limit,
        window_seconds=settings.request_rate_limit_window_seconds,
    )
    try:
        rate_limit = await limiter.consume(client.client_id)
    except RateLimitBackendError as error:
        METRICS.record_rate_limit("backend_error")
        logger.exception("rate_limit_backend_unavailable", client_id=str(client.client_id))
        raise GatewayError(
            "The request rate limiter is temporarily unavailable.",
            code="rate_limit_unavailable",
            error_type="service_unavailable_error",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from error

    if not rate_limit.allowed:
        METRICS.record_rate_limit("rejected")
        raise GatewayError(
            "Request rate limit exceeded.",
            code="rate_limit_exceeded",
            error_type="rate_limit_error",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers=rate_limit.headers(include_retry_after=True),
        )
    METRICS.record_rate_limit("allowed")
    return rate_limit
