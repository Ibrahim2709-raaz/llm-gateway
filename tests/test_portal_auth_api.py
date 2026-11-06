from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import Response
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.api.routes.portal_auth import (
    activate_organization,
    current_account,
    login,
    logout,
    password_reset_confirm,
    password_reset_request,
    register_account,
    resend_email_verification,
    verify_email,
)
from app.core.config import Settings
from app.core.errors import GatewayError
from app.db.models import BrowserSession, Organization, OrganizationMembership, User
from app.identity.accounts import (
    AccountConflictError,
    AccountLogin,
    EmailVerificationRequest,
    InvalidCredentialsError,
    PasswordResetRequest,
    RegisteredAccount,
)
from app.identity.authorization import Principal
from app.identity.dependencies import BrowserIdentity
from app.identity.email_delivery import EmailDeliveryError
from app.identity.passwords import ScryptPasswordHasher
from app.identity.sessions import (
    ActionTokenError,
    AuthenticatedBrowserSession,
    IssuedBrowserSession,
)
from app.identity.tokens import issue_opaque_token
from app.main import create_app
from app.schemas.portal_auth import (
    LoginRequest,
    PasswordResetConfirmRequest,
    PasswordResetRequestBody,
    RegisterAccountRequest,
    ResendVerificationRequest,
    VerifyEmailRequest,
)

NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)


def identity_graph() -> tuple[User, Organization, OrganizationMembership]:
    user = User(
        id=uuid4(),
        email_normalized="owner@example.com",
        display_name="Owner",
        status="active",
        email_verified_at=NOW,
    )
    organization = Organization(
        id=uuid4(),
        client_id=uuid4(),
        slug="example-team",
        name="Example Team",
        status="active",
    )
    membership = OrganizationMembership(
        id=uuid4(),
        organization_id=organization.id,
        user_id=user.id,
        role="owner",
        status="active",
    )
    return user, organization, membership


def browser_identity() -> BrowserIdentity:
    user, organization, membership = identity_graph()
    stored = BrowserSession(
        id=uuid4(),
        user_id=user.id,
        active_organization_id=organization.id,
        token_prefix="portal_session_example",
        token_hash="session-hash",
        csrf_token_hash="csrf-hash",
        created_at=NOW,
        expires_at=NOW + timedelta(days=1),
    )
    return BrowserIdentity(
        authentication=AuthenticatedBrowserSession(session=stored, user=user),
        organization=organization,
        membership=membership,
        principal=Principal(
            actor_type="user",
            actor_id=user.id,
            organization_id=organization.id,
            organization_role="owner",
        ),
    )


def smtp_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        portal_action_token_delivery="smtp",
        portal_public_base_url="https://gateway.example.com",
        smtp_host="smtp.example.com",
        smtp_from_email="gateway@example.com",
    )


def registration_payload() -> RegisterAccountRequest:
    return RegisterAccountRequest(
        email="owner@example.com",
        display_name="Owner",
        password="a-long-enough-password",
        organization_name="Example Team",
        organization_slug="example-team",
    )


def test_portal_auth_routes_are_in_openapi_contract() -> None:
    with TestClient(create_app()) as client:
        openapi = client.get("/openapi.json").json()
    paths = openapi["paths"]
    assert "/v1/portal/auth/register" in paths
    assert "/v1/portal/auth/verify-email" in paths
    assert "/v1/portal/auth/verify-email/resend" in paths
    assert "/v1/portal/auth/login" in paths
    assert "/v1/portal/auth/password-reset/request" in paths
    assert "/v1/portal/auth/password-reset/confirm" in paths
    assert "/v1/portal/auth/logout" in paths
    assert "/v1/portal/auth/me" in paths


@pytest.mark.anyio
async def test_register_returns_development_verification_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, organization, membership = identity_graph()
    secret = issue_opaque_token(namespace="email_verification")
    verification = MagicMock(value=secret.value)
    registered = RegisteredAccount(
        client=MagicMock(id=organization.client_id),
        organization=organization,
        user=user,
        membership=membership,
        verification=verification,
    )
    operation = AsyncMock(return_value=registered)
    monkeypatch.setattr("app.api.routes.portal_auth.register_organization_owner", operation)
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()

    result = await register_account(
        RegisterAccountRequest(
            email="owner@example.com",
            display_name="Owner",
            password="a-long-enough-password",
            organization_name="Example Team",
            organization_slug="example-team",
        ),
        database,
        Settings(_env_file=None, app_env="test"),
        ScryptPasswordHasher(log_n=14),
    )
    assert result.verification_token == secret.value
    database.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_registration_is_disabled_without_token_delivery() -> None:
    database = MagicMock()
    with pytest.raises(GatewayError) as error:
        await register_account(
            RegisterAccountRequest(
                email="owner@example.com",
                display_name="Owner",
                password="a-long-enough-password",
                organization_name="Example Team",
                organization_slug="example-team",
            ),
            database,
            Settings(_env_file=None, app_env="test", portal_action_token_delivery="disabled"),
            ScryptPasswordHasher(log_n=14),
        )
    assert error.value.status_code == 503


