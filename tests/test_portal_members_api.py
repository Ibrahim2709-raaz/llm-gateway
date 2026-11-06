from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import Response
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.api.routes.portal_members import (
    accept_invitation,
    accept_new_user_invitation,
    create_invitation,
    list_members,
    update_member,
)
from app.core.config import Settings
from app.core.errors import GatewayError
from app.db.models import BrowserSession, Organization, OrganizationMembership, User
from app.identity.accounts import AccountConflictError
from app.identity.authorization import AuthorizationError, Principal
from app.identity.dependencies import BrowserIdentity
from app.identity.email_delivery import EmailDeliveryError
from app.identity.members import (
    AcceptedNewUserInvitation,
    InvitationError,
    IssuedOrganizationInvitation,
    MembershipConflictError,
    OrganizationMember,
)
from app.identity.sessions import AuthenticatedBrowserSession, IssuedBrowserSession
from app.main import create_app
from app.schemas.portal_members import (
    AcceptInvitationRequest,
    AcceptNewUserInvitationRequest,
    CreateInvitationRequest,
    UpdateOrganizationMemberRequest,
)

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
async def test_create_invitation_sends_email_without_returning_token(
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
    delivery = MagicMock()
    delivery.send_organization_invitation = AsyncMock()
    monkeypatch.setattr(
        "app.api.routes.portal_members.get_portal_email_delivery",
        MagicMock(return_value=delivery),
    )
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()
    settings = Settings(
        _env_file=None,
        app_env="test",
        portal_action_token_delivery="smtp",
        portal_public_base_url="https://gateway.example.com",
        smtp_host="smtp.example.com",
        smtp_from_email="gateway@example.com",
    )

    result = await create_invitation(
        identity.organization.id,
        CreateInvitationRequest(email="member@example.com", role="developer"),
        identity,
        database,
        settings,
    )

    assert result.invitation_token is None
    delivery.send_organization_invitation.assert_awaited_once_with(
        recipient="member@example.com",
        organization_name=identity.organization.name,
        token="raw-invitation-token",
    )


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


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("authorization_error", "expected_status"),
    [
        (AuthorizationError("cross_organization_access", "wrong organization"), 404),
        (AuthorizationError("permission_denied", "owner required"), 403),
    ],
)
async def test_list_members_maps_authorization_failures(
    monkeypatch: pytest.MonkeyPatch,
    authorization_error: AuthorizationError,
    expected_status: int,
) -> None:
    identity = browser_identity()
    monkeypatch.setattr(
        "app.api.routes.portal_members.list_organization_members",
        AsyncMock(side_effect=authorization_error),
    )

    with pytest.raises(GatewayError) as error:
        await list_members(identity.organization.id, identity, MagicMock())

    assert error.value.status_code == expected_status


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("failure", "expected_status"),
    [
        (MembershipConflictError("already active"), 409),
        (ValueError("invalid role"), 422),
        (IntegrityError("insert", {}, RuntimeError("conflict")), 422),
    ],
)
async def test_create_invitation_maps_service_failures(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected_status: int,
) -> None:
    identity = browser_identity()
    monkeypatch.setattr(
        "app.api.routes.portal_members.issue_organization_invitation",
        AsyncMock(side_effect=failure),
    )
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()

    with pytest.raises(GatewayError) as error:
        await create_invitation(
            identity.organization.id,
            CreateInvitationRequest(email="member@example.com", role="developer"),
            identity,
            database,
            Settings(_env_file=None, app_env="test"),
        )

    assert error.value.status_code == expected_status
    database.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_create_invitation_reports_smtp_failure_after_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = browser_identity()
    invitation = MagicMock(
        id=uuid4(),
        email_normalized="member@example.com",
        role="developer",
        expires_at=NOW + timedelta(days=7),
    )
    monkeypatch.setattr(
        "app.api.routes.portal_members.issue_organization_invitation",
        AsyncMock(
            return_value=IssuedOrganizationInvitation(
                invitation=invitation,
                value="raw-invitation-token",
            )
        ),
    )
    delivery = MagicMock()
    delivery.send_organization_invitation = AsyncMock(
        side_effect=EmailDeliveryError("SMTP delivery failed")
    )
    monkeypatch.setattr(
        "app.api.routes.portal_members.get_portal_email_delivery",
        MagicMock(return_value=delivery),
    )
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()
    settings = Settings(
        _env_file=None,
        app_env="test",
        portal_action_token_delivery="smtp",
        portal_public_base_url="https://gateway.example.com",
        smtp_host="smtp.example.com",
        smtp_from_email="gateway@example.com",
    )

    with pytest.raises(GatewayError) as error:
        await create_invitation(
            identity.organization.id,
            CreateInvitationRequest(email="member@example.com", role="developer"),
            identity,
            database,
            settings,
        )

    assert error.value.status_code == 503
    assert error.value.code == "email_delivery_failed"
    database.commit.assert_awaited_once()
    database.rollback.assert_not_awaited()


