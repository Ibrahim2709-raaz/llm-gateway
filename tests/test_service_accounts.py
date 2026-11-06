from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.auth.api_keys import hash_api_key
from app.db.models import ApiKey, Organization, ServiceAccount
from app.identity.authorization import AuthorizationError, Principal
from app.identity.service_accounts import (
    create_service_account,
    list_service_accounts,
    revoke_service_account,
)
from app.main import create_app

NOW = datetime(2026, 11, 5, 12, tzinfo=UTC)


def principal(organization_id: object, role: str = "owner") -> Principal:
    return Principal(
        actor_type="user",
        actor_id=uuid4(),
        organization_id=organization_id,
        organization_role=role,
    )


def test_service_account_routes_are_in_openapi_contract() -> None:
    with TestClient(create_app()) as client:
        paths = client.get("/openapi.json").json()["paths"]
    base = "/v1/portal/organizations/{organization_id}/service-accounts"
    assert base in paths
    assert f"{base}/{{service_account_id}}" in paths


@pytest.mark.anyio
async def test_create_service_account_stores_only_key_digest_and_audits() -> None:
    organization = Organization(
        id=uuid4(),
        client_id=uuid4(),
        slug="example-team",
        name="Example Team",
        status="active",
    )
    database = MagicMock()
    database.scalar = AsyncMock(return_value=organization)
    database.flush = AsyncMock()

    created = await create_service_account(
        database,
        principal=principal(organization.id),
        organization_id=organization.id,
        name=" Production worker ",
        pepper="test-pepper",
    )

    assert created.service_account.name == "Production worker"
    assert created.service_account.role == "developer"
    assert created.api_key.service_account_id == created.service_account.id
    assert created.api_key.client_id == organization.client_id
    assert created.api_key.key_hash == hash_api_key(created.token, "test-pepper")
    assert created.token != created.api_key.key_hash
    assert database.add.call_count == 3  # account, key, and audit event


@pytest.mark.anyio
async def test_only_owner_can_manage_service_accounts() -> None:
    organization_id = uuid4()
    with pytest.raises(AuthorizationError):
        await create_service_account(
            MagicMock(),
            principal=principal(organization_id, role="developer"),
            organization_id=organization_id,
            name="Worker",
            pepper="test-pepper",
        )


@pytest.mark.anyio
async def test_list_service_accounts_returns_scoped_account_and_credential() -> None:
    organization_id = uuid4()
    account = ServiceAccount(
        id=uuid4(),
        organization_id=organization_id,
        name="Worker",
        role="developer",
        status="active",
    )
    key = ApiKey(
        id=uuid4(),
        client_id=uuid4(),
        service_account_id=account.id,
        name="Credential",
        key_prefix="gw_live_example",
        key_hash="digest",
    )
    database = MagicMock()
    database.execute = AsyncMock(return_value=[(account, key)])

    result = await list_service_accounts(
        database,
        principal=principal(organization_id),
        organization_id=organization_id,
    )

    assert result[0].service_account is account
    assert result[0].api_key is key


@pytest.mark.anyio
async def test_revoking_service_account_revokes_all_active_credentials_once() -> None:
    organization_id = uuid4()
    account = ServiceAccount(
        id=uuid4(),
        organization_id=organization_id,
        name="Worker",
        role="developer",
        status="active",
    )
    keys = [
        ApiKey(
            id=uuid4(),
            client_id=uuid4(),
            service_account_id=account.id,
            name="Credential",
            key_prefix="gw_live_example",
            key_hash="digest",
        )
    ]
    rows = MagicMock()
    rows.all.return_value = keys
    database = MagicMock()
    database.scalar = AsyncMock(return_value=account)
    database.scalars = AsyncMock(return_value=rows)
    database.flush = AsyncMock()

    await revoke_service_account(
        database,
        principal=principal(organization_id),
        organization_id=organization_id,
        service_account_id=account.id,
        now=NOW,
    )
    await revoke_service_account(
        database,
        principal=principal(organization_id),
        organization_id=organization_id,
        service_account_id=account.id,
        now=NOW,
    )

    assert account.status == "revoked"
    assert keys[0].revoked_at == NOW
    assert database.scalars.await_count == 1
    assert database.add.call_count == 1  # one audit event