@pytest.mark.anyio
async def test_registration_sends_verification_email_in_smtp_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, organization, membership = identity_graph()
    secret = issue_opaque_token(namespace="email_verification")
    registered = RegisteredAccount(
        client=MagicMock(id=organization.client_id),
        organization=organization,
        user=user,
        membership=membership,
        verification=MagicMock(value=secret.value),
    )
    monkeypatch.setattr(
        "app.api.routes.portal_auth.register_organization_owner",
        AsyncMock(return_value=registered),
    )
    delivery = MagicMock()
    delivery.send_email_verification = AsyncMock()
    monkeypatch.setattr(
        "app.api.routes.portal_auth.get_portal_email_delivery",
        MagicMock(return_value=delivery),
    )
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()

    result = await register_account(
        RegisterAccountRequest(
            email=user.email_normalized,
            display_name=user.display_name,
            password="a-long-enough-password",
            organization_name=organization.name,
            organization_slug=organization.slug,
        ),
        database,
        smtp_settings(),
        ScryptPasswordHasher(log_n=14),
    )

    assert result.verification_token is None
    delivery.send_email_verification.assert_awaited_once_with(
        recipient=user.email_normalized,
        token=secret.value,
    )
    database.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_registration_delivery_failure_preserves_committed_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, organization, membership = identity_graph()
    registered = RegisteredAccount(
        client=MagicMock(id=organization.client_id),
        organization=organization,
        user=user,
        membership=membership,
        verification=MagicMock(value="email_verification_secret"),
    )
    monkeypatch.setattr(
        "app.api.routes.portal_auth.register_organization_owner",
        AsyncMock(return_value=registered),
    )
    delivery = MagicMock()
    delivery.send_email_verification = AsyncMock(
        side_effect=EmailDeliveryError("SMTP delivery failed")
    )
    monkeypatch.setattr(
        "app.api.routes.portal_auth.get_portal_email_delivery",
        MagicMock(return_value=delivery),
    )
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()

    with pytest.raises(GatewayError) as error:
        await register_account(
            RegisterAccountRequest(
                email=user.email_normalized,
                display_name=user.display_name,
                password="a-long-enough-password",
                organization_name=organization.name,
                organization_slug=organization.slug,
            ),
            database,
            smtp_settings(),
            ScryptPasswordHasher(log_n=14),
        )

    assert error.value.status_code == 503
    assert error.value.code == "email_delivery_failed"
    database.commit.assert_awaited_once()
    database.rollback.assert_not_awaited()


@pytest.mark.anyio
async def test_verification_resend_is_generic_and_sends_when_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = issue_opaque_token(namespace="email_verification")
    monkeypatch.setattr(
        "app.api.routes.portal_auth.request_email_verification",
        AsyncMock(return_value=EmailVerificationRequest(delivery=MagicMock(value=secret.value))),
    )
    delivery = MagicMock()
    delivery.send_email_verification = AsyncMock()
    monkeypatch.setattr(
        "app.api.routes.portal_auth.get_portal_email_delivery",
        MagicMock(return_value=delivery),
    )
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()

    result = await resend_email_verification(
        ResendVerificationRequest(email="OWNER@example.com"),
        database,
        smtp_settings(),
    )

    assert result.verification_token is None
    assert result.message.startswith("If the account needs verification")
    delivery.send_email_verification.assert_awaited_once_with(
        recipient="owner@example.com",
        token=secret.value,
    )


@pytest.mark.anyio
async def test_password_reset_request_does_not_reveal_unknown_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation = AsyncMock(return_value=PasswordResetRequest(delivery=None))
    monkeypatch.setattr("app.api.routes.portal_auth.request_password_reset", operation)
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()
    response = await password_reset_request(
        PasswordResetRequestBody(email="missing@example.com"),
        database,
        Settings(_env_file=None, app_env="test"),
    )
    assert response.reset_token is None
    assert response.message.startswith("If the account exists")


