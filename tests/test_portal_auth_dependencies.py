from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import Request

from app.core.config import Settings
from app.core.errors import GatewayError
from app.db.models import BrowserSession, Organization, OrganizationMembership, User
from app.identity.dependencies import (
    authenticate_portal_session,
    require_portal_csrf,
)
from app.identity.sessions import AuthenticatedBrowserSession, BrowserAuthenticationError
from app.identity.tokens import hash_opaque_token

NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)


def request_with_cookie(value: str = "raw-session") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v1/portal/auth/me",
            "headers": [(b"cookie", f"llm_gateway_session={value}".encode())],
        }
    )


def authentication_graph() -> tuple[
    AuthenticatedBrowserSession, OrganizationMembership, Organization
]:
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
        role="owner",
        status="active",
    )
    stored = BrowserSession(
        id=uuid4(),
        user_id=user.id,
        active_organization_id=organization.id,
        token_prefix="portal_session_example",
        token_hash="session-hash",
        csrf_token_hash=hash_opaque_token("csrf-value"),
        created_at=NOW,
        expires_at=NOW + timedelta(days=1),
    )
    return AuthenticatedBrowserSession(session=stored, user=user), membership, organization


@pytest.mark.anyio
async def test_portal_session_builds_tenant_scoped_principal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authentication, membership, organization = authentication_graph()
    monkeypatch.setattr(
        "app.identity.dependencies.authenticate_browser_session",
        AsyncMock(return_value=authentication),
    )
    result = MagicMock()
    result.one_or_none.return_value = (membership, organization)
    database = MagicMock()
    database.execute = AsyncMock(return_value=result)

    identity = await authenticate_portal_session(
        request_with_cookie(), database, Settings(_env_file=None, app_env="test")
    )
    assert identity.principal.organization_id == organization.id
    assert identity.principal.organization_role == "owner"
    assert identity.authentication.user.id == membership.user_id


@pytest.mark.anyio
async def test_portal_session_maps_authentication_failure_to_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.identity.dependencies.authenticate_browser_session",
        AsyncMock(side_effect=BrowserAuthenticationError("invalid")),
    )
    with pytest.raises(GatewayError) as error:
        await authenticate_portal_session(
            request_with_cookie("bad"), MagicMock(), Settings(_env_file=None, app_env="test")
        )
    assert error.value.status_code == 401
    assert error.value.code == "invalid_portal_session"


@pytest.mark.anyio
async def test_csrf_dependency_accepts_only_bound_token() -> None:
    authentication, membership, organization = authentication_graph()
    identity = MagicMock(
        authentication=authentication,
        membership=membership,
        organization=organization,
    )
    assert await require_portal_csrf(identity, "csrf-value") is identity
    with pytest.raises(GatewayError) as error:
        await require_portal_csrf(identity, "different-value")
    assert error.value.status_code == 403
    assert error.value.code == "invalid_csrf_token"
