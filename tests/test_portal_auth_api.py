from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import Response
from fastapi.testclient import TestClient

from app.api.routes.portal_auth import (
    activate_organization,
    current_account,
    login,
    logout,
    register_account,
)
from app.core.config import Settings
from app.core.errors import GatewayError
from app.db.models import BrowserSession, Organization, OrganizationMembership, User
from app.identity.accounts import AccountLogin, RegisteredAccount
from app.identity.authorization import Principal
from app.identity.dependencies import BrowserIdentity
from app.identity.passwords import ScryptPasswordHasher
from app.identity.sessions import AuthenticatedBrowserSession, IssuedBrowserSession
from app.identity.tokens import issue_opaque_token
from app.main import create_app
from app.schemas.portal_auth import LoginRequest, RegisterAccountRequest

NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)


def identity_graph() -> tuple[User, Organization, OrganizationMembership]:
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
    return user, organization, membership


def browser_identity() -> BrowserIdentity:
    user, organization, membership = identity_graph()
    stored = BrowserSession(
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
        authentication=AuthenticatedBrowserSession(session=stored, user=user),
        organization=organization,
        membership=membership,
        principal=Principal(
            actor_type="user",
            actor_id=user.id,
            organization_id=organization.id,
            organization_role="owner",
        ),
    )


def test_portal_auth_routes_are_in_openapi_contract() -> None:
    with TestClient(create_app()) as client:
        openapi = client.get("/openapi.json").json()
    paths = openapi["paths"]
    assert "/v1/portal/auth/register" in paths
    assert "/v1/portal/auth/verify-email" in paths
    assert "/v1/portal/auth/login" in paths
    assert "/v1/portal/auth/logout" in paths
    assert "/v1/portal/auth/me" in paths


@pytest.mark.anyio
async def test_register_returns_development_verification_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, organization, membership = identity_graph()
    secret = issue_opaque_token(namespace="email_verification")
    verification = MagicMock(value=secret.value)
    registered = RegisteredAccount(
        client=MagicMock(id=organization.client_id),
        organization=organization,
        user=user,
        membership=membership,
        verification=verification,
    )
    operation = AsyncMock(return_value=registered)
    monkeypatch.setattr("app.api.routes.portal_auth.register_organization_owner", operation)
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()

    result = await register_account(
        RegisterAccountRequest(
            email="owner@example.com",
            display_name="Owner",
            password="a-long-enough-password",
            organization_name="Example Team",
            organization_slug="example-team",
        ),
        database,
        Settings(_env_file=None, app_env="test"),
        ScryptPasswordHasher(log_n=14),
    )
    assert result.verification_token == secret.value
    database.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_registration_is_disabled_without_token_delivery() -> None:
    database = MagicMock()
    with pytest.raises(GatewayError) as error:
        await register_account(
            RegisterAccountRequest(
                email="owner@example.com",
                display_name="Owner",
                password="a-long-enough-password",
                organization_name="Example Team",
                organization_slug="example-team",
            ),
            database,
            Settings(_env_file=None, app_env="test", portal_action_token_delivery="disabled"),
            ScryptPasswordHasher(log_n=14),
        )
    assert error.value.status_code == 503


@pytest.mark.anyio
async def test_login_sets_http_only_session_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    user, organization, membership = identity_graph()
    account = AccountLogin(user=user, organization=organization, membership=membership)
    monkeypatch.setattr(
        "app.api.routes.portal_auth.authenticate_account", AsyncMock(return_value=account)
    )
    secret = issue_opaque_token(namespace="portal_session")
    csrf = issue_opaque_token(namespace="csrf")
    stored = BrowserSession(
        id=uuid4(),
        user_id=user.id,
        active_organization_id=organization.id,
        token_prefix=secret.prefix,
        token_hash=secret.hash,
        csrf_token_hash=csrf.hash,
        created_at=NOW,
        expires_at=NOW + timedelta(days=7),
    )
    issued = IssuedBrowserSession(stored, secret.value, csrf.value)
    monkeypatch.setattr(
        "app.api.routes.portal_auth.issue_browser_session", AsyncMock(return_value=issued)
    )
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()
    response = Response()

    result = await login(
        LoginRequest(email=user.email_normalized, password="a-long-enough-password"),
        response,
        database,
        Settings(_env_file=None, app_env="test"),
        ScryptPasswordHasher(log_n=14),
    )
    cookie = response.headers["set-cookie"]
    assert "llm_gateway_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert result.csrf_token == csrf.value
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.anyio
async def test_logout_revokes_session_and_clears_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    identity = browser_identity()
    revoke = AsyncMock()
    monkeypatch.setattr("app.api.routes.portal_auth.revoke_browser_session", revoke)
    database = MagicMock()
    database.commit = AsyncMock()
    response = Response()

    result = await logout(response, identity, database)
    assert result.message == "Signed out."
    revoke.assert_awaited_once_with(database, stored=identity.authentication.session)
    assert "llm_gateway_session=" in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"]


@pytest.mark.anyio
async def test_current_account_lists_only_service_result(monkeypatch: pytest.MonkeyPatch) -> None:
    identity = browser_identity()
    listing = AsyncMock(return_value=[(identity.membership, identity.organization)])
    monkeypatch.setattr("app.api.routes.portal_auth.list_active_memberships", listing)
    result = await current_account(identity, MagicMock())
    assert result.active_organization_id == identity.organization.id
    assert [item.id for item in result.organizations] == [identity.organization.id]


@pytest.mark.anyio
async def test_activate_organization_rejects_cross_organization_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = browser_identity()
    monkeypatch.setattr(
        "app.api.routes.portal_auth.list_active_memberships",
        AsyncMock(return_value=[(identity.membership, identity.organization)]),
    )
    database = MagicMock()
    database.commit = AsyncMock()
    with pytest.raises(GatewayError) as error:
        await activate_organization(uuid4(), identity, database)
    assert error.value.status_code == 404
    database.commit.assert_not_awaited()
