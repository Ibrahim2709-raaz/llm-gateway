from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.routes.portal_admin import list_admin_models
from app.core.config import Settings
from app.core.errors import GatewayError
from app.identity.authorization import Principal
from app.platform.model_catalog import build_model_catalog


def operator_identity() -> MagicMock:
    identity = MagicMock()
    identity.principal = Principal(
        actor_type="user",
        actor_id=uuid4(),
        organization_id=uuid4(),
        organization_role="owner",
        platform_role="platform_operator",
    )
    return identity


def request_with_providers(*names: str) -> MagicMock:
    providers = tuple(SimpleNamespace(name=name) for name in names)
    request = MagicMock()
    request.app.state.resources.providers = providers
    return request


def test_model_catalog_reports_configured_availability_and_no_secrets() -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        openai_enabled=True,
        openai_api_key="secret-not-for-the-catalog",
        openai_context_window_tokens=128_000,
    )

    models = build_model_catalog(settings, available_provider_names=frozenset({"openai", "mock"}))

    openai = next(model for model in models if model.provider == "openai")
    anthropic = next(model for model in models if model.provider == "anthropic")
    assert openai.enabled is True
    assert openai.context_window_tokens == 128_000
    assert anthropic.enabled is False
    assert "secret-not-for-the-catalog" not in repr(models)


@pytest.mark.anyio
async def test_platform_operator_can_list_model_catalog() -> None:
    settings = Settings(_env_file=None, app_env="test")

    response = await list_admin_models(
        request_with_providers("mock"), operator_identity(), settings
    )

    assert response.credentials_exposed is False
    assert [item.provider for item in response.data] == [
        "openai",
        "anthropic",
        "local",
        "mock",
    ]
    mock = response.data[-1]
    assert mock.availability == "available"
    assert mock.auto_selectable is False
    assert mock.capabilities == ["text_generation"]


@pytest.mark.anyio
async def test_organization_owner_without_platform_role_is_denied() -> None:
    identity = operator_identity()
    identity.principal = Principal(
        actor_type="user",
        actor_id=uuid4(),
        organization_id=uuid4(),
        organization_role="owner",
    )

    with pytest.raises(GatewayError) as error:
        await list_admin_models(
            request_with_providers("mock"),
            identity,
            Settings(_env_file=None, app_env="test"),
        )

    assert error.value.status_code == 403
    assert error.value.code == "platform_permission_denied"


def test_admin_model_catalog_is_in_openapi_contract(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert "/v1/portal/admin/models" in response.json()["paths"]
