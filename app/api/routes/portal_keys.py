from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import GatewayError
from app.db.session import get_database_session
from app.identity.authorization import AuthorizationError
from app.identity.dependencies import (
    BrowserIdentity,
    authenticate_portal_session,
    require_portal_csrf,
)
from app.identity.portal_keys import (
    PortalApiKeyError,
    create_organization_api_key,
    list_organization_api_keys,
    revoke_organization_api_key,
)
from app.schemas.portal_keys import (
    ApiKeyListResponse,
    ApiKeyRevokedResponse,
    ApiKeySummary,
    CreateApiKeyRequest,
    CreatedApiKeyResponse,
)

router = APIRouter(
    prefix="/v1/portal/organizations/{organization_id}/api-keys",
    tags=["Portal API keys"],
)


def _raise_authorization_error(error: AuthorizationError) -> NoReturn:
    if error.code == "cross_organization_access":
        raise GatewayError(
            "The organization is unavailable.",
            code="organization_unavailable",
            status_code=status.HTTP_404_NOT_FOUND,
        ) from error
    raise GatewayError(
        "Permission to manage API keys is required.",
        code=error.code,
        error_type="authorization_error",
        status_code=status.HTTP_403_FORBIDDEN,
    ) from error


@router.post(
    "",
    response_model=CreatedApiKeyResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createPortalApiKey",
)
async def create_api_key(
    organization_id: UUID,
    payload: CreateApiKeyRequest,
    identity: Annotated[BrowserIdentity, Depends(require_portal_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> CreatedApiKeyResponse:
    try:
        created = await create_organization_api_key(
            database,
            principal=identity.principal,
            organization_id=organization_id,
            name=payload.name,
            pepper=settings.gateway_api_key_pepper.get_secret_value(),
        )
        await database.commit()
    except AuthorizationError as exc:
        await database.rollback()
        _raise_authorization_error(exc)
    except (PortalApiKeyError, ValueError) as exc:
        await database.rollback()
        raise GatewayError(
            "The API key could not be created.",
            code="invalid_api_key_request",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        ) from exc
    return CreatedApiKeyResponse(
        id=created.api_key.id,
        name=created.api_key.name,
        key_prefix=created.api_key.key_prefix,
        token=created.token,
        created_at=created.api_key.created_at,
    )


@router.get("", response_model=ApiKeyListResponse, operation_id="listPortalApiKeys")
async def list_api_keys(
    organization_id: UUID,
    identity: Annotated[BrowserIdentity, Depends(authenticate_portal_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> ApiKeyListResponse:
    try:
        api_keys = await list_organization_api_keys(
            database,
            principal=identity.principal,
            organization_id=organization_id,
        )
    except AuthorizationError as exc:
        _raise_authorization_error(exc)
    return ApiKeyListResponse(
        data=[
            ApiKeySummary(
                id=api_key.id,
                name=api_key.name,
                key_prefix=api_key.key_prefix,
                created_at=api_key.created_at,
                revoked_at=api_key.revoked_at,
            )
            for api_key in api_keys
        ]
    )


@router.delete(
    "/{api_key_id}",
    response_model=ApiKeyRevokedResponse,
    operation_id="revokePortalApiKey",
)
async def revoke_api_key(
    organization_id: UUID,
    api_key_id: UUID,
    identity: Annotated[BrowserIdentity, Depends(require_portal_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> ApiKeyRevokedResponse:
    try:
        api_key = await revoke_organization_api_key(
            database,
            principal=identity.principal,
            organization_id=organization_id,
            api_key_id=api_key_id,
        )
        await database.commit()
    except AuthorizationError as exc:
        await database.rollback()
        _raise_authorization_error(exc)
    except PortalApiKeyError as exc:
        await database.rollback()
        raise GatewayError(
            "The API key is unavailable.",
            code="api_key_unavailable",
            status_code=status.HTTP_404_NOT_FOUND,
        ) from exc
    if api_key.revoked_at is None:  # pragma: no cover - invariant guarded by service
        raise RuntimeError("Revoked API key has no revocation timestamp")
    return ApiKeyRevokedResponse(id=api_key.id, revoked_at=api_key.revoked_at)
