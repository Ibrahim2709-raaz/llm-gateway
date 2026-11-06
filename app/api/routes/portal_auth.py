from datetime import timedelta
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.portal_sessions import browser_session_response, set_portal_session_cookie
from app.core.config import Settings, get_settings
from app.core.errors import GatewayError
from app.db.session import get_database_session
from app.identity.accounts import (
    AccountConflictError,
    InvalidCredentialsError,
    authenticate_account,
    list_active_memberships,
    register_organization_owner,
    request_email_verification,
    request_password_reset,
    reset_account_password,
    verify_account_email,
)
from app.identity.dependencies import (
    PORTAL_SESSION_COOKIE,
    BrowserIdentity,
    authenticate_portal_session,
    require_portal_csrf,
)
from app.identity.email_delivery import EmailDeliveryError, get_portal_email_delivery
from app.identity.passwords import ScryptPasswordHasher, get_password_hasher
from app.identity.sessions import (
    ActionTokenError,
    issue_browser_session,
    revoke_browser_session,
)
from app.rate_limits.portal import enforce_portal_email_rate_limit
from app.schemas.portal_auth import (
    BrowserSessionResponse,
    CurrentAccountResponse,
    LoginRequest,
    MessageResponse,
    OrganizationSummary,
    PasswordResetConfirmRequest,
    PasswordResetRequestBody,
    PasswordResetRequestResponse,
    RegisterAccountRequest,
    RegisterAccountResponse,
    ResendVerificationRequest,
    ResendVerificationResponse,
    VerifyEmailRequest,
)

router = APIRouter(prefix="/v1/portal/auth", tags=["Portal authentication"])
logger = structlog.get_logger(__name__)


@router.post(
    "/register",
    response_model=RegisterAccountResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="registerPortalAccount",
    dependencies=[Depends(enforce_portal_email_rate_limit)],
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
    if settings.portal_action_token_delivery == "smtp":
        delivery = get_portal_email_delivery(settings)
        try:
            await delivery.send_email_verification(
                recipient=registered.user.email_normalized,
                token=registered.verification.value,
            )
        except EmailDeliveryError as exc:
            raise GatewayError(
                "The account was created, but the verification email could not be sent. "
                "Request another verification email to continue.",
                code="email_delivery_failed",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from exc
    return RegisterAccountResponse(
        user_id=registered.user.id,
        organization_id=registered.organization.id,
        verification_token=(
            registered.verification.value
            if settings.portal_action_token_delivery == "response"
            else None
        ),
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


@router.post(
    "/verify-email/resend",
    response_model=ResendVerificationResponse,
    operation_id="resendPortalEmailVerification",
    dependencies=[Depends(enforce_portal_email_rate_limit)],
)
async def resend_email_verification(
    payload: ResendVerificationRequest,
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ResendVerificationResponse:
    if settings.portal_action_token_delivery == "disabled":
        raise GatewayError(
            "Email verification is unavailable until email delivery is configured.",
            code="email_delivery_unavailable",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    try:
        requested = await request_email_verification(
            database,
            email=payload.email,
            verification_ttl=timedelta(seconds=settings.email_verification_ttl_seconds),
        )
        await database.commit()
    except ValueError as exc:
        await database.rollback()
        raise GatewayError(
            "Account details are invalid.",
            code="invalid_account_details",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        ) from exc

    if settings.portal_action_token_delivery == "smtp" and requested.delivery is not None:
        delivery = get_portal_email_delivery(settings)
        try:
            await delivery.send_email_verification(
                recipient=payload.email.strip().casefold(),
                token=requested.delivery.value,
            )
        except EmailDeliveryError:
            logger.warning("portal_email_delivery_failed", action="email_verification")
    return ResendVerificationResponse(
        message="If the account needs verification, instructions have been sent.",
        verification_token=(
            requested.delivery.value
            if requested.delivery is not None
            and settings.portal_action_token_delivery == "response"
            else None
        ),
    )


@router.post(
    "/password-reset/request",
    response_model=PasswordResetRequestResponse,
    operation_id="requestPortalPasswordReset",
    dependencies=[Depends(enforce_portal_email_rate_limit)],
)
async def password_reset_request(
    payload: PasswordResetRequestBody,
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> PasswordResetRequestResponse:
    if settings.portal_action_token_delivery == "disabled":
        raise GatewayError(
            "Password reset is unavailable until email delivery is configured.",
            code="email_delivery_unavailable",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    try:
        requested = await request_password_reset(
            database,
            email=payload.email,
            reset_ttl=timedelta(seconds=settings.password_reset_ttl_seconds),
        )
        await database.commit()
    except ValueError as exc:
        await database.rollback()
        raise GatewayError(
            "Account details are invalid.",
            code="invalid_account_details",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        ) from exc
    if settings.portal_action_token_delivery == "smtp" and requested.delivery is not None:
        delivery = get_portal_email_delivery(settings)
        try:
            await delivery.send_password_reset(
                recipient=payload.email.strip().casefold(),
                token=requested.delivery.value,
            )
        except EmailDeliveryError:
            logger.warning("portal_email_delivery_failed", action="password_reset")
    return PasswordResetRequestResponse(
        message="If the account exists, password reset instructions are available.",
        reset_token=(
            requested.delivery.value
            if requested.delivery is not None
            and settings.portal_action_token_delivery == "response"
            else None
        ),
    )


@router.post(
    "/password-reset/confirm",
    response_model=MessageResponse,
    operation_id="confirmPortalPasswordReset",
)
async def password_reset_confirm(
    payload: PasswordResetConfirmRequest,
    database: Annotated[AsyncSession, Depends(get_database_session)],
    hasher: Annotated[ScryptPasswordHasher, Depends(get_password_hasher)],
) -> MessageResponse:
    try:
        await reset_account_password(
            database,
            reset_token=payload.token.get_secret_value(),
            new_password=payload.new_password.get_secret_value(),
            password_hasher=hasher,
        )
        await database.commit()
    except (ActionTokenError, InvalidCredentialsError, ValueError) as exc:
        await database.rollback()
        raise GatewayError(
            "The password reset link is invalid or expired.",
            code="invalid_password_reset_token",
        ) from exc
    return MessageResponse(message="Password changed. Sign in again on every device.")


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
    set_portal_session_cookie(response, issued=issued, settings=settings)
    return browser_session_response(
        issued,
        user=account.user,
        organization=account.organization,
        membership=account.membership,
    )


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
