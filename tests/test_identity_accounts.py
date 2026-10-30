from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.db.models import Organization, OrganizationMembership, User, UserActionToken
from app.identity.accounts import (
    AccountConflictError,
    InvalidCredentialsError,
    authenticate_account,
    normalize_organization_slug,
    register_organization_owner,
    verify_account_email,
)
from app.identity.passwords import ScryptPasswordHasher

NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)
FAST_HASHER = ScryptPasswordHasher(log_n=14)


@pytest.mark.parametrize("slug", ["team-ai", "abc", "a1-b2"])
def test_normalize_organization_slug(slug: str) -> None:
    assert normalize_organization_slug(f" {slug.upper()} ") == slug


@pytest.mark.parametrize("slug", ["ab", "has spaces", "-leading", "trailing-", "UP_under"])
def test_normalize_organization_slug_rejects_invalid_values(slug: str) -> None:
    with pytest.raises(ValueError, match="slug"):
        normalize_organization_slug(slug)


@pytest.mark.anyio
async def test_register_owner_creates_legacy_client_identity_and_audit() -> None:
    database = MagicMock()
    database.scalar = AsyncMock(side_effect=[None, None])
    database.execute = AsyncMock()
    database.flush = AsyncMock()

    registered = await register_organization_owner(
        database,
        email=" Owner@Example.com ",
        display_name="  Example   Owner ",
        password="a-long-enough-password",
        organization_name=" Example   Team ",
        organization_slug="Example-Team",
        verification_ttl=timedelta(days=1),
        password_hasher=FAST_HASHER,
        now=NOW,
    )

    assert registered.organization.client_id == registered.client.id
    assert registered.membership.organization_id == registered.organization.id
    assert registered.membership.user_id == registered.user.id
    assert registered.membership.role == "owner"
    assert registered.user.email_normalized == "owner@example.com"
    assert registered.user.display_name == "Example Owner"
    assert registered.user.password_hash != "a-long-enough-password"
    assert registered.verification.value.startswith("email_verification_")
    assert database.add_all.call_count == 1
    # Action token and audit event are individually added after the identity graph.
    assert database.add.call_count == 2


@pytest.mark.anyio
async def test_register_owner_rejects_duplicate_email_or_slug() -> None:
    database = MagicMock()
    database.scalar = AsyncMock(return_value=uuid4())
    with pytest.raises(AccountConflictError, match="already"):
        await register_organization_owner(
            database,
            email="owner@example.com",
            display_name="Owner",
            password="a-long-enough-password",
            organization_name="Example",
            organization_slug="example-team",
            verification_ttl=timedelta(days=1),
            password_hasher=FAST_HASHER,
        )
    database.add_all.assert_not_called()


@pytest.mark.anyio
async def test_authenticate_account_returns_first_active_membership() -> None:
    user = User(
        id=uuid4(),
        email_normalized="owner@example.com",
        display_name="Owner",
        password_hash=FAST_HASHER.hash("a-long-enough-password"),
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
    result = MagicMock()
    result.one_or_none.return_value = (membership, organization)
    database = MagicMock()
    database.scalar = AsyncMock(return_value=user)
    database.execute = AsyncMock(return_value=result)

    login = await authenticate_account(
        database,
        email="OWNER@example.com",
        password="a-long-enough-password",
        password_hasher=FAST_HASHER,
    )
    assert login.user is user
    assert login.membership is membership
    assert login.organization is organization


@pytest.mark.anyio
@pytest.mark.parametrize("missing_user", [True, False])
async def test_authenticate_account_has_generic_failure(missing_user: bool) -> None:
    user = None
    if not missing_user:
        user = User(
            id=uuid4(),
            email_normalized="owner@example.com",
            display_name="Owner",
            password_hash=FAST_HASHER.hash("correct-long-password"),
            status="active",
            email_verified_at=NOW,
        )
    database = MagicMock()
    database.scalar = AsyncMock(return_value=user)
    with pytest.raises(InvalidCredentialsError, match="incorrect"):
        await authenticate_account(
            database,
            email="owner@example.com",
            password="wrong-but-long-password",
            password_hasher=FAST_HASHER,
        )


@pytest.mark.anyio
async def test_verify_account_email_consumes_token_and_marks_user() -> None:
    user = User(
        id=uuid4(),
        email_normalized="owner@example.com",
        display_name="Owner",
        status="active",
    )
    token = UserActionToken(
        user_id=user.id,
        purpose="email_verification",
        token_prefix="email_verification_test",
        token_hash="hash",
        created_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )
    database = MagicMock()
    database.scalar = AsyncMock(return_value=token)
    database.get = AsyncMock(return_value=user)
    database.flush = AsyncMock()

    verified = await verify_account_email(
        database,
        verification_token="raw-token",
        now=NOW + timedelta(minutes=1),
    )
    assert verified.email_verified_at == NOW + timedelta(minutes=1)
    assert token.consumed_at == NOW + timedelta(minutes=1)
