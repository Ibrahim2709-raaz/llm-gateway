from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.routes.portal_keys import create_api_key, list_api_keys, revoke_api_key
from app.core.config import Settings
from app.core.errors import GatewayError
from app.db.models import ApiKey, BrowserSession, Organization, OrganizationMembership, User
from app.identity.authorization import AuthorizationError, Principal
from app.identity.dependencies import BrowserIdentity
from app.identity.portal_keys import CreatedOrganizationApiKey, PortalApiKeyError
from app.identity.sessions import AuthenticatedBrowserSession
from app.main import create_app
from app.schemas.portal_keys import CreateApiKeyRequest

NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)


def browser_identity() -> BrowserIdentity:
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
    session = BrowserSession(
        id=uuid4(),
        user_id=user.id,
        active_organization_id=organization.id,
        token_prefix="prefix",
        token_hash="hash",
        csrf_token_hash="csrf",
        created_at=NOW,
        expires_at=NOW + timedelta(days=1),
    )
    return BrowserIdentity(
        authentication=AuthenticatedBrowserSession(session=session, user=user),
        organization=organization,
        membership=membership,
        principal=Principal(
            actor_type="user",
            actor_id=user.id,
            organization_id=organization.id,
            organization_role="owner",
        ),
    )


def portal_api_key(identity: BrowserIdentity, *, revoked: bool = False) -> ApiKey:
    return ApiKey(
        id=uuid4(),
        client_id=identity.organization.client_id,
        name="Production",
        key_prefix="gw_live_example",
        key_hash="digest",
        created_at=NOW,
        revoked_at=NOW if revoked else None,
    )


def test_portal_api_key_routes_are_in_openapi_contract() -> None:
    with TestClient(create_app()) as client:
        paths = client.get("/openapi.json").json()["paths"]
    collection = "/v1/portal/organizations/{organization_id}/api-keys"
    item = "/v1/portal/organizations/{organization_id}/api-keys/{api_key_id}"
    assert set(paths[collection]) >= {"get", "post"}
    assert "delete" in paths[item]


@pytest.mark.anyio
async def test_create_api_key_serializes_one_time_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = browser_identity()
    api_key = portal_api_key(identity)
    monkeypatch.setattr(
        "app.api.routes.portal_keys.create_organization_api_key",
        AsyncMock(return_value=CreatedOrganizationApiKey(api_key, "gw_live_secret")),
    )
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()

    result = await create_api_key(
        identity.organization.id,
        CreateApiKeyRequest(name="Production"),
        identity,
        database,
        Settings(_env_file=None, app_env="test"),
    )

    assert result.id == api_key.id
    assert result.token == "gw_live_secret"
    database.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_list_api_keys_serializes_safe_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = browser_identity()
    api_key = portal_api_key(identity)
    monkeypatch.setattr(
        "app.api.routes.portal_keys.list_organization_api_keys",
        AsyncMock(return_value=[api_key]),
    )

    result = await list_api_keys(identity.organization.id, identity, MagicMock())

    assert len(result.data) == 1
    assert result.data[0].key_prefix == api_key.key_prefix
    assert not hasattr(result.data[0], "token")


@pytest.mark.anyio
async def test_revoke_api_key_returns_revocation_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = browser_identity()
    api_key = portal_api_key(identity, revoked=True)
    monkeypatch.setattr(
        "app.api.routes.portal_keys.revoke_organization_api_key",
        AsyncMock(return_value=api_key),
    )
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()

    result = await revoke_api_key(
        identity.organization.id,
        api_key.id,
        identity,
        database,
    )

    assert result.id == api_key.id
    assert result.revoked_at == NOW
    database.commit.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("code", "expected_status"),
    [("cross_organization_access", 404), ("permission_denied", 403)],
)
async def test_list_api_keys_maps_authorization_failures(
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    expected_status: int,
) -> None:
    identity = browser_identity()
    monkeypatch.setattr(
        "app.api.routes.portal_keys.list_organization_api_keys",
        AsyncMock(side_effect=AuthorizationError(code, "denied")),
    )

    with pytest.raises(GatewayError) as error:
        await list_api_keys(identity.organization.id, identity, MagicMock())

    assert error.value.status_code == expected_status


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("failure", "expected_status"),
    [
        (AuthorizationError("permission_denied", "denied"), 403),
        (PortalApiKeyError("unavailable"), 422),
    ],
)
async def test_create_api_key_maps_service_failures(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected_status: int,
) -> None:
    identity = browser_identity()
    monkeypatch.setattr(
        "app.api.routes.portal_keys.create_organization_api_key",
        AsyncMock(side_effect=failure),
    )
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()

    with pytest.raises(GatewayError) as error:
        await create_api_key(
            identity.organization.id,
            CreateApiKeyRequest(name="Production"),
            identity,
            database,
            Settings(_env_file=None, app_env="test"),
        )

    assert error.value.status_code == expected_status
    database.rollback.assert_awaited_once()
