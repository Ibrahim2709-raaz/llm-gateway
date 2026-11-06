from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditEvent
from app.identity.authorization import AUDIT_READ, Principal, require_organization_permission


async def list_organization_audit_events(
    database: AsyncSession,
    *,
    principal: Principal,
    organization_id: UUID,
    limit: int,
) -> list[AuditEvent]:
    require_organization_permission(
        principal,
        organization_id=organization_id,
        permission=AUDIT_READ,
    )
    if not 1 <= limit <= 200:
        raise ValueError("Audit event limit must be between 1 and 200")
    statement = (
        select(AuditEvent)
        .where(AuditEvent.organization_id == organization_id)
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id)
        .limit(limit)
    )
    return list((await database.scalars(statement)).all())
