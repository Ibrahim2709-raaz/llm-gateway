from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.api_keys import generate_api_key
from app.db.models import ApiKey, Organization
from app.identity.audit import append_audit_event
from app.identity.authorization import API_KEYS_MANAGE, Principal, require_organization_permission
from app.identity.sessions import utc_now


class PortalApiKeyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CreatedOrganizationApiKey:
    api_key: ApiKey
    token: str


@dataclass(frozen=True, slots=True)
class RotatedOrganizationApiKey:
    revoked_api_key_id: UUID
    api_key: ApiKey
    token: str


def _normalize_key_name(value: str) -> str:
    normalized = " ".join(value.split())
    if not 1 <= len(normalized) <= 200:
        raise ValueError("API key name must contain 1 to 200 characters")
    return normalized


async def create_organization_api_key(
    database: AsyncSession,
    *,
    principal: Principal,
    organization_id: UUID,
    name: str,
    pepper: str,
) -> CreatedOrganizationApiKey:
    require_organization_permission(
        principal,
        organization_id=organization_id,
        permission=API_KEYS_MANAGE,
    )
    normalized_name = _normalize_key_name(name)
    organization = await database.scalar(
        select(Organization).where(
            Organization.id == organization_id,
            Organization.status == "active",
        )
    )
    if organization is None:
        raise PortalApiKeyError("Organization is unavailable")
    generated = generate_api_key(pepper)
    api_key = ApiKey(
        id=uuid4(),
        client_id=organization.client_id,
        name=normalized_name,
        key_prefix=generated.prefix,
        key_hash=generated.digest,
    )
    database.add(api_key)
    await database.flush()
    await append_audit_event(
        database,
        principal=principal,
        action="api_key.created",
        resource_type="api_key",
        organization_id=organization_id,
        resource_id=str(api_key.id),
        event_data={"key_prefix": api_key.key_prefix, "name": api_key.name},
    )
    return CreatedOrganizationApiKey(api_key=api_key, token=generated.token)


async def list_organization_api_keys(
    database: AsyncSession,
    *,
    principal: Principal,
    organization_id: UUID,
) -> list[ApiKey]:
    require_organization_permission(
        principal,
        organization_id=organization_id,
        permission=API_KEYS_MANAGE,
    )
    statement = (
        select(ApiKey)
        .join(Organization, Organization.client_id == ApiKey.client_id)
        .where(Organization.id == organization_id)
        .where(ApiKey.service_account_id.is_(None))
        .order_by(ApiKey.created_at.desc(), ApiKey.id)
    )
    return list((await database.scalars(statement)).all())


async def revoke_organization_api_key(
    database: AsyncSession,
    *,
    principal: Principal,
    organization_id: UUID,
    api_key_id: UUID,
    now: datetime | None = None,
) -> ApiKey:
    require_organization_permission(
        principal,
        organization_id=organization_id,
        permission=API_KEYS_MANAGE,
    )
    statement = (
        select(ApiKey)
        .join(Organization, Organization.client_id == ApiKey.client_id)
        .where(
            Organization.id == organization_id,
            ApiKey.id == api_key_id,
            ApiKey.service_account_id.is_(None),
        )
        .with_for_update()
    )
    api_key = await database.scalar(statement)
    if api_key is None:
        raise PortalApiKeyError("API key does not exist")
    if api_key.revoked_at is None:
        api_key.revoked_at = now or utc_now()
        await append_audit_event(
            database,
            principal=principal,
            action="api_key.revoked",
            resource_type="api_key",
            organization_id=organization_id,
            resource_id=str(api_key.id),
            event_data={"key_prefix": api_key.key_prefix, "name": api_key.name},
        )
        await database.flush()
    return api_key


async def rotate_organization_api_key(
    database: AsyncSession,
    *,
    principal: Principal,
    organization_id: UUID,
    api_key_id: UUID,
    pepper: str,
    now: datetime | None = None,
) -> RotatedOrganizationApiKey:
    require_organization_permission(
        principal,
        organization_id=organization_id,
        permission=API_KEYS_MANAGE,
    )
    statement = (
        select(ApiKey, Organization)
        .join(Organization, Organization.client_id == ApiKey.client_id)
        .where(
            Organization.id == organization_id,
            Organization.status == "active",
            ApiKey.id == api_key_id,
            ApiKey.service_account_id.is_(None),
            ApiKey.revoked_at.is_(None),
        )
        .with_for_update()
    )
    row = (await database.execute(statement)).one_or_none()
    if row is None:
        raise PortalApiKeyError("Active API key does not exist")
    current, organization = row
    generated = generate_api_key(pepper)
    replacement = ApiKey(
        id=uuid4(),
        client_id=organization.client_id,
        name=current.name,
        key_prefix=generated.prefix,
        key_hash=generated.digest,
    )
    current.revoked_at = now or utc_now()
    database.add(replacement)
    await database.flush()
    await append_audit_event(
        database,
        principal=principal,
        action="api_key.rotated",
        resource_type="api_key",
        organization_id=organization_id,
        resource_id=str(replacement.id),
        event_data={
            "name": replacement.name,
            "old_key_prefix": current.key_prefix,
            "new_key_prefix": replacement.key_prefix,
            "replaced_api_key_id": str(current.id),
        },
    )
    return RotatedOrganizationApiKey(current.id, replacement, generated.token)
