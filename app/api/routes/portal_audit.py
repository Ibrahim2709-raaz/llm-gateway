from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import GatewayError
from app.db.session import get_database_session
from app.identity.audit_feed import list_organization_audit_events
from app.identity.authorization import AuthorizationError
from app.identity.dependencies import BrowserIdentity, authenticate_portal_session
from app.schemas.portal_audit import AuditEventListResponse, AuditEventResponse

router = APIRouter(
    prefix="/v1/portal/organizations/{organization_id}/audit-events",
    tags=["Portal audit"],
)


@router.get("", response_model=AuditEventListResponse, operation_id="listPortalAuditEvents")
async def list_audit_events(
    organization_id: UUID,
    identity: Annotated[BrowserIdentity, Depends(authenticate_portal_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditEventListResponse:
    try:
        events = await list_organization_audit_events(
            database,
            principal=identity.principal,
            organization_id=organization_id,
            limit=limit,
        )
    except AuthorizationError as exc:
        if exc.code == "cross_organization_access":
            raise GatewayError(
                "The organization is unavailable.",
                code="organization_unavailable",
                status_code=status.HTTP_404_NOT_FOUND,
            ) from exc
        raise GatewayError(
            "Owner permission is required to view audit events.",
            code=exc.code,
            error_type="authorization_error",
            status_code=status.HTTP_403_FORBIDDEN,
        ) from exc
    return AuditEventListResponse(
        data=[
            AuditEventResponse(
                id=event.id,
                actor_type=event.actor_type,
                actor_id=event.actor_id,
                action=event.action,
                resource_type=event.resource_type,
                resource_id=event.resource_id,
                request_id=event.request_id,
                event_data=event.event_data,
                created_at=event.created_at,
            )
            for event in events
        ],
        limit=limit,
    )
