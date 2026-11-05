from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.routes.chat import get_chat_service
from app.core.config import Settings, get_settings
from app.core.errors import GatewayError
from app.identity.authorization import (
    ROUTING_MANAGE,
    AuthorizationError,
    require_organization_permission,
)
from app.identity.dependencies import BrowserIdentity, require_portal_csrf
from app.rate_limits.dependencies import consume_client_rate_limit
from app.schemas.chat import ChatCompletionRequest, ChatCompletionResponse
from app.schemas.portal_playground import PlaygroundPreviewResponse
from app.services.chat import ChatCompletionService

router = APIRouter(
    prefix="/v1/portal/organizations/{organization_id}/playground",
    tags=["Portal playground"],
)


def _raise_authorization_error(error: AuthorizationError) -> NoReturn:
    if error.code == "cross_organization_access":
        raise GatewayError(
            "The organization is unavailable.",
            code="organization_unavailable",
            status_code=status.HTTP_404_NOT_FOUND,
        ) from error
    raise GatewayError(
        "Developer permission is required to use the playground.",
        code=error.code,
        error_type="authorization_error",
        status_code=status.HTTP_403_FORBIDDEN,
    ) from error


def _authorize_playground(identity: BrowserIdentity, organization_id: UUID) -> None:
    try:
        require_organization_permission(
            identity.principal,
            organization_id=organization_id,
            permission=ROUTING_MANAGE,
        )
    except AuthorizationError as exc:
        _raise_authorization_error(exc)


@router.post(
    "/preview",
    response_model=PlaygroundPreviewResponse,
    operation_id="previewPortalPlaygroundRequest",
)
async def preview_playground_request(
    organization_id: UUID,
    payload: ChatCompletionRequest,
    identity: Annotated[BrowserIdentity, Depends(require_portal_csrf)],
    service: Annotated[ChatCompletionService, Depends(get_chat_service)],
) -> PlaygroundPreviewResponse:
    _authorize_playground(identity, organization_id)
    preview = await service.preview(payload, identity.organization.client_id)
    primary = preview.plan.candidates[0]
    routing_strategy = (
        "explicit"
        if preview.request.model != "auto"
        else preview.request.routing.strategy
    )
    return PlaygroundPreviewResponse(
        selected_provider=primary.provider.name,
        selected_model=primary.provider.model,
        routing_strategy=routing_strategy,
        estimated_cost_usd=float(primary.estimated_cost_usd),
        task_category=preview.plan.classification.category.value,
        difficulty=preview.plan.classification.difficulty.value,
        classification_confidence=preview.plan.classification.confidence,
        policy_version=preview.plan.policy_version,
        score_version=primary.score_version,
        reason_codes=list(primary.reason_codes),
        eligible_candidate_count=len(preview.plan.candidates),
    )


@router.post(
    "/completions",
    response_model=ChatCompletionResponse,
    operation_id="createPortalPlaygroundCompletion",
)
async def create_playground_completion(
    organization_id: UUID,
    payload: ChatCompletionRequest,
    request: Request,
    response: Response,
    identity: Annotated[BrowserIdentity, Depends(require_portal_csrf)],
    service: Annotated[ChatCompletionService, Depends(get_chat_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ChatCompletionResponse:
    _authorize_playground(identity, organization_id)
    rate_limit = await consume_client_rate_limit(
        request,
        client_id=identity.organization.client_id,
        settings=settings,
    )
    response.headers.update(rate_limit.headers())
    return await service.create(
        payload,
        UUID(request.state.request_id),
        identity.organization.client_id,
    )
