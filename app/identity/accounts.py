from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    BrowserSession,
    Client,
    Organization,
    OrganizationMembership,
    User,
)
from app.identity.audit import append_audit_event
from app.identity.authorization import Principal
from app.identity.passwords import ScryptPasswordHasher
from app.identity.sessions import (
    IssuedUserActionToken,
    consume_user_action_token,
    issue_user_action_token,
    normalize_email,
    utc_now,
)

ORGANIZATION_SLUG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$")
_DUMMY_PASSWORD_HASH = ScryptPasswordHasher().hash("dummy-password-never-authenticates")


class AccountConflictError(ValueError):
    pass


class InvalidCredentialsError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RegisteredAccount:
    client: Client
    organization: Organization
    user: User
    membership: OrganizationMembership
    verification: IssuedUserActionToken


@dataclass(frozen=True, slots=True)
class AccountLogin:
    user: User
    organization: Organization
    membership: OrganizationMembership


@dataclass(frozen=True, slots=True)
class PasswordResetRequest:
    delivery: IssuedUserActionToken | None


def normalize_organization_slug(value: str) -> str:
    normalized = value.strip().casefold()
    if not ORGANIZATION_SLUG_PATTERN.fullmatch(normalized):
        raise ValueError(
            "Organization slug must contain 3 to 63 lowercase letters, numbers, or hyphens"
        )
    return normalized


def _normalize_display_name(value: str) -> str:
    normalized = " ".join(value.split())
    if not 1 <= len(normalized) <= 200:
        raise ValueError("Display name must contain 1 to 200 characters")
    return normalized


def _normalize_organization_name(value: str) -> str:
    normalized = " ".join(value.split())
    if not 1 <= len(normalized) <= 200:
        raise ValueError("Organization name must contain 1 to 200 characters")
    return normalized


async def register_organization_owner(
    database: AsyncSession,
    *,
    email: str,
    display_name: str,
    password: str,
    organization_name: str,
    organization_slug: str,
    verification_ttl: timedelta,
    password_hasher: ScryptPasswordHasher | None = None,
    now: datetime | None = None,
) -> RegisteredAccount:
    normalized_email = normalize_email(email)
    normalized_display_name = _normalize_display_name(display_name)
    normalized_organization_name = _normalize_organization_name(organization_name)
    normalized_slug = normalize_organization_slug(organization_slug)
    hasher = password_hasher or ScryptPasswordHasher()
    encoded_password = hasher.hash(password)

    if await database.scalar(select(User.id).where(User.email_normalized == normalized_email)):
        raise AccountConflictError("An account or organization already uses those details")
    if await database.scalar(select(Organization.id).where(Organization.slug == normalized_slug)):
        raise AccountConflictError("An account or organization already uses those details")

    client_id = uuid4()
    organization_id = uuid4()
    user_id = uuid4()
    client = Client(id=client_id, name=normalized_organization_name, status="active")
    organization = Organization(
        id=organization_id,
        client_id=client_id,
        slug=normalized_slug,
        name=normalized_organization_name,
        status="active",
    )
    user = User(
        id=user_id,
        email_normalized=normalized_email,
        display_name=normalized_display_name,
        password_hash=encoded_password,
        status="active",
    )
    membership = OrganizationMembership(
        organization_id=organization_id,
        user_id=user_id,
        role="owner",
        status="active",
    )
    database.add_all((client, organization, user, membership))
    await database.flush()

    verification = await issue_user_action_token(
        database,
        user=user,
        purpose="email_verification",
        ttl=verification_ttl,
        now=now,
    )
    await append_audit_event(
        database,
        principal=Principal(
            actor_type="user",
            actor_id=user_id,
            organization_id=organization_id,
            organization_role="owner",
        ),
        action="organization.registered",
        resource_type="organization",
        organization_id=organization_id,
        resource_id=str(organization_id),
        event_data={"slug": normalized_slug},
    )
    return RegisteredAccount(
        client=client,
        organization=organization,
        user=user,
        membership=membership,
        verification=verification,
    )


