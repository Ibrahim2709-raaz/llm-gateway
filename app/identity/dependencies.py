from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Header, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import GatewayError
from app.db.models import Organization, OrganizationMembership
from app.db.session import get_database_session
from app.identity.authorization import Principal
from app.identity.sessions import (
    AuthenticatedBrowserSession,
    BrowserAuthenticationError,
    authenticate_browser_session,
    verify_csrf_token,
)

PORTAL_SESSION_COOKIE = "llm_gateway_session"
PORTAL_CSRF_HEADER = "X-CSRF-Token"


@dataclass(frozen=True, slots=True)
class BrowserIdentity:
    authentication: AuthenticatedBrowserSession
    organization: Organization
    membership: OrganizationMembership
    principal: Principal


async def authenticate_portal_session(
    request: Request,
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> BrowserIdentity:
    session_token = request.cookies.get(PORTAL_SESSION_COOKIE, "")
    try:
        authenticated = await authenticate_browser_session(
            database,
            session_token=session_token,
            touch_interval=timedelta(seconds=settings.browser_session_touch_interval_seconds),
        )
    except BrowserAuthenticationError as exc:
        raise GatewayError(
            "A valid portal session is required.",
            code="invalid_portal_session",
            error_type="authentication_error",
            status_code=status.HTTP_401_UNAUTHORIZED,
        ) from exc

    organization_id = authenticated.session.active_organization_id
    if organization_id is None:
        raise GatewayError(
            "The portal session has no active organization.",
            code="active_organization_required",
            error_type="authentication_error",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    statement = (
        select(OrganizationMembership, Organization)
        .join(Organization, Organization.id == OrganizationMembership.organization_id)
        .where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.user_id == authenticated.user.id,
            OrganizationMembership.status == "active",
            Organization.status == "active",
        )
    )
    row = (await database.execute(statement)).one_or_none()
    if row is None:
        raise GatewayError(
            "The active organization is unavailable.",
            code="active_organization_unavailable",
            error_type="authentication_error",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    membership, organization = row
    return BrowserIdentity(
        authentication=authenticated,
        membership=membership,
        organization=organization,
        principal=Principal(
            actor_type="user",
            actor_id=authenticated.user.id,
            organization_id=organization.id,
            organization_role=membership.role,
            platform_role=authenticated.user.platform_role,
        ),
    )


async def require_portal_csrf(
    identity: Annotated[BrowserIdentity, Depends(authenticate_portal_session)],
    csrf_token: Annotated[str | None, Header(alias=PORTAL_CSRF_HEADER)] = None,
) -> BrowserIdentity:
    try:
        verify_csrf_token(identity.authentication.session, csrf_token or "")
    except BrowserAuthenticationError as exc:
        raise GatewayError(
            "A valid CSRF token is required.",
            code="invalid_csrf_token",
            error_type="authentication_error",
            status_code=status.HTTP_403_FORBIDDEN,
        ) from exc
    return identity
