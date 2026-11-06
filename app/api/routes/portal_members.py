from datetime import timedelta
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.portal_sessions import browser_session_response, set_portal_session_cookie
from app.core.config import Settings, get_settings
from app.core.errors import GatewayError
from app.db.session import get_database_session
from app.identity.accounts import AccountConflictError
from app.identity.authorization import AuthorizationError
from app.identity.dependencies import (
    BrowserIdentity,
    authenticate_portal_session,
    require_portal_csrf,
)
from app.identity.email_delivery import EmailDeliveryError, get_portal_email_delivery
from app.identity.members import (
    InvitationError,
    MembershipConflictError,
    accept_invitation_for_new_user,
    accept_organization_invitation,
    issue_organization_invitation,
    list_organization_members,
    update_organization_member,
)
from app.identity.passwords import ScryptPasswordHasher, get_password_hasher
from app.identity.sessions import issue_browser_session
from app.schemas.portal_auth import BrowserSessionResponse
from app.schemas.portal_members import (
    AcceptedInvitationResponse,
    AcceptInvitationRequest,
    AcceptNewUserInvitationRequest,
    CreateInvitationRequest,
    InvitationResponse,
    OrganizationMemberResponse,
    OrganizationMembersResponse,
    UpdateOrganizationMemberRequest,
)

router = APIRouter(prefix="/v1/portal", tags=["Portal members"])


def _raise_authorization_error(error: AuthorizationError) -> NoReturn:
    if error.code == "cross_organization_access":
        raise GatewayError(
            "The organization is unavailable.",
            code="organization_unavailable",
            status_code=status.HTTP_404_NOT_FOUND,
        ) from error
    raise GatewayError(
        "Owner permission is required.",
        code=error.code,
        error_type="authorization_error",
        status_code=status.HTTP_403_FORBIDDEN,
    ) from error


