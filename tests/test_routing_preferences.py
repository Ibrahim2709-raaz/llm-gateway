from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import OrganizationRoutingPreference
from app.routing.preferences import (
    apply_client_routing_preference,
    set_organization_routing_preference,
)
from app.schemas.chat import ChatCompletionRequest, ChatMessage, RoutingOptions


def request(*, routing: RoutingOptions | None = None) -> ChatCompletionRequest:
    values: dict[str, object] = {
        "messages": [ChatMessage(role="user", content="Hello")]
    }
    if routing is not None:
        values["routing"] = routing
    return ChatCompletionRequest.model_validate(values)


@pytest.mark.anyio
async def test_organization_preference_applies_when_strategy_is_omitted() -> None:
    database = AsyncMock(spec=AsyncSession)
    database.scalar.return_value = "cost"
    original = request(routing=RoutingOptions(max_cost_usd=0.5))
    effective = await apply_client_routing_preference(
        database,
        client_id=uuid4(),
        request=original,
    )
    assert effective.routing.strategy == "cost"
    assert effective.routing.max_cost_usd == 0.5
    assert original.routing.strategy == "balanced"


@pytest.mark.anyio
async def test_explicit_request_strategy_always_wins_without_database_query() -> None:
    database = AsyncMock(spec=AsyncSession)
    original = request(routing=RoutingOptions(strategy="quality"))
    effective = await apply_client_routing_preference(
        database,
        client_id=uuid4(),
        request=original,
    )
    assert effective is original
    database.scalar.assert_not_awaited()


@pytest.mark.anyio
async def test_routing_preference_upsert_is_row_locked() -> None:
    database = AsyncMock(spec=AsyncSession)
    database.scalar.return_value = None
    organization_id = uuid4()
    user_id = uuid4()
    created = await set_organization_routing_preference(
        database,
        organization_id=organization_id,
        strategy="latency",
        updated_by_user_id=user_id,
    )
    assert isinstance(created, OrganizationRoutingPreference)
    assert created.strategy == "latency"
    assert created.updated_by_user_id == user_id
    database.add.assert_called_once_with(created)
    database.flush.assert_awaited_once()
