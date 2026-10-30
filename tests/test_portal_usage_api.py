from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.routes.portal_usage import get_portal_usage_summary, update_portal_budget
from app.db.models import BrowserSession, Organization, OrganizationMembership, User
from app.identity.authorization import Principal
from app.identity.dependencies import BrowserIdentity
from app.identity.sessions import AuthenticatedBrowserSession
from app.main import create_app
from app.schemas.portal_usage import UpdateBudgetRequest
from app.usage.service import UsageSummary

NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)


def browser_identity(role: str = "owner") -> BrowserIdentity:
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
        role=role,
        status="active",
    )
    stored = BrowserSession(
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
        authentication=AuthenticatedBrowserSession(stored, user),
        organization=organization,
        membership=membership,
        principal=Principal(
            actor_type="user",
            actor_id=user.id,
            organization_id=organization.id,
            organization_role=role,
        ),
    )


def test_portal_usage_routes_are_in_openapi_contract() -> None:
    with TestClient(create_app()) as client:
        paths = client.get("/openapi.json").json()["paths"]
    assert "/v1/portal/organizations/{organization_id}/usage" in paths
    assert "/v1/portal/organizations/{organization_id}/budgets/{period}" in paths


@pytest.mark.anyio
async def test_billing_viewer_can_read_organization_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = browser_identity(role="billing_viewer")
    summary = UsageSummary(
        period="2026-10",
        request_count=4,
        prompt_tokens=100,
        completion_tokens=25,
        cost_usd=Decimal("0.75"),
        budget_usd=Decimal("10"),
        budget_committed_usd=Decimal("1"),
    )
    operation = AsyncMock(return_value=summary)
    monkeypatch.setattr("app.api.routes.portal_usage.DatabaseUsageService.summary", operation)
    response = await get_portal_usage_summary(
        identity.organization.id,
        "2026-10",
        identity,
        MagicMock(),
    )
    assert response.cost_usd == 0.75
    assert response.remaining_budget_usd == 9
    assert response.total_tokens == 125


@pytest.mark.anyio
async def test_budget_update_is_audited_and_committed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = browser_identity()
    budget = MagicMock(
        id=uuid4(),
        period="2026-10",
        limit_usd=Decimal("25.00000000"),
        alert_threshold_percent=80,
    )
    monkeypatch.setattr(
        "app.api.routes.portal_usage.set_budget_limit",
        AsyncMock(return_value=budget),
    )
    audit = AsyncMock()
    monkeypatch.setattr("app.api.routes.portal_usage.append_audit_event", audit)
    database = MagicMock()
    database.flush = AsyncMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()
    response = await update_portal_budget(
        identity.organization.id,
        "2026-10",
        UpdateBudgetRequest(limit_usd=Decimal("25"), alert_threshold_percent=80),
        identity,
        database,
    )
    assert response.limit_usd == Decimal("25.00000000")
    audit.assert_awaited_once()
    database.commit.assert_awaited_once()
