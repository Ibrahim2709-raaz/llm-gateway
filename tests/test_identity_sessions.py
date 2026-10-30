import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.db.models import BrowserSession, User, UserActionToken
from app.identity.sessions import (
    ActionTokenError,
    BrowserAuthenticationError,
    authenticate_browser_session,
    consume_user_action_token,
    issue_browser_session,
    issue_user_action_token,
    normalize_email,
    revoke_browser_session,
    verify_csrf_token,
)

NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)


def active_user() -> User:
    return User(
        id=uuid4(),
        email_normalized="owner@example.com",
        display_name="Owner",
        status="active",
        email_verified_at=NOW,
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" Owner@Example.COM ", "owner@example.com"),
        ("person+portal@sub.example.com", "person+portal@sub.example.com"),
    ],
)
def test_normalize_email(raw: str, expected: str) -> None:
    assert normalize_email(raw) == expected


@pytest.mark.parametrize(
    "raw", ["missing-at.example.com", "@example.com", "a@localhost", "a @x.io"]
)
def test_normalize_email_rejects_invalid_addresses(raw: str) -> None:
    with pytest.raises(ValueError, match="invalid"):
        normalize_email(raw)


def test_issue_and_authenticate_browser_session() -> None:
    asyncio.run(_issue_and_authenticate_browser_session())


async def _issue_and_authenticate_browser_session() -> None:
    database = MagicMock()
    database.flush = AsyncMock()
    user = active_user()

    issued = await issue_browser_session(
        database,
        user=user,
        active_organization_id=uuid4(),
        ttl=timedelta(days=7),
        now=NOW,
    )

    assert issued.session.token_hash != issued.session_token
    assert issued.session.csrf_token_hash != issued.csrf_token
    assert issued.session.expires_at == NOW + timedelta(days=7)
    verify_csrf_token(issued.session, issued.csrf_token)
    with pytest.raises(BrowserAuthenticationError, match="CSRF"):
        verify_csrf_token(issued.session, "wrong-token")

    result = MagicMock()
    result.one_or_none.return_value = (issued.session, user)
    database.execute = AsyncMock(return_value=result)
    authenticated = await authenticate_browser_session(
        database,
        session_token=issued.session_token,
        now=NOW + timedelta(minutes=10),
    )
    assert authenticated.user is user
    assert issued.session.last_seen_at == NOW + timedelta(minutes=10)


def test_authenticate_rejects_expired_or_suspended_session() -> None:
    asyncio.run(_authenticate_rejects_expired_or_suspended_session())


async def _authenticate_rejects_expired_or_suspended_session() -> None:
    database = MagicMock()
    result = MagicMock()
    user = active_user()
    stored = BrowserSession(
        user_id=user.id,
        token_prefix="portal_session_example",
        token_hash="hash",
        csrf_token_hash="csrf-hash",
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=1),
    )
    result.one_or_none.return_value = (stored, user)
    database.execute = AsyncMock(return_value=result)

    with pytest.raises(BrowserAuthenticationError, match="expired"):
        await authenticate_browser_session(
            database, session_token="raw", now=NOW + timedelta(hours=1)
        )

    stored.expires_at = NOW + timedelta(hours=2)
    user.status = "suspended"
    with pytest.raises(BrowserAuthenticationError, match="expired"):
        await authenticate_browser_session(database, session_token="raw", now=NOW)


def test_unverified_user_cannot_receive_session() -> None:
    asyncio.run(_unverified_user_cannot_receive_session())


async def _unverified_user_cannot_receive_session() -> None:
    database = MagicMock()
    database.flush = AsyncMock()
    user = active_user()
    user.email_verified_at = None
    with pytest.raises(BrowserAuthenticationError):
        await issue_browser_session(
            database,
            user=user,
            active_organization_id=None,
            ttl=timedelta(days=1),
        )


def test_revoke_browser_session_is_idempotent() -> None:
    asyncio.run(_revoke_browser_session_is_idempotent())


async def _revoke_browser_session_is_idempotent() -> None:
    database = MagicMock()
    database.flush = AsyncMock()
    stored = BrowserSession(
        user_id=uuid4(),
        token_prefix="prefix",
        token_hash="hash",
        csrf_token_hash="csrf-hash",
        expires_at=NOW + timedelta(days=1),
    )
    await revoke_browser_session(database, stored=stored, now=NOW)
    await revoke_browser_session(database, stored=stored, now=NOW + timedelta(hours=1))
    assert stored.revoked_at == NOW
    database.flush.assert_awaited_once()


def test_action_tokens_are_single_use_and_replace_previous_token() -> None:
    asyncio.run(_action_tokens_are_single_use_and_replace_previous_token())


async def _action_tokens_are_single_use_and_replace_previous_token() -> None:
    database = MagicMock()
    database.execute = AsyncMock()
    database.flush = AsyncMock()
    issued = await issue_user_action_token(
        database,
        user=active_user(),
        purpose="password_reset",
        ttl=timedelta(hours=1),
        now=NOW,
    )
    assert issued.token.token_hash != issued.value
    database.execute.assert_awaited_once()

    database.scalar = AsyncMock(return_value=issued.token)
    consumed = await consume_user_action_token(
        database,
        value=issued.value,
        purpose="password_reset",
        now=NOW + timedelta(minutes=5),
    )
    assert consumed.consumed_at == NOW + timedelta(minutes=5)

    with pytest.raises(ActionTokenError, match="expired"):
        await consume_user_action_token(
            database,
            value=issued.value,
            purpose="password_reset",
            now=NOW + timedelta(minutes=6),
        )


def test_action_token_rejects_wrong_purpose_and_expiry() -> None:
    asyncio.run(_action_token_rejects_wrong_purpose_and_expiry())


async def _action_token_rejects_wrong_purpose_and_expiry() -> None:
    database = MagicMock()
    database.scalar = AsyncMock(return_value=None)
    with pytest.raises(ActionTokenError):
        await consume_user_action_token(database, value="value", purpose="unsupported")

    token = UserActionToken(
        user_id=uuid4(),
        purpose="email_verification",
        token_prefix="prefix",
        token_hash="hash",
        created_at=NOW,
        expires_at=NOW,
    )
    database.scalar = AsyncMock(return_value=token)
    with pytest.raises(ActionTokenError):
        await consume_user_action_token(
            database,
            value="value",
            purpose="email_verification",
            now=NOW + timedelta(seconds=1),
        )
