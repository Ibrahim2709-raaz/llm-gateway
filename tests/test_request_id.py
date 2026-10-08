from uuid import UUID, uuid4

from fastapi.testclient import TestClient


def test_gateway_generates_request_id(client: TestClient) -> None:
    response = client.get("/health/live")

    UUID(response.headers["x-request-id"])


def test_gateway_preserves_valid_request_id(client: TestClient) -> None:
    request_id = str(uuid4())

    response = client.get("/health/live", headers={"X-Request-ID": request_id})

    assert response.headers["x-request-id"] == request_id


def test_gateway_replaces_invalid_request_id(client: TestClient) -> None:
    response = client.get("/health/live", headers={"X-Request-ID": "not-a-uuid"})

    generated = response.headers["x-request-id"]
    UUID(generated)
    assert generated != "not-a-uuid"

