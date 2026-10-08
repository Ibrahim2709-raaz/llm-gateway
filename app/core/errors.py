from typing import Any, cast

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = structlog.get_logger(__name__)


class GatewayError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        error_type: str = "gateway_error",
        status_code: int = status.HTTP_400_BAD_REQUEST,
        param: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.error_type = error_type
        self.status_code = status_code
        self.param = param
        self.headers = headers


def error_body(
    request: Request,
    *,
    message: str,
    error_type: str,
    code: str,
    param: str | None = None,
) -> dict[str, Any]:
    return {
        "error": {
            "message": message,
            "type": error_type,
            "param": param,
            "code": code,
            "request_id": request.state.request_id,
        }
    }


async def gateway_error_handler(request: Request, exc: Exception) -> JSONResponse:
    gateway_error = cast(GatewayError, exc)
    logger.warning(
        "gateway_request_rejected",
        error_code=gateway_error.code,
        status_code=gateway_error.status_code,
    )
    return JSONResponse(
        status_code=gateway_error.status_code,
        content=error_body(
            request,
            message=gateway_error.message,
            error_type=gateway_error.error_type,
            code=gateway_error.code,
            param=gateway_error.param,
        ),
        headers=gateway_error.headers,
    )


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    validation_error = cast(RequestValidationError, exc)
    first_error = validation_error.errors()[0] if validation_error.errors() else {}
    location = first_error.get("loc", ())
    param = ".".join(str(part) for part in location if part != "body") or None
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=error_body(
            request,
            message="Request validation failed.",
            error_type="invalid_request_error",
            code="validation_error",
            param=param,
        ),
    )


def register_exception_handlers(application: FastAPI) -> None:
    application.add_exception_handler(GatewayError, gateway_error_handler)
    application.add_exception_handler(RequestValidationError, validation_error_handler)
