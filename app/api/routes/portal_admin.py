from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from app.core.config import Settings, get_settings
from app.core.errors import GatewayError
from app.identity.authorization import (
    PLATFORM_MANAGE,
    AuthorizationError,
    require_platform_permission,
)
from app.identity.dependencies import BrowserIdentity, authenticate_portal_session
from app.platform.model_catalog import build_model_catalog
from app.schemas.portal_admin import ModelCatalogEntry, ModelCatalogResponse

router = APIRouter(prefix="/v1/portal/admin", tags=["Portal administration"])


@router.get("/models", response_model=ModelCatalogResponse, operation_id="listAdminModels")
async def list_admin_models(
    request: Request,
    identity: Annotated[BrowserIdentity, Depends(authenticate_portal_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ModelCatalogResponse:
    try:
        require_platform_permission(identity.principal, permission=PLATFORM_MANAGE)
    except AuthorizationError as exc:
        raise GatewayError(
            "Platform operator permission is required.",
            code=exc.code,
            error_type="authorization_error",
            status_code=status.HTTP_403_FORBIDDEN,
        ) from exc

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