@router.get(
    "/organizations/{organization_id}/members",
    response_model=OrganizationMembersResponse,
    operation_id="listPortalOrganizationMembers",
)
async def list_members(
    organization_id: UUID,
    identity: Annotated[BrowserIdentity, Depends(authenticate_portal_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> OrganizationMembersResponse:
    try:
        members = await list_organization_members(
            database,
            principal=identity.principal,
            organization_id=organization_id,
        )
    except AuthorizationError as exc:
        _raise_authorization_error(exc)
    return OrganizationMembersResponse(
        data=[
            OrganizationMemberResponse(
                membership_id=member.membership.id,
                user_id=member.user.id,
                email=member.user.email_normalized,
                display_name=member.user.display_name,
                role=member.membership.role,
                status=member.membership.status,
            )
            for member in members
        ]
    )


@router.post(
    "/organizations/{organization_id}/invitations",
    response_model=InvitationResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createPortalOrganizationInvitation",
)
async def create_invitation(
    organization_id: UUID,
    payload: CreateInvitationRequest,
    identity: Annotated[BrowserIdentity, Depends(require_portal_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> InvitationResponse:
    if settings.portal_action_token_delivery == "disabled":
        raise GatewayError(
            "Invitations are unavailable until email delivery is configured.",
            code="email_delivery_unavailable",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    try:
        issued = await issue_organization_invitation(
            database,
            principal=identity.principal,
            organization_id=organization_id,
            email=payload.email,
            role=payload.role,
            ttl=timedelta(seconds=settings.organization_invitation_ttl_seconds),
        )
        await database.commit()
    except AuthorizationError as exc:
        await database.rollback()
        _raise_authorization_error(exc)
    except MembershipConflictError as exc:
        await database.rollback()
        raise GatewayError(
            "The user is already an active organization member.",
            code="membership_conflict",
            status_code=status.HTTP_409_CONFLICT,
        ) from exc
    except (IntegrityError, ValueError) as exc:
        await database.rollback()
        raise GatewayError(
            "The invitation could not be created.",
            code="invalid_invitation",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        ) from exc
    if settings.portal_action_token_delivery == "smtp":
        delivery = get_portal_email_delivery(settings)
        try:
            await delivery.send_organization_invitation(
                recipient=issued.invitation.email_normalized,
                organization_name=identity.organization.name,
                token=issued.value,
            )
        except EmailDeliveryError as exc:
            raise GatewayError(
                "The invitation was created, but the email could not be sent. "
                "Create another invitation to retry.",
                code="email_delivery_failed",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from exc
    return InvitationResponse(
        id=issued.invitation.id,
        email=issued.invitation.email_normalized,
        role=issued.invitation.role,
        expires_at=issued.invitation.expires_at.isoformat(),
        invitation_token=(
            issued.value if settings.portal_action_token_delivery == "response" else None
        ),
    )


@router.post(
    "/invitations/accept",
    response_model=AcceptedInvitationResponse,
    operation_id="acceptPortalOrganizationInvitation",
)
async def accept_invitation(
    payload: AcceptInvitationRequest,
    identity: Annotated[BrowserIdentity, Depends(require_portal_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> AcceptedInvitationResponse:
    try:
        membership = await accept_organization_invitation(
            database,
            user=identity.authentication.user,
            value=payload.token.get_secret_value(),
        )
        identity.authentication.session.active_organization_id = membership.organization_id
        await database.commit()
    except InvitationError as exc:
        await database.rollback()
        raise GatewayError(
            "The invitation is invalid or expired.",
            code="invalid_invitation",
        ) from exc
    except IntegrityError as exc:
        await database.rollback()
        raise GatewayError(
            "The organization membership already exists.",
            code="membership_conflict",
            status_code=status.HTTP_409_CONFLICT,
        ) from exc
    return AcceptedInvitationResponse(
        organization_id=membership.organization_id,
        membership_id=membership.id,
        role=membership.role,
    )


@router.post(
    "/invitations/accept-new",
    response_model=BrowserSessionResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="acceptPortalInvitationForNewUser",
)
async def accept_new_user_invitation(
    payload: AcceptNewUserInvitationRequest,
    response: Response,
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    hasher: Annotated[ScryptPasswordHasher, Depends(get_password_hasher)],
) -> BrowserSessionResponse:
    try:
        accepted = await accept_invitation_for_new_user(
            database,
            value=payload.token.get_secret_value(),
            display_name=payload.display_name,
            password=payload.password.get_secret_value(),
            password_hasher=hasher,
        )
        issued = await issue_browser_session(
            database,
            user=accepted.user,
            active_organization_id=accepted.organization.id,
            ttl=timedelta(seconds=settings.browser_session_ttl_seconds),
        )
        await database.commit()
    except InvitationError as exc:
        await database.rollback()
        raise GatewayError(
            "The invitation is invalid or expired.",
            code="invalid_invitation",
        ) from exc
    except AccountConflictError as exc:
        await database.rollback()
        raise GatewayError(
            "An account already uses this email. Sign in to accept the invitation.",
            code="account_exists",
            status_code=status.HTTP_409_CONFLICT,
        ) from exc
    except (IntegrityError, ValueError) as exc:
        await database.rollback()
        raise GatewayError(
            "The invitation could not be accepted.",
            code="invalid_invitation_account",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        ) from exc
    set_portal_session_cookie(response, issued=issued, settings=settings)
    return browser_session_response(
        issued,
        user=accepted.user,
        organization=accepted.organization,
        membership=accepted.membership,
    )


@router.patch(
    "/organizations/{organization_id}/members/{membership_id}",
    response_model=OrganizationMemberResponse,
    operation_id="updatePortalOrganizationMember",
)
async def update_member(
    organization_id: UUID,
    membership_id: UUID,
    payload: UpdateOrganizationMemberRequest,
    identity: Annotated[BrowserIdentity, Depends(require_portal_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> OrganizationMemberResponse:
    try:
        membership = await update_organization_member(
            database,
            principal=identity.principal,
            organization_id=organization_id,
            membership_id=membership_id,
            role=payload.role,
            status=payload.status,
        )
        member_user = await database.get(type(identity.authentication.user), membership.user_id)
        if member_user is None:
            raise MembershipConflictError("Organization member does not exist")
        await database.commit()
    except AuthorizationError as exc:
        await database.rollback()
        _raise_authorization_error(exc)
    except MembershipConflictError as exc:
        await database.rollback()
        raise GatewayError(
            str(exc),
            code="membership_conflict",
            status_code=status.HTTP_409_CONFLICT,
        ) from exc
    return OrganizationMemberResponse(
        membership_id=membership.id,
        user_id=member_user.id,
        email=member_user.email_normalized,
        display_name=member_user.display_name,
        role=membership.role,
        status=membership.status,
    )
