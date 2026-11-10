from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.api.routes.portal_admin import (
    change_admin_routing_policy,
    change_admin_routing_rollout,
    emergency_admin_routing_baseline,
    get_admin_routing_operational_summary,
    get_admin_routing_rollout,
    list_admin_routing_policies,
)
from app.core.config import Settings
from app.db.models import (
    RoutingPolicyVersion,
    RoutingRolloutChange,
    RoutingRolloutConfiguration,
)
from app.identity.authorization import Principal
from app.platform.routing_operations import (
    ClassifierDistribution,
    RoutingConcentration,
    RoutingOperationalSummary,
)
from app.routing.control_plane import RoutingControlChangeResult
from app.routing.policy_activation import PolicyActivationResult
from app.routing.rollout import IntelligentRoutingRollout, RoutingRuntimeConfiguration
from app.schemas.portal_admin import (
    ChangeRoutingPolicyRequest,
    ChangeRoutingRolloutRequest,
    EmergencyBaselineRequest,
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


def runtime(*, mode: str = "shadow") -> RoutingRuntimeConfiguration:
    return RoutingRuntimeConfiguration(
        mode=mode,  # type: ignore[arg-type]
        rollout=IntelligentRoutingRollout(percentage=0),
        required_policy_version="policy-v2" if mode != "baseline" else None,
        required_score_version="scores-v2" if mode != "baseline" else None,
        source="database",
        version=3,
    )


def stored_control(*, mode: str = "shadow") -> RoutingRolloutConfiguration:
    return RoutingRolloutConfiguration(
        id=uuid4(),
        name="default",
        mode=mode,
        canary_percent=0,
        customer_allowlist=[],
        required_policy_version="policy-v2" if mode != "baseline" else None,
        required_score_version="scores-v2" if mode != "baseline" else None,
        version=3,
        updated_by_actor="operator",
    )


@pytest.mark.anyio
async def test_operator_lists_and_activates_routing_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = RoutingPolicyVersion(
        id=uuid4(),
        version="policy-v2",
        status="draft",
        classifier_version="classifier-v2",
        score_version="scores-v2",
        strategy_weights={},
        minimum_quality={},
        minimum_sample_count=5,
        cold_start_mode="exclude",
        created_at=NOW,
        updated_at=NOW,
    )
    query_result = MagicMock()
    query_result.scalars.return_value.all.return_value = [policy]
    database = MagicMock()
    database.execute = AsyncMock(return_value=query_result)
    database.commit = AsyncMock()
    database.rollback = AsyncMock()
    activation = AsyncMock(
        return_value=PolicyActivationResult(
            policy_id=policy.id,
            policy_version=policy.version,
            previous_policy_version="policy-v1",
            action="activate",
            activated_at=NOW,
        )
    )
    audit = AsyncMock()
    monkeypatch.setattr("app.api.routes.portal_admin.change_active_policy", activation)
    monkeypatch.setattr("app.api.routes.portal_admin.append_audit_event", audit)
    identity = operator_identity()

    listing = await list_admin_routing_policies(identity, database)
    changed = await change_admin_routing_policy(
        ChangeRoutingPolicyRequest(
            version="policy-v2",
            action="activate",
            reason="Evaluation gate passed",
            confirmation="CHANGE_ROUTING_POLICY",
        ),
        identity,
        database,
    )

    assert listing.data[0].score_version == "scores-v2"
    assert changed.previous_policy_version == "policy-v1"
    audit.assert_awaited_once()
    database.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_operator_reads_and_changes_persistent_rollout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = runtime()
    after_model = stored_control(mode="live")
    after_model.canary_percent = 5
    change = RoutingRolloutChange(
        id=uuid4(),
        configuration_id=after_model.id,
        action="update",
        actor="operator",
        reason="Start canary",
        previous_configuration={},
        new_configuration={},
    )
    resolver = MagicMock()
    resolver.load = AsyncMock(return_value=before)
    update = AsyncMock(
        return_value=RoutingControlChangeResult(after_model, change)
    )
    audit = AsyncMock()
    monkeypatch.setattr(
        "app.api.routes.portal_admin.DatabaseRoutingControlResolver",
        lambda *_args: resolver,
    )
    monkeypatch.setattr("app.api.routes.portal_admin.change_routing_control", update)
    monkeypatch.setattr("app.api.routes.portal_admin.append_audit_event", audit)
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()
    identity = operator_identity()
    settings = Settings(_env_file=None)

    current = await get_admin_routing_rollout(identity, database, settings)
    changed = await change_admin_routing_rollout(
        ChangeRoutingRolloutRequest(
            mode="live",
            canary_percent=5,
            customer_allowlist=[],
            required_policy_version="policy-v2",
            required_score_version="scores-v2",
            reason="Start reviewed canary",
            confirmation="CHANGE_ROUTING_ROLLOUT",
        ),
        identity,
        database,
        settings,
    )

    assert current.mode == "shadow"
    assert changed.mode == "live"
    assert changed.canary_percent == 5
    update.assert_awaited_once()
    audit.assert_awaited_once()


@pytest.mark.anyio
async def test_emergency_baseline_is_audited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = stored_control(mode="baseline")
    change = RoutingRolloutChange(
        id=uuid4(),
        configuration_id=model.id,
        action="emergency_baseline",
        actor="operator",
        reason="Provider incident",
        previous_configuration={},
        new_configuration={},
    )
    emergency = AsyncMock(return_value=RoutingControlChangeResult(model, change))
    audit = AsyncMock()
    monkeypatch.setattr("app.api.routes.portal_admin.emergency_baseline", emergency)
    monkeypatch.setattr("app.api.routes.portal_admin.append_audit_event", audit)
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()

    response = await emergency_admin_routing_baseline(
        EmergencyBaselineRequest(
            reason="Provider incident", confirmation="EMERGENCY_BASELINE"
        ),
        operator_identity(),
        database,
    )

    assert response.mode == "baseline"
    emergency.assert_awaited_once()
    audit.assert_awaited_once()
    database.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_operational_summary_exposes_aggregates_and_grafana_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = MagicMock()
    resolver.load = AsyncMock(return_value=runtime())
    summary = RoutingOperationalSummary(
        window_hours=24,
        request_count=10,
        concentration=(RoutingConcentration("openai", "model-a", 8, 0.8),),
        classifier_distribution=(
            ClassifierDistribution("coding", "medium", 6, 0.6),
        ),
        current_cost_usd=Decimal("1.2"),
        previous_cost_usd=Decimal("1"),
        cost_drift_percent=20,
        active_policy_version="policy-v2",
        active_score_version="scores-v2",
        evidence_valid_until=NOW + timedelta(days=5),
        evidence_expired_count=0,
        alert_state="healthy",
        alert_reasons=(),
    )
    monkeypatch.setattr(
        "app.api.routes.portal_admin.DatabaseRoutingControlResolver",
        lambda *_args: resolver,
    )
    build = AsyncMock(return_value=summary)
    monkeypatch.setattr(
        "app.api.routes.portal_admin.build_routing_operational_summary", build
    )

    response = await get_admin_routing_operational_summary(
        operator_identity(), MagicMock(), Settings(_env_file=None), 24
    )

    assert response.concentration[0].share == 0.8
    assert response.alert_state == "healthy"
    assert response.grafana_url.startswith("http://localhost:3000/")
