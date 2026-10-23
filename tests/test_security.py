from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.main as main_module
from app.core.config import Settings


def test_gateway_adds_security_headers(client: TestClient) -> None:
    response = client.get("/health/live")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"
    assert response.headers["cache-control"] == "no-store"
    assert "strict-transport-security" not in response.headers


def test_oversized_request_is_rejected_before_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None, max_request_body_bytes=1_024)
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)

    with TestClient(main_module.create_app()) as client:
        response = client.post(
            "/v1/chat/completions",
            content=b"x" * 1_025,
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]
    UUID(response.headers["x-request-id"])


def test_untrusted_host_is_rejected_with_gateway_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None, trusted_hosts="api.example.com")
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)

    with TestClient(main_module.create_app(), base_url="http://api.example.com") as client:
        response = client.get("/health/live", headers={"host": "attacker.example"})

    assert response.status_code == 400
    assert response.headers["x-content-type-options"] == "nosniff"
    UUID(response.headers["x-request-id"])


def test_production_disables_api_docs_and_enables_hsts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        gateway_api_key_pepper="a-long-production-secret",
        trusted_hosts="api.example.com",
    )
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)

    with TestClient(main_module.create_app(), base_url="https://api.example.com") as client:
        docs_response = client.get("/docs")
        openapi_response = client.get("/openapi.json")

    assert docs_response.status_code == 404
    assert openapi_response.status_code == 404
    assert (
        docs_response.headers["strict-transport-security"]
        == "max-age=31536000; includeSubDomains"
    )


def test_production_rejects_wildcard_trusted_hosts() -> None:
    with pytest.raises(ValidationError, match="TRUSTED_HOSTS"):
        Settings(
            _env_file=None,
            app_env="production",
            gateway_api_key_pepper="a-long-production-secret",
            trusted_hosts="*",
        )