async def verify_account_email(
    database: AsyncSession,
    *,
    verification_token: str,
    now: datetime | None = None,
) -> User:
    current_time = now or utc_now()
    action_token = await consume_user_action_token(
        database,
        value=verification_token,
        purpose="email_verification",
        now=current_time,
    )
    user = await database.get(User, action_token.user_id)
    if user is None or user.status != "active":
        raise InvalidCredentialsError("Account is unavailable")
    if user.email_verified_at is None:
        user.email_verified_at = current_time
        await database.flush()
    return user


async def authenticate_account(
    database: AsyncSession,
    *,
    email: str,
    password: str,
    password_hasher: ScryptPasswordHasher | None = None,
) -> AccountLogin:
    normalized_email = normalize_email(email)
    hasher = password_hasher or ScryptPasswordHasher()
    user = await database.scalar(select(User).where(User.email_normalized == normalized_email))
    encoded_hash = (
        user.password_hash
        if user is not None and user.password_hash
        else _DUMMY_PASSWORD_HASH
    )
    password_valid = hasher.verify(password, encoded_hash)
    if (
        user is None
        or not password_valid
        or user.status != "active"
        or user.email_verified_at is None
    ):
        raise InvalidCredentialsError("Email or password is incorrect")

    if hasher.needs_rehash(encoded_hash):
        user.password_hash = hasher.hash(password)
        await database.flush()

    statement = (
        select(OrganizationMembership, Organization)
        .join(Organization, Organization.id == OrganizationMembership.organization_id)
        .where(
            OrganizationMembership.user_id == user.id,
            OrganizationMembership.status == "active",
            Organization.status == "active",
        )
        .order_by(OrganizationMembership.created_at, OrganizationMembership.id)
        .limit(1)
    )
    row = (await database.execute(statement)).one_or_none()
    if row is None:
        raise InvalidCredentialsError("Account has no active organization")
    membership, organization = row
    return AccountLogin(user=user, organization=organization, membership=membership)


async def request_password_reset(
    database: AsyncSession,
    *,
    email: str,
    reset_ttl: timedelta,
    now: datetime | None = None,
) -> PasswordResetRequest:
    normalized_email = normalize_email(email)
    user = await database.scalar(select(User).where(User.email_normalized == normalized_email))
    if user is None or user.status != "active" or user.password_hash is None:
        return PasswordResetRequest(delivery=None)
    delivery = await issue_user_action_token(
        database,
        user=user,
        purpose="password_reset",
        ttl=reset_ttl,
        now=now,
    )
    return PasswordResetRequest(delivery=delivery)


async def reset_account_password(
    database: AsyncSession,
    *,
    reset_token: str,
    new_password: str,
    password_hasher: ScryptPasswordHasher | None = None,
    now: datetime | None = None,
) -> User:
    current_time = now or utc_now()
    action_token = await consume_user_action_token(
        database,
        value=reset_token,
        purpose="password_reset",
        now=current_time,
    )
    user = await database.get(User, action_token.user_id)
    if user is None or user.status != "active":
        raise InvalidCredentialsError("Account is unavailable")
    hasher = password_hasher or ScryptPasswordHasher()
    user.password_hash = hasher.hash(new_password)
    await database.execute(
        update(BrowserSession)
        .where(BrowserSession.user_id == user.id, BrowserSession.revoked_at.is_(None))
        .values(revoked_at=current_time)
    )
    await append_audit_event(
        database,
        principal=Principal(actor_type="user", actor_id=user.id),
        action="user.password_reset",
        resource_type="user",
        resource_id=str(user.id),
    )
    await database.flush()
    return user


async def list_active_memberships(
    database: AsyncSession, *, user_id: UUID
) -> list[tuple[OrganizationMembership, Organization]]:
    statement = (
        select(OrganizationMembership, Organization)
        .join(Organization, Organization.id == OrganizationMembership.organization_id)
        .where(
            OrganizationMembership.user_id == user_id,
            OrganizationMembership.status == "active",
            Organization.status == "active",
        )
        .order_by(Organization.name, Organization.id)
    )
    return list((await database.execute(statement)).all())
