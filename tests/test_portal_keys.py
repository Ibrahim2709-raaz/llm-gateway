from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.auth.api_keys import hash_api_key
from app.db.models import ApiKey, Organization
from app.identity.authorization import AuthorizationError, Principal
from app.identity.portal_keys import (
    create_organization_api_key,
    list_organization_api_keys,
    revoke_organization_api_key,
    rotate_organization_api_key,
)

NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)


def principal(organization_id: object, role: str = "owner") -> Principal:
    return Principal(
        actor_type="user",
        actor_id=uuid4(),
        organization_id=organization_id,
        organization_role=role,
    )


@pytest.mark.anyio
async def test_create_api_key_stores_digest_and_audits_safe_prefix() -> None:
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
    created = await create_organization_api_key(
        database,
        principal=principal(organization.id),
        organization_id=organization.id,
        name=" Production key ",
        pepper="test-pepper",
    )
    assert created.api_key.client_id == organization.client_id
    assert created.api_key.name == "Production key"
    assert created.api_key.key_hash == hash_api_key(created.token, "test-pepper")
    assert created.token != created.api_key.key_hash
    assert database.add.call_count == 2  # key and audit event


@pytest.mark.anyio
async def test_billing_viewer_cannot_manage_api_keys() -> None:
    organization_id = uuid4()
    with pytest.raises(AuthorizationError):
        await create_organization_api_key(
            MagicMock(),
            principal=principal(organization_id, role="billing_viewer"),
            organization_id=organization_id,
            name="Key",
            pepper="test-pepper",
        )


@pytest.mark.anyio
async def test_list_api_keys_uses_only_organization_scoped_query() -> None:
    organization_id = uuid4()
    key = ApiKey(
        id=uuid4(),
        client_id=uuid4(),
        name="Key",
        key_prefix="gw_live_example",
        key_hash="digest",
        created_at=NOW,
    )
    rows = MagicMock()
    rows.all.return_value = [key]
    database = MagicMock()
    database.scalars = AsyncMock(return_value=rows)
    result = await list_organization_api_keys(
        database,
        principal=principal(organization_id),
        organization_id=organization_id,
    )
    assert result == [key]
    database.scalars.assert_awaited_once()


@pytest.mark.anyio
async def test_revoke_api_key_is_idempotent_and_audited_once() -> None:
    organization_id = uuid4()
    key = ApiKey(
        id=uuid4(),
        client_id=uuid4(),
        name="Key",
        key_prefix="gw_live_example",
        key_hash="digest",
    )
    database = MagicMock()
    database.scalar = AsyncMock(return_value=key)
    database.flush = AsyncMock()
    await revoke_organization_api_key(
        database,
        principal=principal(organization_id),
        organization_id=organization_id,
        api_key_id=key.id,
        now=NOW,
    )
    await revoke_organization_api_key(
        database,
        principal=principal(organization_id),
        organization_id=organization_id,
        api_key_id=key.id,
        now=NOW,
    )
    assert key.revoked_at == NOW
    assert database.add.call_count == 1


@pytest.mark.anyio
async def test_rotate_api_key_revokes_old_key_and_returns_one_time_replacement() -> None:
    organization = Organization(
        id=uuid4(),
        client_id=uuid4(),
        slug="example-team",
        name="Example Team",
        status="active",
    )
    current = ApiKey(
        id=uuid4(),
        client_id=organization.client_id,
        name="Production key",
        key_prefix="gw_live_old_key",
        key_hash="old-digest",
    )
    row = MagicMock()
    row.one_or_none.return_value = (current, organization)
    database = MagicMock()
    database.execute = AsyncMock(return_value=row)
    database.flush = AsyncMock()

    rotated = await rotate_organization_api_key(
        database,
        principal=principal(organization.id),
        organization_id=organization.id,
        api_key_id=current.id,
        pepper="test-pepper",
        now=NOW,
    )

    assert current.revoked_at == NOW
    assert rotated.api_key.name == current.name
    assert rotated.api_key.key_hash == hash_api_key(rotated.token, "test-pepper")
    assert rotated.api_key.service_account_id is None
    assert database.add.call_count == 2  # replacement key and audit event