@pytest.mark.anyio
async def test_password_reset_sends_email_without_returning_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = issue_opaque_token(namespace="password_reset")
    monkeypatch.setattr(
        "app.api.routes.portal_auth.request_password_reset",
        AsyncMock(return_value=PasswordResetRequest(delivery=MagicMock(value=secret.value))),
    )
    delivery = MagicMock()
    delivery.send_password_reset = AsyncMock()
    monkeypatch.setattr(
        "app.api.routes.portal_auth.get_portal_email_delivery",
        MagicMock(return_value=delivery),
    )
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()

    result = await password_reset_request(
        PasswordResetRequestBody(email="OWNER@example.com"),
        database,
        smtp_settings(),
    )

    assert result.reset_token is None
    delivery.send_password_reset.assert_awaited_once_with(
        recipient="owner@example.com",
        token=secret.value,
    )


@pytest.mark.anyio
async def test_password_reset_delivery_failure_remains_generic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = issue_opaque_token(namespace="password_reset")
    monkeypatch.setattr(
        "app.api.routes.portal_auth.request_password_reset",
        AsyncMock(return_value=PasswordResetRequest(delivery=MagicMock(value=secret.value))),
    )
    delivery = MagicMock()
    delivery.send_password_reset = AsyncMock(
        side_effect=EmailDeliveryError("SMTP delivery failed")
    )
    monkeypatch.setattr(
        "app.api.routes.portal_auth.get_portal_email_delivery",
        MagicMock(return_value=delivery),
    )
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()

    result = await password_reset_request(
        PasswordResetRequestBody(email="owner@example.com"),
        database,
        smtp_settings(),
    )

    assert result.reset_token is None
    assert result.message.startswith("If the account exists")
    database.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_login_sets_http_only_session_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    user, organization, membership = identity_graph()
    account = AccountLogin(user=user, organization=organization, membership=membership)
    monkeypatch.setattr(
        "app.api.routes.portal_auth.authenticate_account", AsyncMock(return_value=account)
    )
    secret = issue_opaque_token(namespace="portal_session")
    csrf = issue_opaque_token(namespace="csrf")
    stored = BrowserSession(
        id=uuid4(),
        user_id=user.id,
        active_organization_id=organization.id,
        token_prefix=secret.prefix,
        token_hash=secret.hash,
        csrf_token_hash=csrf.hash,
        created_at=NOW,
        expires_at=NOW + timedelta(days=7),
    )
    issued = IssuedBrowserSession(stored, secret.value, csrf.value)
    monkeypatch.setattr(
        "app.api.routes.portal_auth.issue_browser_session", AsyncMock(return_value=issued)
    )
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()
    response = Response()

    result = await login(
        LoginRequest(email=user.email_normalized, password="a-long-enough-password"),
        response,
        database,
        Settings(_env_file=None, app_env="test"),
        ScryptPasswordHasher(log_n=14),
    )
    cookie = response.headers["set-cookie"]
    assert "llm_gateway_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert result.csrf_token == csrf.value
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.anyio
async def test_logout_revokes_session_and_clears_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    identity = browser_identity()
    revoke = AsyncMock()
    monkeypatch.setattr("app.api.routes.portal_auth.revoke_browser_session", revoke)
    database = MagicMock()
    database.commit = AsyncMock()
    response = Response()

    result = await logout(response, identity, database)
    assert result.message == "Signed out."
    revoke.assert_awaited_once_with(database, stored=identity.authentication.session)
    assert "llm_gateway_session=" in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"]


@pytest.mark.anyio
async def test_current_account_lists_only_service_result(monkeypatch: pytest.MonkeyPatch) -> None:
    identity = browser_identity()
    listing = AsyncMock(return_value=[(identity.membership, identity.organization)])
    monkeypatch.setattr("app.api.routes.portal_auth.list_active_memberships", listing)
    result = await current_account(identity, MagicMock())
    assert result.active_organization_id == identity.organization.id
    assert [item.id for item in result.organizations] == [identity.organization.id]


