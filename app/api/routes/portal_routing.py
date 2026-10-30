from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import GatewayError
from app.db.session import get_database_session
from app.identity.authorization import (
    ROUTING_READ,
    AuthorizationError,
    require_organization_permission,
)
from app.identity.dependencies import BrowserIdentity, authenticate_portal_session
from app.routing.repository import DatabaseRoutingEvidenceRepository
from app.schemas.portal_routing import RoutingDecisionListResponse, RoutingDecisionResponse

router = APIRouter(
    prefix="/v1/portal/organizations/{organization_id}/routing",
    tags=["Portal routing"],
)


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
        if exc.code == "cross_organization_access":
            raise GatewayError(
                "The organization is unavailable.",
                code="organization_unavailable",
                status_code=status.HTTP_404_NOT_FOUND,
            ) from exc
        raise GatewayError(
            "Permission to inspect routing decisions is required.",
            code=exc.code,
            error_type="authorization_error",
            status_code=status.HTTP_403_FORBIDDEN,
        ) from exc

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
