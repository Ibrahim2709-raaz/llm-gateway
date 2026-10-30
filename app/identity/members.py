from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Organization,
    OrganizationInvitation,
    OrganizationMembership,
    User,
)
from app.identity.audit import append_audit_event
from app.identity.authorization import MEMBERS_MANAGE, Principal, require_organization_permission
from app.identity.roles import ORGANIZATION_ROLES
from app.identity.sessions import normalize_email, utc_now
from app.identity.tokens import hash_opaque_token, issue_opaque_token


class MembershipConflictError(ValueError):
    pass


class InvitationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class IssuedOrganizationInvitation:
    invitation: OrganizationInvitation
    value: str


@dataclass(frozen=True, slots=True)
class OrganizationMember:
    membership: OrganizationMembership
    user: User


async def issue_organization_invitation(
    database: AsyncSession,
    *,
    principal: Principal,
    organization_id: UUID,
    email: str,
    role: str,
    ttl: timedelta,
    now: datetime | None = None,
) -> IssuedOrganizationInvitation:
    require_organization_permission(
        principal,
        organization_id=organization_id,
        permission=MEMBERS_MANAGE,
    )
    if role not in ORGANIZATION_ROLES:
        raise ValueError("Unsupported organization role")
    if ttl <= timedelta(0):
        raise ValueError("Invitation TTL must be positive")
    normalized_email = normalize_email(email)
    issued_at = now or utc_now()

    existing_membership = await database.scalar(
        select(OrganizationMembership.id)
        .join(User, User.id == OrganizationMembership.user_id)
        .where(
            OrganizationMembership.organization_id == organization_id,
            User.email_normalized == normalized_email,
            OrganizationMembership.status == "active",
        )
    )
    if existing_membership is not None:
        raise MembershipConflictError("User is already an active organization member")

    await database.execute(
        update(OrganizationInvitation)
        .where(
            OrganizationInvitation.organization_id == organization_id,
            OrganizationInvitation.email_normalized == normalized_email,
            OrganizationInvitation.accepted_at.is_(None),
            OrganizationInvitation.revoked_at.is_(None),
        )
        .values(revoked_at=issued_at)
    )
    secret = issue_opaque_token(namespace="organization_invitation")
    invitation = OrganizationInvitation(
        organization_id=organization_id,
        email_normalized=normalized_email,
        role=role,
        token_prefix=secret.prefix,
        token_hash=secret.hash,
        invited_by_user_id=principal.actor_id,
        expires_at=issued_at + ttl,
    )
    database.add(invitation)
    await database.flush()
    await append_audit_event(
        database,
        principal=principal,
        action="organization.invitation_created",
        resource_type="organization_invitation",
        organization_id=organization_id,
        resource_id=str(invitation.id),
        event_data={"email_normalized": normalized_email, "role": role},
    )
    return IssuedOrganizationInvitation(invitation=invitation, value=secret.value)


async def accept_organization_invitation(
    database: AsyncSession,
    *,
    user: User,
    value: str,
    now: datetime | None = None,
) -> OrganizationMembership:
    current_time = now or utc_now()
    invitation = await database.scalar(
        select(OrganizationInvitation).where(
            OrganizationInvitation.token_hash == hash_opaque_token(value)
        ).with_for_update()
    )
    if (
        invitation is None
        or invitation.accepted_at is not None
        or invitation.revoked_at is not None
        or invitation.expires_at <= current_time
        or invitation.email_normalized != user.email_normalized
        or user.status != "active"
    ):
        raise InvitationError("Invitation is invalid or expired")
    organization_active = await database.scalar(
        select(Organization.id).where(
            Organization.id == invitation.organization_id,
            Organization.status == "active",
        )
    )
    if organization_active is None:
        raise InvitationError("Invitation is invalid or expired")

    membership = await database.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == invitation.organization_id,
            OrganizationMembership.user_id == user.id,
        ).with_for_update()
    )
    if membership is None:
        membership = OrganizationMembership(
            organization_id=invitation.organization_id,
            user_id=user.id,
            role=invitation.role,
            status="active",
        )
        database.add(membership)
    else:
        membership.role = invitation.role
        membership.status = "active"
    invitation.accepted_at = current_time
    await database.flush()
    await append_audit_event(
        database,
        principal=Principal(
            actor_type="user",
            actor_id=user.id,
            organization_id=invitation.organization_id,
            organization_role=invitation.role,
        ),
        action="organization.invitation_accepted",
        resource_type="organization_membership",
        organization_id=invitation.organization_id,
        resource_id=str(membership.id),
        event_data={"role": invitation.role},
    )
    return membership


async def list_organization_members(
    database: AsyncSession,
    *,
    principal: Principal,
    organization_id: UUID,
) -> list[OrganizationMember]:
    require_organization_permission(
        principal,
        organization_id=organization_id,
        permission=MEMBERS_MANAGE,
    )
    rows = await database.execute(
        select(OrganizationMembership, User)
        .join(User, User.id == OrganizationMembership.user_id)
        .where(OrganizationMembership.organization_id == organization_id)
        .order_by(User.display_name, User.id)
    )
    return [OrganizationMember(membership=membership, user=user) for membership, user in rows.all()]


async def update_organization_member(
    database: AsyncSession,
    *,
    principal: Principal,
    organization_id: UUID,
    membership_id: UUID,
    role: str,
    status: str,
) -> OrganizationMembership:
    require_organization_permission(
        principal,
        organization_id=organization_id,
        permission=MEMBERS_MANAGE,
    )
    if role not in ORGANIZATION_ROLES or status not in {"active", "suspended"}:
        raise ValueError("Unsupported membership role or status")
    membership = await database.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.id == membership_id,
            OrganizationMembership.organization_id == organization_id,
        ).with_for_update()
    )
    if membership is None:
        raise MembershipConflictError("Organization membership does not exist")

    removes_owner = membership.role == "owner" and (role != "owner" or status != "active")
    if removes_owner:
        owner_rows = await database.scalars(
            select(OrganizationMembership.id).where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.role == "owner",
                OrganizationMembership.status == "active",
            ).with_for_update()
        )
        if len(owner_rows.all()) <= 1:
            raise MembershipConflictError("The final active owner cannot be removed")

    previous_role = membership.role
    previous_status = membership.status
    membership.role = role
    membership.status = status
    await database.flush()
    await append_audit_event(
        database,
        principal=principal,
        action="organization.membership_updated",
        resource_type="organization_membership",
        organization_id=organization_id,
        resource_id=str(membership.id),
        event_data={
            "previous_role": previous_role,
            "new_role": role,
            "previous_status": previous_status,
            "new_status": status,
        },
    )
    return membership
