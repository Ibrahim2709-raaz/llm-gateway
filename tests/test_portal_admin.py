from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.routes.portal_admin import (
    create_admin_pricing_proposal,
    list_admin_models,
    list_admin_pricing_proposals,
    review_admin_pricing_proposal,
)
from app.core.config import Settings
from app.core.errors import GatewayError
from app.db.models import ProviderPricingProposal
from app.identity.authorization import Principal
from app.platform.model_catalog import build_model_catalog
from app.platform.pricing import PricingProposalError
from app.schemas.portal_admin import (
    CreatePricingProposalRequest,
    ReviewPricingProposalRequest,
)

NOW = datetime(2026, 11, 9, 12, tzinfo=UTC)


def operator_identity() -> MagicMock:
    user_id = uuid4()
    identity = MagicMock()
    identity.authentication.user.id = user_id
    identity.principal = Principal(
        actor_type="user",
        actor_id=user_id,
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


def pricing_proposal(*, proposer_id: UUID | None = None) -> ProviderPricingProposal:
    return ProviderPricingProposal(
        id=uuid4(),
        provider="openai",
        model="model-a",
        input_cost_per_million=Decimal("1.25"),
        output_cost_per_million=Decimal("5.5"),
        source_url="https://provider.example/pricing",
        rationale="Official provider price changed.",
        status="pending",
        proposed_by_user_id=proposer_id or uuid4(),
        created_at=NOW,
        updated_at=NOW,
    )


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
    assert "/v1/portal/admin/pricing-proposals" in response.json()["paths"]


@pytest.mark.anyio
async def test_platform_operator_can_list_pricing_proposals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = pricing_proposal()
    listing = AsyncMock(return_value=[item])
    monkeypatch.setattr("app.api.routes.portal_admin.list_pricing_proposals", listing)

    response = await list_admin_pricing_proposals(operator_identity(), MagicMock(), 25)

    assert response.data[0].id == item.id
    assert response.data[0].status == "pending"
    listing.assert_awaited_once_with(ANY, limit=25)


@pytest.mark.anyio
async def test_create_pricing_proposal_is_audited_and_committed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = operator_identity()
    item = pricing_proposal(proposer_id=identity.authentication.user.id)
    create = AsyncMock(return_value=item)
    audit = AsyncMock()
    monkeypatch.setattr("app.api.routes.portal_admin.create_pricing_proposal", create)
    monkeypatch.setattr("app.api.routes.portal_admin.append_audit_event", audit)
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()
    payload = CreatePricingProposalRequest(
        provider="openai",
        model="model-a",
        input_cost_per_million_usd=Decimal("1.25"),
        output_cost_per_million_usd=Decimal("5.5"),
        source_url="https://provider.example/pricing",
        rationale="Official provider price changed.",
    )

    response = await create_admin_pricing_proposal(payload, identity, database)

    assert response.id == item.id
    create.assert_awaited_once()
    audit.assert_awaited_once()
    database.commit.assert_awaited_once()
    database.rollback.assert_not_awaited()


@pytest.mark.anyio
async def test_pricing_review_conflict_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    review = AsyncMock(
        side_effect=PricingProposalError(
            "independent_pricing_review_required", "A different operator is required"
        )
    )
    monkeypatch.setattr("app.api.routes.portal_admin.review_pricing_proposal", review)
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()

    with pytest.raises(GatewayError) as error:
        await review_admin_pricing_proposal(
            uuid4(),
            ReviewPricingProposalRequest(
                decision="approved", review_note="Checked source."
            ),
            operator_identity(),
            database,
        )

    assert error.value.status_code == 409
    database.rollback.assert_awaited_once()
