from fastapi import Response

from app.core.config import Settings
from app.db.models import Organization, OrganizationMembership, User
from app.identity.dependencies import PORTAL_SESSION_COOKIE
from app.identity.sessions import IssuedBrowserSession
from app.schemas.portal_auth import BrowserSessionResponse, OrganizationSummary


def set_portal_session_cookie(
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


def browser_session_response(
    issued: IssuedBrowserSession,
    *,
    user: User,
    organization: Organization,
    membership: OrganizationMembership,
) -> BrowserSessionResponse:
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
