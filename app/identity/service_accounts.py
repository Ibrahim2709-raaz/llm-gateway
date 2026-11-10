from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.api_keys import generate_api_key
from app.db.models import ApiKey, Organization, ServiceAccount
from app.identity.audit import append_audit_event
from app.identity.authorization import (
    SERVICE_ACCOUNTS_MANAGE,
    Principal,
    require_organization_permission,
)
from app.identity.sessions import utc_now


class ServiceAccountError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CreatedServiceAccount:
    service_account: ServiceAccount
    api_key: ApiKey
    token: str


@dataclass(frozen=True, slots=True)
class ServiceAccountCredential:
    service_account: ServiceAccount
    api_key: ApiKey | None


def _normalize_name(value: str) -> str:
    normalized = " ".join(value.split())
    if not 1 <= len(normalized) <= 200:
        raise ServiceAccountError("Service account name must contain 1 to 200 characters")
    return normalized


async def create_service_account(
    database: AsyncSession,
    *,
    principal: Principal,
    organization_id: UUID,
    name: str,
    pepper: str,
) -> CreatedServiceAccount:
    require_organization_permission(
        principal,
        organization_id=organization_id,
        permission=SERVICE_ACCOUNTS_MANAGE,
    )
    organization = await database.scalar(
        select(Organization).where(
            Organization.id == organization_id,
            Organization.status == "active",
        )
    )
    if organization is None:
        raise ServiceAccountError("Organization is unavailable")
    normalized_name = _normalize_name(name)
    generated = generate_api_key(pepper)
    service_account = ServiceAccount(
        id=uuid4(),
        organization_id=organization_id,
        name=normalized_name,
        role="developer",
        status="active",
        created_by_user_id=principal.actor_id,
    )
    api_key = ApiKey(
        id=uuid4(),
        client_id=organization.client_id,
        service_account_id=service_account.id,
        name=f"{normalized_name} credential",
        key_prefix=generated.prefix,
        key_hash=generated.digest,
    )
    database.add(service_account)
    database.add(api_key)
    await database.flush()
    await append_audit_event(
        database,
        principal=principal,
        action="service_account.created",
        resource_type="service_account",
        organization_id=organization_id,
        resource_id=str(service_account.id),
        event_data={"name": service_account.name, "key_prefix": api_key.key_prefix},
    )
    return CreatedServiceAccount(service_account, api_key, generated.token)


async def list_service_accounts(
    database: AsyncSession,
    *,
    principal: Principal,
    organization_id: UUID,
) -> list[ServiceAccountCredential]:
    require_organization_permission(
        principal,
        organization_id=organization_id,
        permission=SERVICE_ACCOUNTS_MANAGE,
    )
    statement = (
        select(ServiceAccount, ApiKey)
        .outerjoin(
            ApiKey,
            (ApiKey.service_account_id == ServiceAccount.id) & ApiKey.revoked_at.is_(None),
        )
        .where(ServiceAccount.organization_id == organization_id)
        .order_by(ServiceAccount.created_at.desc(), ServiceAccount.id)
    )
    rows = await database.execute(statement)
    return [ServiceAccountCredential(account, key) for account, key in rows]


async def revoke_service_account(
    database: AsyncSession,
    *,
    principal: Principal,
    organization_id: UUID,
    service_account_id: UUID,
    now: datetime | None = None,
) -> ServiceAccount:
    require_organization_permission(
        principal,
        organization_id=organization_id,
        permission=SERVICE_ACCOUNTS_MANAGE,
    )
    account = await database.scalar(
        select(ServiceAccount)
        .where(
            ServiceAccount.id == service_account_id,
            ServiceAccount.organization_id == organization_id,
        )
        .with_for_update()
    )
    if account is None:
        raise ServiceAccountError("Service account does not exist")
    if account.status == "active":
        revoked_at = now or utc_now()
        account.status = "revoked"
        keys = (
            await database.scalars(
                select(ApiKey)
                .where(
                    ApiKey.service_account_id == account.id,
                    ApiKey.revoked_at.is_(None),
                )
                .with_for_update()
            )
        ).all()
        for api_key in keys:
            api_key.revoked_at = revoked_at
        await append_audit_event(
            database,
            principal=principal,
            action="service_account.revoked",
            resource_type="service_account",
            organization_id=organization_id,
            resource_id=str(account.id),
            event_data={"name": account.name, "revoked_key_count": len(keys)},
        )
        await database.flush()
    return account
