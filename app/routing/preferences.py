from __future__ import annotations

from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Organization, OrganizationRoutingPreference
from app.identity.roles import ROUTING_STRATEGIES
from app.routing.intelligent import RoutingStrategy
from app.schemas.chat import ChatCompletionRequest


class DatabaseRoutingPreferenceResolver:
    def __init__(self, database: AsyncSession) -> None:
        self.database = database

    async def apply(
        self, client_id: UUID, request: ChatCompletionRequest
    ) -> ChatCompletionRequest:
        return await apply_client_routing_preference(
            self.database,
            client_id=client_id,
            request=request,
        )


async def get_organization_routing_preference(
    database: AsyncSession, *, organization_id: UUID
) -> OrganizationRoutingPreference | None:
    return await database.scalar(
        select(OrganizationRoutingPreference).where(
            OrganizationRoutingPreference.organization_id == organization_id
        )
    )


async def set_organization_routing_preference(
    database: AsyncSession,
    *,
    organization_id: UUID,
    strategy: str,
    updated_by_user_id: UUID,
) -> OrganizationRoutingPreference:
    if strategy not in ROUTING_STRATEGIES:
        raise ValueError("Unsupported routing strategy")
    preference = await database.scalar(
        select(OrganizationRoutingPreference)
        .where(OrganizationRoutingPreference.organization_id == organization_id)
        .with_for_update()
    )
    if preference is None:
        preference = OrganizationRoutingPreference(
            organization_id=organization_id,
            strategy=strategy,
            updated_by_user_id=updated_by_user_id,
        )
        database.add(preference)
    else:
        preference.strategy = strategy
        preference.updated_by_user_id = updated_by_user_id
    await database.flush()
    return preference


async def apply_client_routing_preference(
    database: AsyncSession,
    *,
    client_id: UUID,
    request: ChatCompletionRequest,
) -> ChatCompletionRequest:
    if "strategy" in request.routing.model_fields_set:
        return request
    strategy = await database.scalar(
        select(OrganizationRoutingPreference.strategy)
        .join(Organization, Organization.id == OrganizationRoutingPreference.organization_id)
        .where(
            Organization.client_id == client_id,
            Organization.status == "active",
        )
    )
    if strategy is None:
        return request
    routing = request.routing.model_copy(
        update={"strategy": cast(RoutingStrategy, strategy)}
    )
    return request.model_copy(update={"routing": routing})
