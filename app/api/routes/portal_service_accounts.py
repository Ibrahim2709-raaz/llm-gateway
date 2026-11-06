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
from app.identity.service_accounts import (
    ServiceAccountError,
    create_service_account,
    list_service_accounts,
    revoke_service_account,
)
from app.schemas.portal_service_accounts import (
    CreatedServiceAccountResponse,
    CreateServiceAccountRequest,
    ServiceAccountListResponse,
    ServiceAccountRevokedResponse,
    ServiceAccountSummary,
)

router = APIRouter(
    prefix="/v1/portal/organizations/{organization_id}/service-accounts",
    tags=["Portal service accounts"],
)


def _raise_authorization_error(error: AuthorizationError) -> NoReturn:
    if error.code == "cross_organization_access":
        raise GatewayError(
            "The organization is unavailable.",
            code="organization_unavailable",
            status_code=status.HTTP_404_NOT_FOUND,
        ) from error
    raise GatewayError(
        "Owner permission is required to manage service accounts.",
        code=error.code,
        error_type="authorization_error",
        status_code=status.HTTP_403_FORBIDDEN,
    ) from error


@router.post(
    "",
    response_model=CreatedServiceAccountResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createPortalServiceAccount",
)
async def create_portal_service_account(
    organization_id: UUID,
    payload: CreateServiceAccountRequest,
    identity: Annotated[BrowserIdentity, Depends(require_portal_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> CreatedServiceAccountResponse:
    try:
        created = await create_service_account(
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
    except ServiceAccountError as exc:
        await database.rollback()
        raise GatewayError(
            "The service account could not be created.",
            code="invalid_service_account_request",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        ) from exc
    return CreatedServiceAccountResponse(
        id=created.service_account.id,
        name=created.service_account.name,
        role=created.service_account.role,
        status=created.service_account.status,
        key_prefix=created.api_key.key_prefix,
        token=created.token,
        created_at=created.service_account.created_at,
    )


@router.get(
    "",
    response_model=ServiceAccountListResponse,
    operation_id="listPortalServiceAccounts",
)
async def list_portal_service_accounts(
    organization_id: UUID,
    identity: Annotated[BrowserIdentity, Depends(authenticate_portal_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> ServiceAccountListResponse:
    try:
        accounts = await list_service_accounts(
            database,
            principal=identity.principal,
            organization_id=organization_id,
        )
    except AuthorizationError as exc:
        _raise_authorization_error(exc)
    return ServiceAccountListResponse(
        data=[
            ServiceAccountSummary(
                id=item.service_account.id,
                name=item.service_account.name,
                role=item.service_account.role,
                status=item.service_account.status,
                key_prefix=item.api_key.key_prefix if item.api_key is not None else None,
                created_at=item.service_account.created_at,
                last_used_at=item.service_account.last_used_at,
            )
            for item in accounts
        ]
    )


@router.delete(
    "/{service_account_id}",
    response_model=ServiceAccountRevokedResponse,
    operation_id="revokePortalServiceAccount",
)
async def revoke_portal_service_account(
    organization_id: UUID,
    service_account_id: UUID,
    identity: Annotated[BrowserIdentity, Depends(require_portal_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> ServiceAccountRevokedResponse:
    try:
        account = await revoke_service_account(
            database,
            principal=identity.principal,
            organization_id=organization_id,
            service_account_id=service_account_id,
        )
        await database.commit()
    except AuthorizationError as exc:
        await database.rollback()
        _raise_authorization_error(exc)
    except ServiceAccountError as exc:
        await database.rollback()
        raise GatewayError(
            "The service account is unavailable.",
            code="service_account_unavailable",
            status_code=status.HTTP_404_NOT_FOUND,
        ) from exc
    return ServiceAccountRevokedResponse(id=account.id, status=account.status)
