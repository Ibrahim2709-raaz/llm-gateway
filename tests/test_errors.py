from typing import Annotated

from fastapi import Query
from fastapi.testclient import TestClient

from app.core.errors import GatewayError
from app.main import create_app


def test_gateway_error_uses_standard_envelope() -> None:
    application = create_app()

    @application.get("/_test/rate-limit")
    async def raise_rate_limit() -> None:
        raise GatewayError(
            "Request rate limit exceeded.",
            code="rate_limit_exceeded",
            error_type="rate_limit_error",
            status_code=429,
            headers={"Retry-After": "30"},
        )

    with TestClient(application) as client:
        response = client.get("/_test/rate-limit")

    assert response.status_code == 429
    assert response.headers["retry-after"] == "30"
    assert response.json() == {
        "error": {
            "message": "Request rate limit exceeded.",
            "type": "rate_limit_error",
            "param": None,
            "code": "rate_limit_exceeded",
            "request_id": response.headers["x-request-id"],
        }
    }


def test_validation_error_uses_standard_envelope() -> None:
    application = create_app()

    @application.get("/_test/validated")
    async def validated(limit: Annotated[int, Query(ge=1)]) -> dict[str, int]:
        return {"limit": limit}

    with TestClient(application) as client:
        response = client.get("/_test/validated", params={"limit": 0})

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "message": "Request validation failed.",
            "type": "invalid_request_error",
            "param": "query.limit",
            "code": "validation_error",
            "request_id": response.headers["x-request-id"],
        }
    }

