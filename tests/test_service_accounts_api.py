from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.api.routes.portal_service_accounts import (
    create_portal_service_account,
    list_portal_service_accounts,
    revoke_portal_service_account,
)
from app.core.config import Settings
from app.core.errors import GatewayError
from app.db.models import (
    ApiKey,
    BrowserSession,
    Organization,
    OrganizationMembership,
    ServiceAccount,
    User,
)
from app.identity.authorization import AuthorizationError, Principal
from app.identity.dependencies import BrowserIdentity
from app.identity.service_accounts import (
    CreatedServiceAccount,
    ServiceAccountCredential,
    ServiceAccountError,
)
from app.identity.sessions import AuthenticatedBrowserSession
from app.schemas.portal_service_accounts import CreateServiceAccountRequest

NOW = datetime(2026, 11, 5, 12, tzinfo=UTC)


def browser_identity() -> BrowserIdentity:
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
    session = BrowserSession(
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
        authentication=AuthenticatedBrowserSession(session, user),
        organization=organization,
        membership=membership,
        principal=Principal(
            actor_type="user",
            actor_id=user.id,
            organization_id=organization.id,
            organization_role="owner",
        ),
    )


def account_and_key(identity: BrowserIdentity) -> tuple[ServiceAccount, ApiKey]:
    account = ServiceAccount(
        id=uuid4(),
        organization_id=identity.organization.id,
        name="Worker",
        role="developer",
        status="active",
        created_at=NOW,
    )
    key = ApiKey(
        id=uuid4(),
        client_id=identity.organization.client_id,
        service_account_id=account.id,
        name="Worker credential",
        key_prefix="gw_live_example",
        key_hash="digest",
        created_at=NOW,
    )
    return account, key


@pytest.mark.anyio
async def test_create_service_account_serializes_one_time_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = browser_identity()
    account, key = account_and_key(identity)
    monkeypatch.setattr(
        "app.api.routes.portal_service_accounts.create_service_account",
        AsyncMock(return_value=CreatedServiceAccount(account, key, "gw_live_secret")),
    )
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()

    result = await create_portal_service_account(
        identity.organization.id,
        CreateServiceAccountRequest(name="Worker"),
        identity,
        database,
        Settings(_env_file=None, app_env="test"),
    )

    assert result.token == "gw_live_secret"
    assert result.key_prefix == key.key_prefix
    database.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_list_service_accounts_returns_only_safe_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = browser_identity()
    account, key = account_and_key(identity)
    monkeypatch.setattr(
        "app.api.routes.portal_service_accounts.list_service_accounts",
        AsyncMock(return_value=[ServiceAccountCredential(account, key)]),
    )

    result = await list_portal_service_accounts(
        identity.organization.id,
        identity,
        MagicMock(),
    )

    assert result.data[0].key_prefix == key.key_prefix
    assert not hasattr(result.data[0], "token")


@pytest.mark.anyio
async def test_revoke_service_account_commits_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = browser_identity()
    account, _ = account_and_key(identity)
    account.status = "revoked"
    monkeypatch.setattr(
        "app.api.routes.portal_service_accounts.revoke_service_account",
        AsyncMock(return_value=account),
    )
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()

    result = await revoke_portal_service_account(
        identity.organization.id,
        account.id,
        identity,
        database,
    )

    assert result.status == "revoked"
    database.commit.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("failure", "expected_status"),
    [
        (AuthorizationError("permission_denied", "denied"), 403),
        (AuthorizationError("cross_organization_access", "denied"), 404),
        (ServiceAccountError("missing"), 422),
    ],
)
async def test_create_service_account_maps_safe_errors(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected_status: int,
) -> None:
    identity = browser_identity()
    monkeypatch.setattr(
        "app.api.routes.portal_service_accounts.create_service_account",
        AsyncMock(side_effect=failure),
    )
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()

    with pytest.raises(GatewayError) as error:
        await create_portal_service_account(
            identity.organization.id,
            CreateServiceAccountRequest(name="Worker"),
            identity,
            database,
            Settings(_env_file=None, app_env="test"),
        )

    assert error.value.status_code == expected_status
    database.rollback.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("failure", "expected_status"),
    [
        (AuthorizationError("permission_denied", "denied"), 403),
        (ServiceAccountError("missing"), 404),
    ],
)
async def test_revoke_service_account_maps_safe_errors(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected_status: int,
) -> None:
    identity = browser_identity()
    monkeypatch.setattr(
        "app.api.routes.portal_service_accounts.revoke_service_account",
        AsyncMock(side_effect=failure),
    )
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()

    with pytest.raises(GatewayError) as error:
        await revoke_portal_service_account(
            identity.organization.id,
            uuid4(),
            identity,
            database,
        )

    assert error.value.status_code == expected_status
    database.rollback.assert_awaited_once()
