from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.usage import get_usage_summary
from app.auth.dependencies import AuthenticatedClient
from app.cli.set_budget import build_parser
from app.core.errors import GatewayError
from app.db.models import BudgetLimit, UsageRecord
from app.db.session import get_database_session
from app.main import app
from app.usage.service import (
    DatabaseUsageService,
    UsageSummary,
    current_period,
    normalize_actual_cost,
    normalize_estimated_cost,
    period_bounds,
    set_budget_limit,
)


def query_result(value: object) -> Mock:
    result = Mock()
    result.scalar_one_or_none.return_value = value
    return result


@pytest.mark.anyio
async def test_reserve_creates_durable_estimated_usage() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = query_result(None)
    service = DatabaseUsageService(session)
    request_id = uuid4()
    client_id = uuid4()

    await service.reserve(request_id, client_id, Decimal("0.123456789"))

    record = session.add.call_args.args[0]
    assert isinstance(record, UsageRecord)
    assert record.request_id == request_id
    assert record.client_id == client_id
    assert record.cost_usd == Decimal("0.12345679")
    assert record.status == "reserved"
    session.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_reserve_checks_locked_budget_before_provider_call() -> None:
    session = AsyncMock(spec=AsyncSession)
    budget = BudgetLimit(
        client_id=uuid4(),
        period="2026-10",
        limit_usd=Decimal("1.00"),
        alert_threshold_percent=80,
    )
    session.execute.return_value = query_result(budget)
    session.scalar.return_value = Decimal("0.70")
    service = DatabaseUsageService(session)

    with pytest.raises(GatewayError) as error:
        await service.reserve(uuid4(), budget.client_id, Decimal("0.31"))

    assert error.value.status_code == 402
    assert error.value.code == "budget_exceeded"
    session.rollback.assert_awaited_once()
    session.add.assert_not_called()


@pytest.mark.anyio
async def test_settle_replaces_reservation_with_actual_usage() -> None:
    session = AsyncMock(spec=AsyncSession)
    record = UsageRecord(
        request_id=uuid4(),
        client_id=uuid4(),
        provider_config_id=None,
        provider=None,
        model=None,
        input_tokens=0,
        output_tokens=0,
        cost_usd=Decimal("1"),
        latency_ms=0,
        status="reserved",
        created_at=datetime.now(UTC),
    )
    session.execute.return_value = query_result(record)
    provider_config_id = uuid4()
    session.scalar.return_value = provider_config_id
    service = DatabaseUsageService(session)

    await service.settle(
        record.request_id,
        record.client_id,
        provider="openai",
        model="test-model",
        input_tokens=100,
        output_tokens=25,
        actual_cost_usd=Decimal("0.123456785"),
        latency_ms=321,
    )

    assert record.provider_config_id == provider_config_id
    assert record.provider == "openai"
    assert record.model == "test-model"
    assert record.input_tokens == 100
    assert record.output_tokens == 25
    assert record.cost_usd == Decimal("0.12345679")
    assert record.latency_ms == 321
    assert record.status == "success"
    session.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_failed_reservation_releases_reserved_cost() -> None:
    session = AsyncMock(spec=AsyncSession)
    record = UsageRecord(
        request_id=uuid4(),
        client_id=uuid4(),
        provider_config_id=None,
        provider=None,
        model=None,
        input_tokens=0,
        output_tokens=0,
        cost_usd=Decimal("1"),
        latency_ms=0,
        status="reserved",
        created_at=datetime.now(UTC),
    )
    session.execute.return_value = query_result(record)

    await DatabaseUsageService(session).fail(record.request_id, record.client_id, latency_ms=99)

    assert record.status == "failed"
    assert record.cost_usd == 0
    assert record.latency_ms == 99


@pytest.mark.anyio
async def test_cached_request_is_recorded_without_tokens_or_cost() -> None:
    session = AsyncMock(spec=AsyncSession)

    await DatabaseUsageService(session).record_cached(uuid4(), uuid4())

    record = session.add.call_args.args[0]
    assert isinstance(record, UsageRecord)
    assert record.status == "cached"
    assert record.input_tokens == 0
    assert record.output_tokens == 0
    assert record.cost_usd == 0
    session.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_usage_summary_reports_actual_usage_and_reserved_remaining_budget() -> None:
    session = AsyncMock(spec=AsyncSession)
    aggregate_result = Mock()
    aggregate_result.one.return_value = (3, 100, 25, Decimal("0.50"))
    session.execute.return_value = aggregate_result
    session.scalar.side_effect = [Decimal("2.00"), Decimal("0.75")]

    summary = await DatabaseUsageService(session).summary(uuid4(), "2026-10")

    assert summary.request_count == 3
    assert summary.total_tokens == 125
    assert summary.cost_usd == Decimal("0.50")
    assert summary.remaining_budget_usd == Decimal("1.25")


