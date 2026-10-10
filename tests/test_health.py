from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from app.api.routes import health


def test_liveness(client: TestClient) -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-request-id"]


def test_readiness_when_dependencies_are_available(
    client: TestClient, monkeypatch: MonkeyPatch
) -> None:
    async def available(*_: object) -> bool:
        return True

    monkeypatch.setattr(health, "database_is_available", available)
    monkeypatch.setattr(health, "redis_is_available", available)

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"postgresql": "ok", "redis": "ok"},
    }


def test_readiness_when_a_dependency_is_unavailable(
    client: TestClient, monkeypatch: MonkeyPatch
) -> None:
    async def database_available(*_: object) -> bool:
        return True

    async def redis_unavailable(*_: object) -> bool:
        return False

    monkeypatch.setattr(health, "database_is_available", database_available)
    monkeypatch.setattr(health, "redis_is_available", redis_unavailable)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "checks": {"postgresql": "ok", "redis": "unavailable"},
    }


def test_openapi_document_is_available(client: TestClient) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["title"] == "LLM Gateway"
