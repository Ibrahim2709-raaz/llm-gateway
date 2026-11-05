from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.db.models import Organization, OrganizationInvitation, OrganizationMembership, User
from app.identity.accounts import AccountConflictError
from app.identity.authorization import AuthorizationError, Principal
from app.identity.members import (
    InvitationError,
    MembershipConflictError,
    accept_invitation_for_new_user,
    accept_organization_invitation,
    issue_organization_invitation,
    update_organization_member,
)
from app.identity.tokens import hash_opaque_token

NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)


def owner_principal(organization_id: object | None = None) -> Principal:
    return Principal(
        actor_type="user",
        actor_id=uuid4(),
        organization_id=organization_id or uuid4(),
        organization_role="owner",
    )


def active_user(email: str = "member@example.com") -> User:
    return User(
        id=uuid4(),
        email_normalized=email,
        display_name="Member",
        status="active",
        email_verified_at=NOW,
    )


@pytest.mark.anyio
async def test_invitation_stores_only_hash_and_safe_prefix() -> None:
    organization_id = uuid4()
    database = MagicMock()
    database.scalar = AsyncMock(return_value=None)
    database.execute = AsyncMock()
    database.flush = AsyncMock()
    issued = await issue_organization_invitation(
        database,
        principal=owner_principal(organization_id),
        organization_id=organization_id,
        email=" MEMBER@Example.com ",
        role="developer",
        ttl=timedelta(days=7),
        now=NOW,
    )
    assert issued.value.startswith("organization_invitation_")
    assert issued.invitation.token_hash == hash_opaque_token(issued.value)
    assert issued.invitation.token_prefix == issued.value[:24]
    assert issued.value != issued.invitation.token_hash
    assert issued.invitation.email_normalized == "member@example.com"
    assert database.add.call_count == 2  # invitation plus audit event


@pytest.mark.anyio
async def test_non_owner_cannot_invite_or_cross_organization() -> None:
    organization_id = uuid4()
    database = MagicMock()
    developer = Principal(
        actor_type="user",
        actor_id=uuid4(),
        organization_id=organization_id,
        organization_role="developer",
    )
    with pytest.raises(AuthorizationError):
        await issue_organization_invitation(
            database,
            principal=developer,
            organization_id=organization_id,
            email="member@example.com",
            role="developer",
            ttl=timedelta(days=1),
        )
    with pytest.raises(AuthorizationError, match="does not belong"):
        await issue_organization_invitation(
            database,
            principal=owner_principal(),
            organization_id=organization_id,
            email="member@example.com",
            role="developer",
            ttl=timedelta(days=1),
        )


@pytest.mark.anyio
async def test_accept_invitation_requires_matching_email() -> None:
    user = active_user("different@example.com")
    invitation = OrganizationInvitation(
        id=uuid4(),
        organization_id=uuid4(),
        email_normalized="member@example.com",
        role="developer",
        token_prefix="organization_invitation",
        token_hash=hash_opaque_token("raw-token"),
        expires_at=NOW + timedelta(days=1),
    )
    database = MagicMock()
    database.scalar = AsyncMock(return_value=invitation)
    with pytest.raises(InvitationError, match="invalid"):
        await accept_organization_invitation(
            database,
            user=user,
            value="raw-token",
            now=NOW,
        )


@pytest.mark.anyio
async def test_accept_invitation_creates_active_membership() -> None:
    user = active_user()
    organization_id = uuid4()
    invitation = OrganizationInvitation(
        id=uuid4(),
        organization_id=organization_id,
        email_normalized=user.email_normalized,
        role="billing_viewer",
        token_prefix="organization_invitation",
        token_hash=hash_opaque_token("raw-token"),
        expires_at=NOW + timedelta(days=1),
    )
    database = MagicMock()
    database.scalar = AsyncMock(side_effect=[invitation, organization_id, None])
    database.flush = AsyncMock()
    membership = await accept_organization_invitation(
        database,
        user=user,
        value="raw-token",
        now=NOW,
    )
    assert membership.organization_id == organization_id
    assert membership.role == "billing_viewer"
    assert membership.status == "active"
    assert invitation.accepted_at == NOW
    assert database.add.call_count == 2  # membership plus audit event


@pytest.mark.anyio
async def test_new_user_accepts_invitation_with_verified_account() -> None:
    organization = Organization(
        id=uuid4(),
        client_id=uuid4(),
        slug="example-team",
        name="Example Team",
        status="active",
    )
    invitation = OrganizationInvitation(
        id=uuid4(),
        organization_id=organization.id,
        email_normalized="new.member@example.com",
        role="developer",
        token_prefix="organization_invitation",
        token_hash=hash_opaque_token("raw-token"),
        expires_at=NOW + timedelta(days=1),
    )
    database = MagicMock()
    database.scalar = AsyncMock(side_effect=[invitation, organization, None])
    database.flush = AsyncMock()
    hasher = MagicMock()
    hasher.hash.return_value = "encoded-password"

    accepted = await accept_invitation_for_new_user(
        database,
        value="raw-token",
        display_name="  New   Member  ",
        password="a sufficiently long password",
        password_hasher=hasher,
        now=NOW,
    )

    assert accepted.user.email_normalized == "new.member@example.com"
    assert accepted.user.display_name == "New Member"
    assert accepted.user.email_verified_at == NOW
    assert accepted.user.password_hash == "encoded-password"
    assert accepted.membership.organization_id == organization.id
    assert accepted.membership.user_id == accepted.user.id
    assert accepted.membership.role == "developer"
    assert invitation.accepted_at == NOW
    assert database.add.call_count == 3  # user, membership, and audit event


@pytest.mark.anyio
async def test_new_user_invitation_cannot_replace_existing_account() -> None:
    organization = Organization(
        id=uuid4(),
        client_id=uuid4(),
        slug="example-team",
        name="Example Team",
        status="active",
    )
    invitation = OrganizationInvitation(
        id=uuid4(),
        organization_id=organization.id,
        email_normalized="member@example.com",
        role="developer",
        token_prefix="organization_invitation",
        token_hash=hash_opaque_token("raw-token"),
        expires_at=NOW + timedelta(days=1),
    )
    database = MagicMock()
    database.scalar = AsyncMock(side_effect=[invitation, organization, uuid4()])

    with pytest.raises(AccountConflictError, match="already uses"):
        await accept_invitation_for_new_user(
            database,
            value="raw-token",
            display_name="Member",
            password="a sufficiently long password",
            now=NOW,
        )

    assert invitation.accepted_at is None
    database.add.assert_not_called()


@pytest.mark.anyio
async def test_final_owner_cannot_be_demoted_or_suspended() -> None:
    organization_id = uuid4()
    membership = OrganizationMembership(
        id=uuid4(),
        organization_id=organization_id,
        user_id=uuid4(),
        role="owner",
        status="active",
    )
    database = MagicMock()
    database.scalar = AsyncMock(return_value=membership)
    owner_rows = MagicMock()
    owner_rows.all.return_value = [membership.id]
    database.scalars = AsyncMock(return_value=owner_rows)
    with pytest.raises(MembershipConflictError, match="final active owner"):
        await update_organization_member(
            database,
            principal=owner_principal(organization_id),
            organization_id=organization_id,
            membership_id=membership.id,
            role="developer",
            status="active",
        )
    assert membership.role == "owner"
    assert membership.status == "active"
