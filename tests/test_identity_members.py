from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.db.models import OrganizationInvitation, OrganizationMembership, User
from app.identity.authorization import AuthorizationError, Principal
from app.identity.members import (
    InvitationError,
    MembershipConflictError,
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
