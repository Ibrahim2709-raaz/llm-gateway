from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ModelCapabilityScore,
    RoutingDecisionSnapshot,
    RoutingPolicyVersion,
    UsageRecord,
)
from app.routing.rollout import RoutingRuntimeConfiguration


@dataclass(frozen=True, slots=True)
class RoutingConcentration:
    provider: str
    model: str
    request_count: int
    share: float


@dataclass(frozen=True, slots=True)
class ClassifierDistribution:
    task_category: str
    difficulty: str
    request_count: int
    share: float


@dataclass(frozen=True, slots=True)
class RoutingOperationalSummary:
    window_hours: int
    request_count: int
    concentration: tuple[RoutingConcentration, ...]
    classifier_distribution: tuple[ClassifierDistribution, ...]
    current_cost_usd: Decimal
    previous_cost_usd: Decimal
    cost_drift_percent: float | None
    active_policy_version: str | None
    active_score_version: str | None
    evidence_valid_until: datetime | None
    evidence_expired_count: int
    alert_state: str
    alert_reasons: tuple[str, ...]


async def build_routing_operational_summary(
    session: AsyncSession,
    *,
    runtime: RoutingRuntimeConfiguration,
    window_hours: int = 24,
    now: datetime | None = None,
) -> RoutingOperationalSummary:
    observed_at = now or datetime.now(UTC)
    cutoff = observed_at - timedelta(hours=window_hours)
    previous_cutoff = cutoff - timedelta(hours=window_hours)

    concentration_rows = (
        await session.execute(
            select(
                RoutingDecisionSnapshot.selected_provider,
                RoutingDecisionSnapshot.selected_model,
                func.count(RoutingDecisionSnapshot.id),
            )
            .where(RoutingDecisionSnapshot.created_at >= cutoff)
            .group_by(
                RoutingDecisionSnapshot.selected_provider,
                RoutingDecisionSnapshot.selected_model,
            )
            .order_by(func.count(RoutingDecisionSnapshot.id).desc())
        )
    ).all()
    request_count = sum(int(row[2]) for row in concentration_rows)
    concentration = tuple(
        RoutingConcentration(
            provider=str(row[0]),
            model=str(row[1]),
            request_count=int(row[2]),
            share=(int(row[2]) / request_count if request_count else 0),
        )
        for row in concentration_rows
    )

    classifier_rows = (
        await session.execute(
            select(
                RoutingDecisionSnapshot.task_category,
                RoutingDecisionSnapshot.difficulty,
                func.count(RoutingDecisionSnapshot.id),
            )
            .where(RoutingDecisionSnapshot.created_at >= cutoff)
            .group_by(
                RoutingDecisionSnapshot.task_category,
                RoutingDecisionSnapshot.difficulty,
            )
            .order_by(func.count(RoutingDecisionSnapshot.id).desc())
        )
    ).all()
    classifier_distribution = tuple(
        ClassifierDistribution(
            task_category=str(row[0]),
            difficulty=str(row[1]),
            request_count=int(row[2]),
            share=(int(row[2]) / request_count if request_count else 0),
        )
        for row in classifier_rows
    )

    cost_rows = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(UsageRecord.cost_usd).filter(
                        UsageRecord.created_at >= cutoff
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(UsageRecord.cost_usd).filter(
                        UsageRecord.created_at >= previous_cutoff,
                        UsageRecord.created_at < cutoff,
                    ),
                    0,
                ),
            )
        )
    ).one()
    current_cost = Decimal(str(cost_rows[0]))
    previous_cost = Decimal(str(cost_rows[1]))
    cost_drift = (
        float((current_cost - previous_cost) / previous_cost * 100)
        if previous_cost > 0
        else None
    )

    active_policy = await session.scalar(
        select(RoutingPolicyVersion).where(RoutingPolicyVersion.status == "active")
    )
    evidence_rows = (
        await session.execute(
            select(ModelCapabilityScore.valid_until).where(
                ModelCapabilityScore.status == "active"
            )
        )
    ).scalars().all()
    expiry_values = [value for value in evidence_rows if value is not None]
    valid_until = min(expiry_values) if expiry_values else None
    expired_count = sum(value <= observed_at for value in expiry_values)
    reasons: list[str] = []
    if runtime.mode != "baseline":
        if active_policy is None:
            reasons.append("active_policy_missing")
        if not expiry_values:
            reasons.append("active_evidence_missing")
        elif expired_count:
            reasons.append("active_evidence_expired")
        elif valid_until is not None and valid_until <= observed_at + timedelta(hours=72):
            reasons.append("active_evidence_expires_soon")
        if (
            runtime.required_policy_version is None
            or runtime.required_score_version is None
        ):
            reasons.append("evidence_pins_missing")
    alert_state = (
        "critical"
        if any(reason.endswith(("missing", "expired")) for reason in reasons)
        else "warning" if reasons else "healthy"
    )
    return RoutingOperationalSummary(
        window_hours=window_hours,
        request_count=request_count,
        concentration=concentration,
        classifier_distribution=classifier_distribution,
        current_cost_usd=current_cost,
        previous_cost_usd=previous_cost,
        cost_drift_percent=cost_drift,
        active_policy_version=(active_policy.version if active_policy is not None else None),
        active_score_version=(active_policy.score_version if active_policy is not None else None),
        evidence_valid_until=valid_until,
        evidence_expired_count=expired_count,
        alert_state=alert_state,
        alert_reasons=tuple(reasons),
    )
