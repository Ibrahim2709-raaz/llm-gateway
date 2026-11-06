from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import Request, Response
from fastapi.testclient import TestClient

from app.api.routes.portal_playground import (
    create_playground_completion,
    preview_playground_request,
)
from app.core.config import Settings
from app.core.errors import GatewayError
from app.db.models import BrowserSession, Organization, OrganizationMembership, User
from app.identity.authorization import Principal
from app.identity.dependencies import BrowserIdentity
from app.identity.sessions import AuthenticatedBrowserSession
from app.main import create_app
from app.providers.mock import MockProvider
from app.rate_limits.service import RateLimitStatus
from app.schemas.chat import ChatCompletionRequest
from app.services.chat import ChatCompletionService

NOW = datetime(2026, 11, 5, 12, tzinfo=UTC)


def browser_identity(*, role: str = "owner") -> BrowserIdentity:
    user = User(
        id=uuid4(),
        email_normalized="owner@example.com",
        display_name="Owner",
        status="active",
        email_verified_at=NOW,
    )
    organization = Organization(
        id=uuid4(),
        client_id=uuid4(),
        slug="example-team",
        name="Example Team",
        status="active",
    )
    membership = OrganizationMembership(
        id=uuid4(),
        organization_id=organization.id,
        user_id=user.id,
        role=role,
        status="active",
    )
    session = BrowserSession(
        id=uuid4(),
        user_id=user.id,
        active_organization_id=organization.id,
        token_prefix="portal_session_example",
        token_hash="session-hash",
        csrf_token_hash="csrf-hash",
        created_at=NOW,
        expires_at=NOW + timedelta(days=1),
    )
    return BrowserIdentity(
        authentication=AuthenticatedBrowserSession(session=session, user=user),
        organization=organization,
        membership=membership,
        principal=Principal(
            actor_type="user",
            actor_id=user.id,
            organization_id=organization.id,
            organization_role=role,
        ),
    )


def playground_payload() -> ChatCompletionRequest:
    return ChatCompletionRequest.model_validate(
        {
            "messages": [{"role": "user", "content": "Explain gateway routing."}],
            "max_tokens": 128,
            "routing": {"strategy": "cost", "allowed_providers": ["mock"]},
        }
    )


def request_with_id(request_id: UUID) -> Request:
    resources = SimpleNamespace(redis=object())
    application = SimpleNamespace(state=SimpleNamespace(resources=resources))
    return cast(
        Request,
        SimpleNamespace(
            app=application,
            state=SimpleNamespace(request_id=str(request_id)),
        ),
    )


def test_playground_routes_are_in_openapi_contract() -> None:
    with TestClient(create_app()) as client:
        paths = client.get("/openapi.json").json()["paths"]
    prefix = "/v1/portal/organizations/{organization_id}/playground"
    assert f"{prefix}/preview" in paths
    assert f"{prefix}/completions" in paths


@pytest.mark.anyio
async def test_playground_preview_uses_router_without_provider_call() -> None:
    identity = browser_identity(role="developer")
    provider = MockProvider()
    provider.complete = AsyncMock(wraps=provider.complete)  # type: ignore[method-assign]
    service = ChatCompletionService((provider,))

    result = await preview_playground_request(
        identity.organization.id,
        playground_payload(),
        identity,
        service,
    )

    assert result.selected_provider == "mock"
    assert result.selected_model == "mock-1"
    assert result.routing_strategy == "cost"
    assert result.estimated_cost_usd == 0
    assert result.eligible_candidate_count == 1
    provider.complete.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.anyio
async def test_playground_completion_reuses_rate_limit_and_chat_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = browser_identity()
    request_id = uuid4()
    request = request_with_id(request_id)
    response = Response()
    allowed = RateLimitStatus(
        limit=60,
        remaining=59,
        reset_after_seconds=60,
        reset_at=1_800_000_000,
        allowed=True,
    )
    limiter = AsyncMock(return_value=allowed)
    monkeypatch.setattr(
        "app.api.routes.portal_playground.consume_client_rate_limit",
        limiter,
    )

    result = await create_playground_completion(
        identity.organization.id,
        playground_payload(),
        request,
        response,
        identity,
        ChatCompletionService((MockProvider(),)),
        Settings(_env_file=None, app_env="test"),
    )

    assert result.gateway.request_id == request_id
    assert result.gateway.provider == "mock"
    assert result.choices[0].message.content.startswith("Mock response:")
    assert response.headers["x-ratelimit-remaining"] == "59"
    limiter.assert_awaited_once()
    assert limiter.await_args.kwargs["client_id"] == identity.organization.client_id


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("role", "organization_id", "expected_status"),
    [
        ("billing_viewer", None, 403),
        ("owner", uuid4(), 404),
    ],
)
async def test_playground_rejects_insufficient_or_cross_organization_access(
    role: str,
    organization_id: UUID | None,
    expected_status: int,
) -> None:
    identity = browser_identity(role=role)
    target = organization_id or identity.organization.id

    with pytest.raises(GatewayError) as error:
        await preview_playground_request(
            target,
            playground_payload(),
            identity,
            ChatCompletionService((MockProvider(),)),
        )

    assert error.value.status_code == expected_status
