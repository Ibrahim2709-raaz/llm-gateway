import hashlib
from typing import Annotated

import structlog
from fastapi import Depends, Request, status

from app.core.config import Settings, get_settings
from app.core.errors import GatewayError
from app.observability.metrics import METRICS
from app.rate_limits.service import RateLimitBackendError, RateLimitStatus, RedisRateLimiter

logger = structlog.get_logger(__name__)


async def enforce_portal_email_rate_limit(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> RateLimitStatus:
    source = request.client.host if request.client is not None else "unknown"
    source_digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    limiter = RedisRateLimiter(
        request.app.state.resources.redis,
        limit=settings.portal_email_rate_limit,
        window_seconds=settings.portal_email_rate_limit_window_seconds,
    )
    try:
        decision = await limiter.consume_identifier(f"portal_email:{source_digest}")
    except RateLimitBackendError as exc:
        METRICS.record_rate_limit("backend_error")
        logger.exception("portal_email_rate_limit_backend_unavailable")
        raise GatewayError(
            "Email requests are temporarily unavailable.",
            code="rate_limit_unavailable",
            error_type="service_unavailable_error",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    if not decision.allowed:
        METRICS.record_rate_limit("rejected")
        raise GatewayError(
            "Too many email requests. Try again later.",
            code="rate_limit_exceeded",
            error_type="rate_limit_error",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers=decision.headers(include_retry_after=True),
        )
    METRICS.record_rate_limit("allowed")
    return decision
