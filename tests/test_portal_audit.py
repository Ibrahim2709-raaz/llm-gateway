from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.audit_feed import list_organization_audit_events
from app.identity.authorization import AuthorizationError, Principal


def principal(organization_id: object, role: str) -> Principal:
    return Principal(
        actor_type="user",
        actor_id=uuid4(),
        organization_id=organization_id,
        organization_role=role,
    )


@pytest.mark.anyio
async def test_audit_feed_is_owner_only_and_organization_scoped() -> None:
    organization_id = uuid4()
    database = AsyncMock(spec=AsyncSession)
    rows = MagicMock()
    rows.all.return_value = []
    database.scalars.return_value = rows
    assert (
        await list_organization_audit_events(
            database,
            principal=principal(organization_id, "owner"),
            organization_id=organization_id,
            limit=50,
        )
        == []
    )
    database.scalars.assert_awaited_once()

    with pytest.raises(AuthorizationError):
        await list_organization_audit_events(
            database,
            principal=principal(organization_id, "developer"),
            organization_id=organization_id,
            limit=50,
        )


@pytest.mark.anyio
async def test_audit_feed_validates_limit_before_query() -> None:
    organization_id = uuid4()
    database = AsyncMock(spec=AsyncSession)
    with pytest.raises(ValueError, match="between 1 and 200"):
        await list_organization_audit_events(
            database,
            principal=principal(organization_id, "owner"),
            organization_id=organization_id,
            limit=201,
        )
    database.scalars.assert_not_awaited()
