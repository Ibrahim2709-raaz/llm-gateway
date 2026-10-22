from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Protocol
from uuid import UUID

from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import GatewayError
from app.db.models import BudgetLimit, Client, ProviderConfig, UsageRecord

COST_PRECISION = Decimal("0.00000001")
COUNTED_USAGE_STATUSES = ("success", "cached")
BUDGETED_USAGE_STATUSES = ("reserved", "success")


def normalize_estimated_cost(value: Decimal) -> Decimal:
    return value.quantize(COST_PRECISION, rounding=ROUND_CEILING)


def normalize_actual_cost(value: Decimal) -> Decimal:
    return value.quantize(COST_PRECISION, rounding=ROUND_HALF_UP)


def period_bounds(period: str) -> tuple[datetime, datetime]:
    year, month = (int(part) for part in period.split("-"))
    start = datetime(year, month, 1, tzinfo=UTC)
    end = datetime(year + (month == 12), 1 if month == 12 else month + 1, 1, tzinfo=UTC)
    return start, end


def current_period(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y-%m")


@dataclass(frozen=True, slots=True)
class UsageSummary:
    period: str
    request_count: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: Decimal
    budget_usd: Decimal | None
    budget_committed_usd: Decimal

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def remaining_budget_usd(self) -> Decimal | None:
        if self.budget_usd is None:
            return None
        return max(Decimal("0"), self.budget_usd - self.budget_committed_usd)


class UsageTracker(Protocol):
    async def reserve(
        self,
        request_id: UUID,
        client_id: UUID,
        estimated_cost_usd: Decimal,
    ) -> None: ...

    async def settle(
        self,
        request_id: UUID,
        client_id: UUID,
        *,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        actual_cost_usd: Decimal,
        latency_ms: int,
    ) -> None: ...

    async def fail(self, request_id: UUID, client_id: UUID, *, latency_ms: int) -> None: ...

    async def record_cached(self, request_id: UUID, client_id: UUID) -> None: ...


class DatabaseUsageService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def reserve(
        self,
        request_id: UUID,
        client_id: UUID,
        estimated_cost_usd: Decimal,
    ) -> None:
        if not estimated_cost_usd.is_finite() or estimated_cost_usd < 0:
            raise ValueError("Estimated cost must be a finite non-negative amount")
        now = datetime.now(UTC)
        period = current_period(now)
        period_start, period_end = period_bounds(period)
        budget_statement = (
            select(BudgetLimit)
            .where(BudgetLimit.client_id == client_id, BudgetLimit.period == period)
            .with_for_update()
        )
        budget = (await self.session.execute(budget_statement)).scalar_one_or_none()
        reserved_cost = normalize_estimated_cost(estimated_cost_usd)
        if budget is not None:
            used_statement = select(func.coalesce(func.sum(UsageRecord.cost_usd), 0)).where(
                UsageRecord.client_id == client_id,
                UsageRecord.created_at >= period_start,
                UsageRecord.created_at < period_end,
                UsageRecord.status.in_(BUDGETED_USAGE_STATUSES),
            )
            used = Decimal(await self.session.scalar(used_statement) or 0)
            if used + reserved_cost > budget.limit_usd:
                await self.session.rollback()
                raise GatewayError(
                    "The estimated request cost exceeds the remaining monthly budget.",
                    code="budget_exceeded",
                    error_type="budget_error",
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    param="routing.max_cost_usd",
                )

        self.session.add(
            UsageRecord(
                request_id=request_id,
                client_id=client_id,
                provider_config_id=None,
                provider=None,
                model=None,
                input_tokens=0,
                output_tokens=0,
                cost_usd=reserved_cost,
                latency_ms=0,
                status="reserved",
                created_at=now,
            )
        )
        await self.session.commit()

    async def settle(
        self,
        request_id: UUID,
        client_id: UUID,
        *,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        actual_cost_usd: Decimal,
        latency_ms: int,
    ) -> None:
        record = await self._locked_record(request_id, client_id)
        provider_config_id = await self.session.scalar(
            select(ProviderConfig.id).where(
                ProviderConfig.provider == provider,
                ProviderConfig.model == model,
            )
        )
        record.provider_config_id = provider_config_id
        record.provider = provider
        record.model = model
        record.input_tokens = input_tokens
        record.output_tokens = output_tokens
        record.cost_usd = normalize_actual_cost(actual_cost_usd)
        record.latency_ms = latency_ms
        record.status = "success"
        await self.session.commit()

    async def fail(self, request_id: UUID, client_id: UUID, *, latency_ms: int) -> None:
        record = await self._locked_record(request_id, client_id)
        record.cost_usd = Decimal("0")
        record.latency_ms = latency_ms
        record.status = "failed"
        await self.session.commit()

    async def record_cached(self, request_id: UUID, client_id: UUID) -> None:
        self.session.add(
            UsageRecord(
                request_id=request_id,
                client_id=client_id,
                provider_config_id=None,
                provider=None,
                model=None,
                input_tokens=0,
                output_tokens=0,
                cost_usd=Decimal("0"),
                latency_ms=0,
                status="cached",
                created_at=datetime.now(UTC),
            )
        )
        await self.session.commit()

    async def summary(self, client_id: UUID, period: str) -> UsageSummary:
        start, end = period_bounds(period)
        usage_statement = select(
            func.count(UsageRecord.id),
            func.coalesce(func.sum(UsageRecord.input_tokens), 0),
            func.coalesce(func.sum(UsageRecord.output_tokens), 0),
            func.coalesce(func.sum(UsageRecord.cost_usd), 0),
        ).where(
            UsageRecord.client_id == client_id,
            UsageRecord.created_at >= start,
            UsageRecord.created_at < end,
            UsageRecord.status.in_(COUNTED_USAGE_STATUSES),
        )
        row = (await self.session.execute(usage_statement)).one()
        budget = await self.session.scalar(
            select(BudgetLimit.limit_usd).where(
                BudgetLimit.client_id == client_id,
                BudgetLimit.period == period,
            )
        )
        budget_committed = await self.session.scalar(
            select(func.coalesce(func.sum(UsageRecord.cost_usd), 0)).where(
                UsageRecord.client_id == client_id,
                UsageRecord.created_at >= start,
                UsageRecord.created_at < end,
                UsageRecord.status.in_(BUDGETED_USAGE_STATUSES),
            )
        )
        return UsageSummary(
            period=period,
            request_count=int(row[0]),
            prompt_tokens=int(row[1]),
            completion_tokens=int(row[2]),
            cost_usd=Decimal(row[3]),
            budget_usd=Decimal(budget) if budget is not None else None,
            budget_committed_usd=Decimal(budget_committed or 0),
        )

    async def _locked_record(self, request_id: UUID, client_id: UUID) -> UsageRecord:
        statement = (
            select(UsageRecord)
            .where(
                UsageRecord.request_id == request_id,
                UsageRecord.client_id == client_id,
            )
            .with_for_update()
        )
        record = (await self.session.execute(statement)).scalar_one_or_none()
        if record is None:
            await self.session.rollback()
            raise RuntimeError("Usage reservation was not found")
        return record


async def set_budget_limit(
    session: AsyncSession,
    *,
    client_id: UUID,
    period: str,
    limit_usd: Decimal,
    alert_threshold_percent: int,
) -> BudgetLimit:
    period_bounds(period)
    if not limit_usd.is_finite() or limit_usd <= 0:
        raise ValueError("Budget limit must be a finite positive amount")
    if not 1 <= alert_threshold_percent <= 100:
        raise ValueError("Alert threshold must be between 1 and 100")
    client_exists = await session.scalar(select(Client.id).where(Client.id == client_id))
    if client_exists is None:
        raise ValueError("Client was not found")
    statement = (
        select(BudgetLimit)
        .where(BudgetLimit.client_id == client_id, BudgetLimit.period == period)
        .with_for_update()
    )
    budget = (await session.execute(statement)).scalar_one_or_none()
    normalized_limit = normalize_actual_cost(limit_usd)
    if budget is None:
        budget = BudgetLimit(
            client_id=client_id,
            period=period,
            limit_usd=normalized_limit,
            alert_threshold_percent=alert_threshold_percent,
        )
        session.add(budget)
    else:
        budget.limit_usd = normalized_limit
        budget.alert_threshold_percent = alert_threshold_percent
    return budget
