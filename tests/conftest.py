import os
from uuid import UUID

os.environ["APP_ENV"] = "test"

import pytest
from fastapi.testclient import TestClient

from app.api.routes.chat import get_chat_service
from app.auth.dependencies import AuthenticatedClient, authenticate_api_key
from app.main import app
from app.providers.mock import MockProvider
from app.rate_limits.dependencies import enforce_request_rate_limit
from app.rate_limits.service import RateLimitStatus
from app.services.chat import ChatCompletionService


@pytest.fixture
def client() -> TestClient:
    async def authenticated_client() -> AuthenticatedClient:
        return AuthenticatedClient(
            client_id=UUID("d8a3f20b-53c6-44b7-881c-2ad452e0304e"),
            api_key_id=UUID("c0d0ee4e-0233-4868-b55d-e657624ae797"),
            name="test-client",
        )

    async def allowed_request() -> RateLimitStatus:
        return RateLimitStatus(
            limit=60,
            remaining=59,
            reset_after_seconds=60,
            reset_at=1_790_707_260,
            allowed=True,
        )

    def chat_service() -> ChatCompletionService:
        return ChatCompletionService((MockProvider(),))

    app.dependency_overrides[authenticate_api_key] = authenticated_client
    app.dependency_overrides[enforce_request_rate_limit] = allowed_request
    app.dependency_overrides[get_chat_service] = chat_service
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(authenticate_api_key, None)
        app.dependency_overrides.pop(enforce_request_rate_limit, None)
        app.dependency_overrides.pop(get_chat_service, None)
