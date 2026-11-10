from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.db.models import RoutingPolicyVersion
from app.platform.routing_operations import build_routing_operational_summary
from app.routing.rollout import IntelligentRoutingRollout, RoutingRuntimeConfiguration

NOW = datetime(2026, 11, 9, 12, tzinfo=UTC)


def result(*, rows: list[tuple[object, ...]] | None = None) -> MagicMock:
    value = MagicMock()
    value.all.return_value = rows or []
    return value


@pytest.mark.anyio
async def test_summary_calculates_concentration_cost_drift_and_freshness() -> None:
    concentration = result(rows=[("openai", "model-a", 8), ("local", "model-b", 2)])
    classifier = result(rows=[("coding", "medium", 6), ("general_chat", "simple", 4)])
    costs = MagicMock()
    costs.one.return_value = (Decimal("12"), Decimal("10"))
    evidence = MagicMock()
    evidence.scalars.return_value.all.return_value = [NOW + timedelta(days=10)]
    policy = RoutingPolicyVersion(
        id=uuid4(),
        version="policy-v2",
        status="active",
        classifier_version="classifier-v2",
        score_version="scores-v2",
        strategy_weights={},
        minimum_quality={},
        minimum_sample_count=5,
        cold_start_mode="exclude",
    )
    session = MagicMock()
    session.execute = AsyncMock(
        side_effect=[concentration, classifier, costs, evidence]
    )
    session.scalar = AsyncMock(return_value=policy)
    runtime = RoutingRuntimeConfiguration(
        mode="live",
        rollout=IntelligentRoutingRollout(percentage=5),
        required_policy_version="policy-v2",
        required_score_version="scores-v2",
        source="database",
        version=3,
    )

    summary = await build_routing_operational_summary(
        session, runtime=runtime, now=NOW
    )

    assert summary.request_count == 10
    assert summary.concentration[0].share == 0.8
    assert summary.classifier_distribution[0].share == 0.6
    assert summary.current_cost_usd == Decimal("12")
    assert summary.cost_drift_percent == 20
    assert summary.evidence_valid_until == NOW + timedelta(days=10)
    assert summary.alert_state == "healthy"


@pytest.mark.anyio
async def test_summary_marks_live_routing_critical_without_evidence() -> None:
    empty = result()
    costs = MagicMock()
    costs.one.return_value = (0, 0)
    evidence = MagicMock()
    evidence.scalars.return_value.all.return_value = []
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[empty, empty, costs, evidence])
    session.scalar = AsyncMock(return_value=None)
    runtime = RoutingRuntimeConfiguration(
        mode="live",
        rollout=IntelligentRoutingRollout(percentage=1),
        required_policy_version=None,
        required_score_version=None,
    )

    summary = await build_routing_operational_summary(
        session, runtime=runtime, now=NOW
    )

    assert summary.alert_state == "critical"
    assert set(summary.alert_reasons) == {
        "active_policy_missing",
        "active_evidence_missing",
        "evidence_pins_missing",
    }
