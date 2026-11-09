from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import GatewayError
from app.db.models import ProviderPricingProposal
from app.db.session import get_database_session
from app.identity.audit import append_audit_event
from app.identity.authorization import (
    PLATFORM_MANAGE,
    AuthorizationError,
    require_platform_permission,
)
from app.identity.dependencies import (
    BrowserIdentity,
    authenticate_portal_session,
    require_portal_csrf,
)
from app.platform.model_catalog import build_model_catalog
from app.platform.pricing import (
    PricingProposalError,
    create_pricing_proposal,
    list_pricing_proposals,
    review_pricing_proposal,
)
from app.schemas.portal_admin import (
    CreatePricingProposalRequest,
    ModelCatalogEntry,
    ModelCatalogResponse,
    PricingProposalListResponse,
    PricingProposalResponse,
    ReviewPricingProposalRequest,
)

router = APIRouter(prefix="/v1/portal/admin", tags=["Portal administration"])


def _require_platform_operator(identity: BrowserIdentity) -> None:
    try:
        require_platform_permission(identity.principal, permission=PLATFORM_MANAGE)
    except AuthorizationError as exc:
        raise GatewayError(
            "Platform operator permission is required.",
            code=exc.code,
            error_type="authorization_error",
            status_code=status.HTTP_403_FORBIDDEN,
        ) from exc


def _proposal_response(proposal: ProviderPricingProposal) -> PricingProposalResponse:
    return PricingProposalResponse(
        id=proposal.id,
        provider=proposal.provider,
        model=proposal.model,
        input_cost_per_million_usd=proposal.input_cost_per_million,
        output_cost_per_million_usd=proposal.output_cost_per_million,
        source_url=proposal.source_url,
        rationale=proposal.rationale,
        status=proposal.status,
        proposed_by_user_id=proposal.proposed_by_user_id,
        reviewed_by_user_id=proposal.reviewed_by_user_id,
        review_note=proposal.review_note,
        created_at=proposal.created_at,
        reviewed_at=proposal.reviewed_at,
    )


@router.get("/models", response_model=ModelCatalogResponse, operation_id="listAdminModels")
async def list_admin_models(
    request: Request,
    identity: Annotated[BrowserIdentity, Depends(authenticate_portal_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ModelCatalogResponse:
    _require_platform_operator(identity)

    available = frozenset(provider.name for provider in request.app.state.resources.providers)
    models = build_model_catalog(settings, available_provider_names=available)
    return ModelCatalogResponse(
        data=[
            ModelCatalogEntry(
                provider=model.provider,
                model=model.model,
                availability="available" if model.enabled else "disabled",
                input_cost_per_million_usd=model.input_cost_per_million_usd,
                output_cost_per_million_usd=model.output_cost_per_million_usd,
                context_window_tokens=model.context_window_tokens,
                capabilities=["text_generation"],
                estimated_latency_ms=model.estimated_latency_ms,
                quality_score=model.quality_score,
                max_complexity=model.max_complexity,
                auto_selectable=model.auto_selectable,
            )
            for model in models
        ]
    )


@router.get(
    "/pricing-proposals",
    response_model=PricingProposalListResponse,
    operation_id="listAdminPricingProposals",
)
async def list_admin_pricing_proposals(
    identity: Annotated[BrowserIdentity, Depends(authenticate_portal_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> PricingProposalListResponse:
    _require_platform_operator(identity)
    proposals = await list_pricing_proposals(database, limit=limit)
    return PricingProposalListResponse(data=[_proposal_response(item) for item in proposals])


@router.post(
    "/pricing-proposals",
    response_model=PricingProposalResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createAdminPricingProposal",
)
async def create_admin_pricing_proposal(
    payload: CreatePricingProposalRequest,
    identity: Annotated[BrowserIdentity, Depends(require_portal_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> PricingProposalResponse:
    _require_platform_operator(identity)
    try:
        proposal = await create_pricing_proposal(
            database,
            provider=payload.provider,
            model=payload.model,
            input_cost_per_million=payload.input_cost_per_million_usd,
            output_cost_per_million=payload.output_cost_per_million_usd,
            source_url=str(payload.source_url),
            rationale=payload.rationale,
            proposed_by_user_id=identity.authentication.user.id,
        )
        await append_audit_event(
            database,
            principal=identity.principal,
            action="provider_pricing.proposed",
            resource_type="provider_pricing_proposal",
            resource_id=str(proposal.id),
            event_data={
                "provider": proposal.provider,
                "model": proposal.model,
                "input_cost_per_million_usd": str(proposal.input_cost_per_million),
                "output_cost_per_million_usd": str(proposal.output_cost_per_million),
                "source_url": proposal.source_url,
            },
        )
        await database.commit()
    except Exception:
        await database.rollback()
        raise
    return _proposal_response(proposal)


@router.post(
    "/pricing-proposals/{proposal_id}/review",
    response_model=PricingProposalResponse,
    operation_id="reviewAdminPricingProposal",
)
async def review_admin_pricing_proposal(
    proposal_id: UUID,
    payload: ReviewPricingProposalRequest,
    identity: Annotated[BrowserIdentity, Depends(require_portal_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> PricingProposalResponse:
    _require_platform_operator(identity)
    try:
        proposal = await review_pricing_proposal(
            database,
            proposal_id=proposal_id,
            reviewer_user_id=identity.authentication.user.id,
            decision=payload.decision,
            review_note=payload.review_note,
        )
        await append_audit_event(
            database,
            principal=identity.principal,
            action=f"provider_pricing.{payload.decision}",
            resource_type="provider_pricing_proposal",
            resource_id=str(proposal.id),
            event_data={
                "provider": proposal.provider,
                "model": proposal.model,
                "decision": payload.decision,
            },
        )
        await database.commit()
    except PricingProposalError as exc:
        await database.rollback()
        error_status = (
            status.HTTP_404_NOT_FOUND
            if exc.code == "pricing_proposal_not_found"
            else status.HTTP_409_CONFLICT
        )
        raise GatewayError(exc.message, code=exc.code, status_code=error_status) from exc
    except Exception:
        await database.rollback()
        raise
    return _proposal_response(proposal)
