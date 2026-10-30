from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BrowserSession, User, UserActionToken
from app.identity.roles import USER_ACTION_TOKEN_PURPOSES
from app.identity.tokens import hash_opaque_token, issue_opaque_token


class BrowserAuthenticationError(ValueError):
    """A deliberately generic browser authentication failure."""


class ActionTokenError(ValueError):
    """A one-time action token could not be used."""


@dataclass(frozen=True, slots=True)
class IssuedBrowserSession:
    session: BrowserSession
    session_token: str
    csrf_token: str


@dataclass(frozen=True, slots=True)
class AuthenticatedBrowserSession:
    session: BrowserSession
    user: User


@dataclass(frozen=True, slots=True)
class IssuedUserActionToken:
    token: UserActionToken
    value: str


def utc_now() -> datetime:
    return datetime.now(UTC)


def normalize_email(value: str) -> str:
    normalized = value.strip().casefold()
    if len(normalized) > 320 or normalized.count("@") != 1:
        raise ValueError("Email address is invalid")
    local_part, domain = normalized.split("@", maxsplit=1)
    if (
        not local_part
        or not domain
        or domain.startswith(".")
        or domain.endswith(".")
        or "." not in domain
        or any(character.isspace() or ord(character) < 32 for character in normalized)
    ):
        raise ValueError("Email address is invalid")
    return normalized


async def issue_browser_session(
    database: AsyncSession,
    *,
    user: User,
    active_organization_id: UUID | None,
    ttl: timedelta,
    now: datetime | None = None,
) -> IssuedBrowserSession:
    if ttl <= timedelta(0):
        raise ValueError("Session TTL must be positive")
    if user.status != "active" or user.email_verified_at is None:
        raise BrowserAuthenticationError("Unable to create browser session")

    issued_at = now or utc_now()
    session_secret = issue_opaque_token(namespace="portal_session")
    csrf_secret = issue_opaque_token(namespace="csrf")
    stored = BrowserSession(
        user_id=user.id,
        active_organization_id=active_organization_id,
        token_prefix=session_secret.prefix,
        token_hash=session_secret.hash,
        csrf_token_hash=csrf_secret.hash,
        created_at=issued_at,
        expires_at=issued_at + ttl,
        last_seen_at=issued_at,
    )
    database.add(stored)
    await database.flush()
    return IssuedBrowserSession(
        session=stored,
        session_token=session_secret.value,
        csrf_token=csrf_secret.value,
    )


async def authenticate_browser_session(
    database: AsyncSession,
    *,
    session_token: str,
    now: datetime | None = None,
    touch_interval: timedelta = timedelta(minutes=5),
) -> AuthenticatedBrowserSession:
    if not session_token or touch_interval < timedelta(0):
        raise BrowserAuthenticationError("Invalid or expired session")

    current_time = now or utc_now()
    statement = (
        select(BrowserSession, User)
        .join(User, User.id == BrowserSession.user_id)
        .where(BrowserSession.token_hash == hash_opaque_token(session_token))
    )
    row = (await database.execute(statement)).one_or_none()
    if row is None:
        raise BrowserAuthenticationError("Invalid or expired session")

    stored, user = row
    if (
        stored.revoked_at is not None
        or stored.expires_at <= current_time
        or user.status != "active"
        or user.email_verified_at is None
    ):
        raise BrowserAuthenticationError("Invalid or expired session")

    if stored.last_seen_at is None or current_time - stored.last_seen_at >= touch_interval:
        stored.last_seen_at = current_time
    return AuthenticatedBrowserSession(session=stored, user=user)


def verify_csrf_token(stored: BrowserSession, presented_token: str) -> None:
    supplied_hash = hash_opaque_token(presented_token) if presented_token else ""
    if not hmac.compare_digest(stored.csrf_token_hash, supplied_hash):
        raise BrowserAuthenticationError("Invalid CSRF token")


async def revoke_browser_session(
    database: AsyncSession,
    *,
    stored: BrowserSession,
    now: datetime | None = None,
) -> None:
    if stored.revoked_at is None:
        stored.revoked_at = now or utc_now()
        await database.flush()


async def issue_user_action_token(
    database: AsyncSession,
    *,
    user: User,
    purpose: str,
    ttl: timedelta,
    now: datetime | None = None,
) -> IssuedUserActionToken:
    if purpose not in USER_ACTION_TOKEN_PURPOSES:
        raise ValueError("Unsupported action token purpose")
    if ttl <= timedelta(0):
        raise ValueError("Action token TTL must be positive")

    issued_at = now or utc_now()
    await database.execute(
        update(UserActionToken)
        .where(
            UserActionToken.user_id == user.id,
            UserActionToken.purpose == purpose,
            UserActionToken.consumed_at.is_(None),
        )
        .values(consumed_at=issued_at)
    )
    secret = issue_opaque_token(namespace=purpose)
    stored = UserActionToken(
        user_id=user.id,
        purpose=purpose,
        token_prefix=secret.prefix,
        token_hash=secret.hash,
        created_at=issued_at,
        expires_at=issued_at + ttl,
    )
    database.add(stored)
    await database.flush()
    return IssuedUserActionToken(token=stored, value=secret.value)


async def consume_user_action_token(
    database: AsyncSession,
    *,
    value: str,
    purpose: str,
    now: datetime | None = None,
) -> UserActionToken:
    if purpose not in USER_ACTION_TOKEN_PURPOSES or not value:
        raise ActionTokenError("Invalid or expired action token")

    current_time = now or utc_now()
    statement = select(UserActionToken).where(
        UserActionToken.token_hash == hash_opaque_token(value),
        UserActionToken.purpose == purpose,
    )
    stored = await database.scalar(statement)
    if (
        stored is None
        or stored.consumed_at is not None
        or stored.expires_at <= current_time
    ):
        raise ActionTokenError("Invalid or expired action token")
    stored.consumed_at = current_time
    await database.flush()
    return stored
