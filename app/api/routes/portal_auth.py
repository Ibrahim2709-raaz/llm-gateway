from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import GatewayError
from app.db.session import get_database_session
from app.identity.accounts import (
    AccountConflictError,
    AccountLogin,
    InvalidCredentialsError,
    authenticate_account,
    list_active_memberships,
    register_organization_owner,
    verify_account_email,
)
from app.identity.dependencies import (
    PORTAL_SESSION_COOKIE,
    BrowserIdentity,
    authenticate_portal_session,
    require_portal_csrf,
)
from app.identity.passwords import ScryptPasswordHasher
from app.identity.sessions import (
    ActionTokenError,
    IssuedBrowserSession,
    issue_browser_session,
    revoke_browser_session,
)
from app.schemas.portal_auth import (
    BrowserSessionResponse,
    CurrentAccountResponse,
    LoginRequest,
    MessageResponse,
    OrganizationSummary,
    RegisterAccountRequest,
    RegisterAccountResponse,
    VerifyEmailRequest,
)

router = APIRouter(prefix="/v1/portal/auth", tags=["Portal authentication"])


def get_password_hasher() -> ScryptPasswordHasher:
    return ScryptPasswordHasher()


def _set_session_cookie(
    response: Response, *, issued: IssuedBrowserSession, settings: Settings
) -> None:
    response.set_cookie(
        key=PORTAL_SESSION_COOKIE,
        value=issued.session_token,
        max_age=settings.browser_session_ttl_seconds,
        expires=issued.session.expires_at,
        path="/v1/portal",
        secure=settings.app_env == "production",
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"


def _session_response(issued: IssuedBrowserSession, login: AccountLogin) -> BrowserSessionResponse:
    user = login.user
    organization = login.organization
    membership = login.membership
    return BrowserSessionResponse(
        user_id=user.id,
        email=user.email_normalized,
        display_name=user.display_name,
        active_organization=OrganizationSummary(
            id=organization.id,
            slug=organization.slug,
            name=organization.name,
            role=membership.role,
        ),
        csrf_token=issued.csrf_token,
        expires_at=issued.session.expires_at.isoformat(),
    )


@router.post(
    "/register",
    response_model=RegisterAccountResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="registerPortalAccount",
)
async def register_account(
    payload: RegisterAccountRequest,
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    hasher: Annotated[ScryptPasswordHasher, Depends(get_password_hasher)],
) -> RegisterAccountResponse:
    if settings.portal_action_token_delivery == "disabled":
        raise GatewayError(
            "Account registration is unavailable until email delivery is configured.",
            code="email_delivery_unavailable",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    try:
        registered = await register_organization_owner(
            database,
            email=payload.email,
            display_name=payload.display_name,
            password=payload.password.get_secret_value(),
            organization_name=payload.organization_name,
            organization_slug=payload.organization_slug,
            verification_ttl=timedelta(seconds=settings.email_verification_ttl_seconds),
            password_hasher=hasher,
        )
        await database.commit()
    except AccountConflictError as exc:
        await database.rollback()
        raise GatewayError(
            "An account or organization already uses those details.",
            code="account_conflict",
            status_code=status.HTTP_409_CONFLICT,
        ) from exc
    except IntegrityError as exc:
        await database.rollback()
        raise GatewayError(
            "An account or organization already uses those details.",
            code="account_conflict",
            status_code=status.HTTP_409_CONFLICT,
        ) from exc
    except ValueError as exc:
        await database.rollback()
        raise GatewayError(
            "Account details are invalid.",
            code="invalid_account_details",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        ) from exc
    return RegisterAccountResponse(
        user_id=registered.user.id,
        organization_id=registered.organization.id,
        verification_token=registered.verification.value,
    )


@router.post(
    "/verify-email",
    response_model=MessageResponse,
    operation_id="verifyPortalEmail",
)
async def verify_email(
    payload: VerifyEmailRequest,
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> MessageResponse:
    try:
        await verify_account_email(
            database,
            verification_token=payload.token.get_secret_value(),
        )
        await database.commit()
    except (ActionTokenError, InvalidCredentialsError) as exc:
        await database.rollback()
        raise GatewayError(
            "The verification link is invalid or expired.",
            code="invalid_verification_token",
        ) from exc
    return MessageResponse(message="Email verified. You can now sign in.")


@router.post("/login", response_model=BrowserSessionResponse, operation_id="loginPortalAccount")
async def login(
    payload: LoginRequest,
    response: Response,
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    hasher: Annotated[ScryptPasswordHasher, Depends(get_password_hasher)],
) -> BrowserSessionResponse:
    try:
        account = await authenticate_account(
            database,
            email=payload.email,
            password=payload.password.get_secret_value(),
            password_hasher=hasher,
        )
        issued = await issue_browser_session(
            database,
            user=account.user,
            active_organization_id=account.organization.id,
            ttl=timedelta(seconds=settings.browser_session_ttl_seconds),
        )
        await database.commit()
    except (InvalidCredentialsError, ValueError) as exc:
        await database.rollback()
        raise GatewayError(
            "Email or password is incorrect.",
            code="invalid_credentials",
            error_type="authentication_error",
            status_code=status.HTTP_401_UNAUTHORIZED,
        ) from exc
    _set_session_cookie(response, issued=issued, settings=settings)
    return _session_response(issued, account)


@router.post("/logout", response_model=MessageResponse, operation_id="logoutPortalAccount")
async def logout(
    response: Response,
    identity: Annotated[BrowserIdentity, Depends(require_portal_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> MessageResponse:
    await revoke_browser_session(database, stored=identity.authentication.session)
    await database.commit()
    response.delete_cookie(
        PORTAL_SESSION_COOKIE,
        path="/v1/portal",
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
    return MessageResponse(message="Signed out.")


@router.get("/me", response_model=CurrentAccountResponse, operation_id="getCurrentPortalAccount")
async def current_account(
    identity: Annotated[BrowserIdentity, Depends(authenticate_portal_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> CurrentAccountResponse:
    memberships = await list_active_memberships(
        database, user_id=identity.authentication.user.id
    )
    return CurrentAccountResponse(
        user_id=identity.authentication.user.id,
        email=identity.authentication.user.email_normalized,
        display_name=identity.authentication.user.display_name,
        active_organization_id=identity.organization.id,
        organizations=[
            OrganizationSummary(
                id=organization.id,
                slug=organization.slug,
                name=organization.name,
                role=membership.role,
            )
            for membership, organization in memberships
        ],
    )


@router.post(
    "/organizations/{organization_id}/activate",
    response_model=MessageResponse,
    operation_id="activatePortalOrganization",
)
async def activate_organization(
    organization_id: UUID,
    identity: Annotated[BrowserIdentity, Depends(require_portal_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> MessageResponse:
    memberships = await list_active_memberships(
        database, user_id=identity.authentication.user.id
    )
    if organization_id not in {organization.id for _, organization in memberships}:
        raise GatewayError(
            "The organization is unavailable.",
            code="organization_unavailable",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    identity.authentication.session.active_organization_id = organization_id
    await database.commit()
    return MessageResponse(message="Active organization changed.")