@pytest.mark.anyio
async def test_usage_endpoint_maps_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    async def summary(*_: object) -> UsageSummary:
        return UsageSummary(
            period="2026-10",
            request_count=2,
            prompt_tokens=10,
            completion_tokens=5,
            cost_usd=Decimal("0.25"),
            budget_usd=Decimal("1.00"),
            budget_committed_usd=Decimal("0.30"),
        )

    monkeypatch.setattr(DatabaseUsageService, "summary", summary)
    identity = AuthenticatedClient(client_id=uuid4(), api_key_id=uuid4(), name="test")

    response = await get_usage_summary("2026-10", identity, AsyncMock(spec=AsyncSession))

    assert response.model_dump() == {
        "period": "2026-10",
        "request_count": 2,
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "cost_usd": 0.25,
        "budget_usd": 1.0,
        "remaining_budget_usd": 0.7,
    }


def test_usage_http_endpoint_is_authenticated_and_validated(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def summary(*_: object) -> UsageSummary:
        return UsageSummary(
            period="2026-10",
            request_count=1,
            prompt_tokens=10,
            completion_tokens=5,
            cost_usd=Decimal("0.25"),
            budget_usd=None,
            budget_committed_usd=Decimal("0.25"),
        )

    async def database_session() -> AsyncIterator[AsyncSession]:
        yield AsyncMock(spec=AsyncSession)

    monkeypatch.setattr(DatabaseUsageService, "summary", summary)
    app.dependency_overrides[get_database_session] = database_session
    try:
        response = client.get("/v1/usage", params={"period": "2026-10"})
        invalid = client.get("/v1/usage", params={"period": "0000-10"})
    finally:
        app.dependency_overrides.pop(get_database_session, None)

    assert response.status_code == 200
    assert response.json()["budget_usd"] is None
    assert response.json()["remaining_budget_usd"] is None
    assert invalid.status_code == 422


@pytest.mark.anyio
async def test_set_budget_limit_creates_and_updates_budget() -> None:
    client_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    session.scalar.return_value = client_id
    session.execute.return_value = query_result(None)

    created = await set_budget_limit(
        session,
        client_id=client_id,
        period="2026-10",
        limit_usd=Decimal("25"),
        alert_threshold_percent=80,
    )

    assert created.limit_usd == Decimal("25.00000000")
    session.add.assert_called_once_with(created)

    existing = BudgetLimit(
        client_id=client_id,
        period="2026-10",
        limit_usd=Decimal("25"),
        alert_threshold_percent=80,
    )
    session.execute.return_value = query_result(existing)
    updated = await set_budget_limit(
        session,
        client_id=client_id,
        period="2026-10",
        limit_usd=Decimal("30"),
        alert_threshold_percent=90,
    )

    assert updated is existing
    assert existing.limit_usd == Decimal("30.00000000")
    assert existing.alert_threshold_percent == 90


@pytest.mark.anyio
async def test_set_budget_limit_rejects_unknown_client() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.scalar.return_value = None

    with pytest.raises(ValueError, match="Client was not found"):
        await set_budget_limit(
            session,
            client_id=uuid4(),
            period="2026-10",
            limit_usd=Decimal("25"),
            alert_threshold_percent=80,
        )


@pytest.mark.anyio
async def test_set_budget_limit_validates_policy_values() -> None:
    session = AsyncMock(spec=AsyncSession)

    with pytest.raises(ValueError, match="positive amount"):
        await set_budget_limit(
            session,
            client_id=uuid4(),
            period="2026-10",
            limit_usd=Decimal("0"),
            alert_threshold_percent=80,
        )

    with pytest.raises(ValueError, match="between 1 and 100"):
        await set_budget_limit(
            session,
            client_id=uuid4(),
            period="2026-10",
            limit_usd=Decimal("25"),
            alert_threshold_percent=101,
        )


def test_period_and_cost_helpers() -> None:
    start, end = period_bounds("2026-12")

    assert start == datetime(2026, 12, 1, tzinfo=UTC)
    assert end == datetime(2027, 1, 1, tzinfo=UTC)
    assert current_period(datetime(2026, 10, 21, tzinfo=UTC)) == "2026-10"
    assert normalize_estimated_cost(Decimal("0.000000001")) == Decimal("0.00000001")
    assert normalize_actual_cost(Decimal("0.000000001")) == Decimal("0E-8")


def test_budget_cli_parses_valid_values() -> None:
    client_id = uuid4()
    arguments = build_parser().parse_args(
        [
            "--client-id",
            str(client_id),
            "--period",
            "2026-10",
            "--limit-usd",
            "25.50",
        ]
    )

    assert arguments.client_id == client_id
    assert arguments.period == "2026-10"
    assert arguments.limit_usd == Decimal("25.50")
