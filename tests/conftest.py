import os
from uuid import UUID

os.environ["APP_ENV"] = "test"

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import AuthenticatedClient, authenticate_api_key
from app.main import app


@pytest.fixture
def client() -> TestClient:
    async def authenticated_client() -> AuthenticatedClient:
        return AuthenticatedClient(
            client_id=UUID("d8a3f20b-53c6-44b7-881c-2ad452e0304e"),
            api_key_id=UUID("c0d0ee4e-0233-4868-b55d-e657624ae797"),
            name="test-client",
        )

    app.dependency_overrides[authenticate_api_key] = authenticated_client
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(authenticate_api_key, None)