@pytest.mark.anyio
async def test_activate_organization_rejects_cross_organization_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = browser_identity()
    monkeypatch.setattr(
        "app.api.routes.portal_auth.list_active_memberships",
        AsyncMock(return_value=[(identity.membership, identity.organization)]),
    )
    database = MagicMock()
    database.commit = AsyncMock()
    with pytest.raises(GatewayError) as error:
        await activate_organization(uuid4(), identity, database)
    assert error.value.status_code == 404
    database.commit.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("failure", "expected_status"),
    [
        (AccountConflictError("duplicate"), 409),
        (IntegrityError("insert", {}, RuntimeError("duplicate")), 409),
        (ValueError("invalid"), 422),
    ],
)
async def test_registration_maps_account_creation_failures(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected_status: int,
) -> None:
    monkeypatch.setattr(
        "app.api.routes.portal_auth.register_organization_owner",
        AsyncMock(side_effect=failure),
    )
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()

    with pytest.raises(GatewayError) as error:
        await register_account(
            registration_payload(),
            database,
            Settings(_env_file=None, app_env="test"),
            ScryptPasswordHasher(log_n=14),
        )

    assert error.value.status_code == expected_status
    database.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_verify_email_success_and_invalid_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr("app.api.routes.portal_auth.verify_account_email", operation)
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()

    result = await verify_email(
        VerifyEmailRequest(token="email_verification_secret"),
        database,
    )
    assert result.message.startswith("Email verified")
    database.commit.assert_awaited_once()

    operation.side_effect = ActionTokenError("expired")
    with pytest.raises(GatewayError) as error:
        await verify_email(
            VerifyEmailRequest(token="email_verification_expired"),
            database,
        )
    assert error.value.code == "invalid_verification_token"
    database.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_verification_resend_disabled_and_invalid_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()
    with pytest.raises(GatewayError) as disabled:
        await resend_email_verification(
            ResendVerificationRequest(email="owner@example.com"),
            database,
            Settings(
                _env_file=None,
                app_env="test",
                portal_action_token_delivery="disabled",
            ),
        )
    assert disabled.value.status_code == 503

    monkeypatch.setattr(
        "app.api.routes.portal_auth.request_email_verification",
        AsyncMock(side_effect=ValueError("invalid")),
    )
    with pytest.raises(GatewayError) as invalid:
        await resend_email_verification(
            ResendVerificationRequest(email="owner@example.com"),
            database,
            Settings(_env_file=None, app_env="test"),
        )
    assert invalid.value.status_code == 422
    database.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_verification_resend_swallows_smtp_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested = EmailVerificationRequest(delivery=MagicMock(value="verification-secret"))
    monkeypatch.setattr(
        "app.api.routes.portal_auth.request_email_verification",
        AsyncMock(return_value=requested),
    )
    delivery = MagicMock()
    delivery.send_email_verification = AsyncMock(
        side_effect=EmailDeliveryError("SMTP delivery failed")
    )
    monkeypatch.setattr(
        "app.api.routes.portal_auth.get_portal_email_delivery",
        MagicMock(return_value=delivery),
    )
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()

    result = await resend_email_verification(
        ResendVerificationRequest(email="owner@example.com"),
        database,
        smtp_settings(),
    )

    assert result.verification_token is None
    assert result.message.startswith("If the account needs verification")


@pytest.mark.anyio
async def test_password_reset_request_disabled_and_invalid_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()
    with pytest.raises(GatewayError) as disabled:
        await password_reset_request(
            PasswordResetRequestBody(email="owner@example.com"),
            database,
            Settings(
                _env_file=None,
                app_env="test",
                portal_action_token_delivery="disabled",
            ),
        )
    assert disabled.value.status_code == 503

    monkeypatch.setattr(
        "app.api.routes.portal_auth.request_password_reset",
        AsyncMock(side_effect=ValueError("invalid")),
    )
    with pytest.raises(GatewayError) as invalid:
        await password_reset_request(
            PasswordResetRequestBody(email="owner@example.com"),
            database,
            Settings(_env_file=None, app_env="test"),
        )
    assert invalid.value.status_code == 422


@pytest.mark.anyio
async def test_password_reset_confirm_success_and_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr("app.api.routes.portal_auth.reset_account_password", operation)
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()
    request = PasswordResetConfirmRequest(
        token="password_reset_secret",
        new_password="a-new-long-enough-password",
    )

    result = await password_reset_confirm(
        request,
        database,
        ScryptPasswordHasher(log_n=14),
    )
    assert result.message.startswith("Password changed")

    operation.side_effect = InvalidCredentialsError("unavailable")
    with pytest.raises(GatewayError) as error:
        await password_reset_confirm(
            request,
            database,
            ScryptPasswordHasher(log_n=14),
        )
    assert error.value.code == "invalid_password_reset_token"
    database.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_login_maps_invalid_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.api.routes.portal_auth.authenticate_account",
        AsyncMock(side_effect=InvalidCredentialsError("incorrect")),
    )
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()

    with pytest.raises(GatewayError) as error:
        await login(
            LoginRequest(email="owner@example.com", password="a-long-enough-password"),
            Response(),
            database,
            Settings(_env_file=None, app_env="test"),
            ScryptPasswordHasher(log_n=14),
        )
    assert error.value.status_code == 401
    database.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_activate_organization_updates_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = browser_identity()
    monkeypatch.setattr(
        "app.api.routes.portal_auth.list_active_memberships",
        AsyncMock(return_value=[(identity.membership, identity.organization)]),
    )
    database = MagicMock()
    database.commit = AsyncMock()

    result = await activate_organization(
        identity.organization.id,
        identity,
        database,
    )

    assert result.message == "Active organization changed."
    assert identity.authentication.session.active_organization_id == identity.organization.id
    database.commit.assert_awaited_once()