@pytest.mark.anyio
async def test_existing_user_accepts_invitation_and_switches_organization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = browser_identity()
    target_membership = OrganizationMembership(
        id=uuid4(),
        organization_id=uuid4(),
        user_id=identity.authentication.user.id,
        role="developer",
        status="active",
    )
    monkeypatch.setattr(
        "app.api.routes.portal_members.accept_organization_invitation",
        AsyncMock(return_value=target_membership),
    )
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()

    result = await accept_invitation(
        AcceptInvitationRequest(token="organization_invitation_secret"),
        identity,
        database,
    )

    assert result.membership_id == target_membership.id
    assert identity.authentication.session.active_organization_id == (
        target_membership.organization_id
    )
    database.commit.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("failure", "expected_status"),
    [
        (InvitationError("expired"), 400),
        (IntegrityError("insert", {}, RuntimeError("conflict")), 409),
    ],
)
async def test_existing_user_invitation_maps_failures(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected_status: int,
) -> None:
    identity = browser_identity()
    monkeypatch.setattr(
        "app.api.routes.portal_members.accept_organization_invitation",
        AsyncMock(side_effect=failure),
    )
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()

    with pytest.raises(GatewayError) as error:
        await accept_invitation(
            AcceptInvitationRequest(token="organization_invitation_secret"),
            identity,
            database,
        )

    assert error.value.status_code == expected_status
    database.rollback.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("failure", "expected_status", "expected_code"),
    [
        (InvitationError("expired"), 400, "invalid_invitation"),
        (AccountConflictError("exists"), 409, "account_exists"),
        (ValueError("invalid"), 422, "invalid_invitation_account"),
    ],
)
async def test_new_user_invitation_maps_failures(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected_status: int,
    expected_code: str,
) -> None:
    monkeypatch.setattr(
        "app.api.routes.portal_members.accept_invitation_for_new_user",
        AsyncMock(side_effect=failure),
    )
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()

    with pytest.raises(GatewayError) as error:
        await accept_new_user_invitation(
            AcceptNewUserInvitationRequest(
                token="organization_invitation_secret",
                display_name="New Member",
                password="a sufficiently long password",
            ),
            Response(),
            database,
            Settings(_env_file=None, app_env="test"),
            MagicMock(),
        )

    assert error.value.status_code == expected_status
    assert error.value.code == expected_code
    database.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_update_member_returns_updated_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = browser_identity()
    target = OrganizationMembership(
        id=uuid4(),
        organization_id=identity.organization.id,
        user_id=identity.authentication.user.id,
        role="billing_viewer",
        status="active",
    )
    monkeypatch.setattr(
        "app.api.routes.portal_members.update_organization_member",
        AsyncMock(return_value=target),
    )
    database = MagicMock()
    database.get = AsyncMock(return_value=identity.authentication.user)
    database.commit = AsyncMock()
    database.rollback = AsyncMock()

    result = await update_member(
        identity.organization.id,
        target.id,
        UpdateOrganizationMemberRequest(role="billing_viewer", status="active"),
        identity,
        database,
    )

    assert result.membership_id == target.id
    assert result.role == "billing_viewer"
    database.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_update_member_maps_missing_user_and_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = browser_identity()
    target = identity.membership
    operation = AsyncMock(return_value=target)
    monkeypatch.setattr(
        "app.api.routes.portal_members.update_organization_member",
        operation,
    )
    database = MagicMock()
    database.get = AsyncMock(return_value=None)
    database.commit = AsyncMock()
    database.rollback = AsyncMock()

    with pytest.raises(GatewayError) as missing:
        await update_member(
            identity.organization.id,
            target.id,
            UpdateOrganizationMemberRequest(role="developer", status="active"),
            identity,
            database,
        )
    assert missing.value.status_code == 409

    operation.side_effect = AuthorizationError("permission_denied", "owner required")
    with pytest.raises(GatewayError) as forbidden:
        await update_member(
            identity.organization.id,
            target.id,
            UpdateOrganizationMemberRequest(role="developer", status="active"),
            identity,
            database,
        )
    assert forbidden.value.status_code == 403
