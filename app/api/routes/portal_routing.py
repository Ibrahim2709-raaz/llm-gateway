from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import GatewayError
from app.db.session import get_database_session
from app.identity.audit import append_audit_event
from app.identity.authorization import (
    ROUTING_MANAGE,
    ROUTING_READ,
    AuthorizationError,
    require_organization_permission,
)
from app.identity.dependencies import (
    BrowserIdentity,
    authenticate_portal_session,
    require_portal_csrf,
)
from app.routing.preferences import (
    get_organization_routing_preference,
    set_organization_routing_preference,
)
from app.routing.repository import DatabaseRoutingEvidenceRepository
from app.schemas.portal_routing import (
    RoutingDecisionListResponse,
    RoutingDecisionResponse,
    RoutingPreferenceResponse,
    UpdateRoutingPreferenceRequest,
)

router = APIRouter(
    prefix="/v1/portal/organizations/{organization_id}/routing",
    tags=["Portal routing"],
)


def _raise_authorization_error(error: AuthorizationError) -> NoReturn:
    if error.code == "cross_organization_access":
        raise GatewayError(
            "The organization is unavailable.",
            code="organization_unavailable",
            status_code=status.HTTP_404_NOT_FOUND,
        ) from error
    raise GatewayError(
        "Permission to manage routing preferences is required.",
        code=error.code,
        error_type="authorization_error",
        status_code=status.HTTP_403_FORBIDDEN,
    ) from error


@router.get(
    "/decisions",
    response_model=RoutingDecisionListResponse,
    operation_id="listPortalRoutingDecisions",
)
async def list_routing_decisions(
    organization_id: UUID,
    identity: Annotated[BrowserIdentity, Depends(authenticate_portal_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> RoutingDecisionListResponse:
    try:
        require_organization_permission(
            identity.principal,
            organization_id=organization_id,
            permission=ROUTING_READ,
        )
    except AuthorizationError as exc:
        _raise_authorization_error(exc)

    decisions = await DatabaseRoutingEvidenceRepository(database).list_decisions(
        client_id=identity.organization.client_id,
        limit=limit,
    )
    return RoutingDecisionListResponse(
        data=[
            RoutingDecisionResponse(
                request_id=decision.request_id,
                task_category=decision.task_category,
                difficulty=decision.difficulty,
                classification_confidence=decision.classification_confidence,
                classifier_version=decision.classifier_version,
                policy_version=decision.policy_version,
                score_version=decision.score_version,
                selected_provider=decision.selected_provider,
                selected_model=decision.selected_model,
                reason_codes=decision.reason_codes,
                candidate_count=decision.candidate_count,
                created_at=decision.created_at,
            )
            for decision in decisions
        ]
    )


@router.get(
    "/preference",
    response_model=RoutingPreferenceResponse,
    operation_id="getPortalRoutingPreference",
)
async def get_routing_preference(
    organization_id: UUID,
    identity: Annotated[BrowserIdentity, Depends(authenticate_portal_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> RoutingPreferenceResponse:
    try:
        require_organization_permission(
            identity.principal,
            organization_id=organization_id,
            permission=ROUTING_READ,
        )
    except AuthorizationError as exc:
        _raise_authorization_error(exc)
    preference = await get_organization_routing_preference(
        database, organization_id=organization_id
    )
    return RoutingPreferenceResponse(
        strategy=preference.strategy if preference is not None else "balanced"
    )


@router.put(
    "/preference",
    response_model=RoutingPreferenceResponse,
    operation_id="updatePortalRoutingPreference",
)
async def update_routing_preference(
    organization_id: UUID,
    payload: UpdateRoutingPreferenceRequest,
    identity: Annotated[BrowserIdentity, Depends(require_portal_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> RoutingPreferenceResponse:
    try:
        require_organization_permission(
            identity.principal,
            organization_id=organization_id,
            permission=ROUTING_MANAGE,
        )
        preference = await set_organization_routing_preference(
            database,
            organization_id=organization_id,
            strategy=payload.strategy,
            updated_by_user_id=identity.authentication.user.id,
        )
        await append_audit_event(
            database,
            principal=identity.principal,
            action="routing.preference_updated",
            resource_type="organization_routing_preference",
            organization_id=organization_id,
            resource_id=str(preference.id),
            event_data={"strategy": preference.strategy},
        )
        await database.commit()
    except AuthorizationError as exc:
        await database.rollback()
        _raise_authorization_error(exc)
    return RoutingPreferenceResponse(strategy=preference.strategy)
