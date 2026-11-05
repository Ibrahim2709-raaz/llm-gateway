from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import Response
from fastapi.testclient import TestClient

from app.api.routes.portal_members import (
    accept_new_user_invitation,
    create_invitation,
    list_members,
)
from app.core.config import Settings
from app.core.errors import GatewayError
from app.db.models import BrowserSession, Organization, OrganizationMembership, User
from app.identity.authorization import Principal
from app.identity.dependencies import BrowserIdentity
from app.identity.members import (
    AcceptedNewUserInvitation,
    IssuedOrganizationInvitation,
    OrganizationMember,
)
from app.identity.sessions import AuthenticatedBrowserSession, IssuedBrowserSession
from app.main import create_app
from app.schemas.portal_members import AcceptNewUserInvitationRequest, CreateInvitationRequest

NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)


def browser_identity(role: str = "owner") -> BrowserIdentity:
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
    stored = BrowserSession(
        id=uuid4(),
        user_id=user.id,
        active_organization_id=organization.id,
        token_prefix="prefix",
        token_hash="hash",
        csrf_token_hash="csrf",
        created_at=NOW,
        expires_at=NOW + timedelta(days=1),
    )
    return BrowserIdentity(
        authentication=AuthenticatedBrowserSession(stored, user),
        organization=organization,
        membership=membership,
        principal=Principal(
            actor_type="user",
            actor_id=user.id,
            organization_id=organization.id,
            organization_role=role,
        ),
    )


def test_member_routes_are_in_openapi_contract() -> None:
    with TestClient(create_app()) as client:
        paths = client.get("/openapi.json").json()["paths"]
    assert "/v1/portal/organizations/{organization_id}/members" in paths
    assert "/v1/portal/organizations/{organization_id}/invitations" in paths
    assert "/v1/portal/invitations/accept" in paths
    assert "/v1/portal/invitations/accept-new" in paths


@pytest.mark.anyio
async def test_list_members_serializes_only_requested_service_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = browser_identity()
    service_result = [OrganizationMember(identity.membership, identity.authentication.user)]
    monkeypatch.setattr(
        "app.api.routes.portal_members.list_organization_members",
        AsyncMock(return_value=service_result),
    )
    response = await list_members(identity.organization.id, identity, MagicMock())
    assert len(response.data) == 1
    assert response.data[0].membership_id == identity.membership.id


@pytest.mark.anyio
async def test_create_invitation_returns_token_only_in_delivery_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = browser_identity()
    invitation = MagicMock(
        id=uuid4(),
        email_normalized="member@example.com",
        role="developer",
        expires_at=NOW + timedelta(days=7),
    )
    issued = IssuedOrganizationInvitation(invitation=invitation, value="raw-invitation-token")
    monkeypatch.setattr(
        "app.api.routes.portal_members.issue_organization_invitation",
        AsyncMock(return_value=issued),
    )
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()
    response = await create_invitation(
        identity.organization.id,
        CreateInvitationRequest(email="member@example.com", role="developer"),
        identity,
        database,
        Settings(_env_file=None, app_env="test"),
    )
    assert response.invitation_token == "raw-invitation-token"
    database.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_create_invitation_fails_closed_without_delivery() -> None:
    identity = browser_identity()
    with pytest.raises(GatewayError) as error:
        await create_invitation(
            identity.organization.id,
            CreateInvitationRequest(email="member@example.com", role="developer"),
            identity,
            MagicMock(),
            Settings(
                _env_file=None,
                app_env="test",
                portal_action_token_delivery="disabled",
            ),
        )
    assert error.value.status_code == 503


@pytest.mark.anyio
async def test_new_user_invitation_creates_portal_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = browser_identity(role="developer")
    accepted = AcceptedNewUserInvitation(
        user=identity.authentication.user,
        organization=identity.organization,
        membership=identity.membership,
    )
    stored_session = identity.authentication.session
    issued = IssuedBrowserSession(
        session=stored_session,
        session_token="portal-session-secret",
        csrf_token="csrf-secret",
    )
    accept_service = AsyncMock(return_value=accepted)
    issue_session = AsyncMock(return_value=issued)
    monkeypatch.setattr(
        "app.api.routes.portal_members.accept_invitation_for_new_user",
        accept_service,
    )
    monkeypatch.setattr(
        "app.api.routes.portal_members.issue_browser_session",
        issue_session,
    )
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()
    response = Response()

    payload = await accept_new_user_invitation(
        AcceptNewUserInvitationRequest(
            token="organization_invitation_secret",
            display_name="New Member",
            password="a sufficiently long password",
        ),
        response,
        database,
        Settings(_env_file=None, app_env="test"),
        MagicMock(),
    )

    assert payload.active_organization.id == identity.organization.id
    assert payload.csrf_token == "csrf-secret"
    assert "llm_gateway_session=portal-session-secret" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    database.commit.assert_awaited_once()
