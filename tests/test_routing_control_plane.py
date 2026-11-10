from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from app.core.config import Settings
from app.db.models import RoutingRolloutChange, RoutingRolloutConfiguration
from app.routing.control_plane import (
    DatabaseRoutingControlResolver,
    RoutingControlError,
    change_routing_control,
    emergency_baseline,
)


def configuration(**overrides: object) -> RoutingRolloutConfiguration:
    values: dict[str, object] = {
        "id": uuid4(),
        "name": "default",
        "mode": "shadow",
        "canary_percent": 0,
        "customer_allowlist": [],
        "required_policy_version": "policy-v2",
        "required_score_version": "scores-v2",
        "version": 2,
        "updated_by_actor": "operator-a",
    }
    values.update(overrides)
    return RoutingRolloutConfiguration(**values)


@pytest.mark.anyio
async def test_resolver_prefers_persistent_configuration() -> None:
    stored = configuration(mode="live", canary_percent=10)
    session = MagicMock()
    session.scalar = AsyncMock(return_value=stored)

    runtime = await DatabaseRoutingControlResolver(
        session, Settings(_env_file=None)
    ).load()

    assert runtime.mode == "live"
    assert runtime.rollout.percentage == 10
    assert runtime.source == "database"
    assert runtime.version == 2


@pytest.mark.anyio
async def test_resolver_uses_environment_until_operator_saves_control() -> None:
    session = MagicMock()
    session.scalar = AsyncMock(return_value=None)
    settings = Settings(
        _env_file=None,
        intelligent_routing_shadow_enabled=True,
        intelligent_routing_required_policy_version="policy-v2",
        intelligent_routing_required_score_version="scores-v2",
    )

    runtime = await DatabaseRoutingControlResolver(session, settings).load()

    assert runtime.mode == "shadow"
    assert runtime.source == "environment"


@pytest.mark.anyio
async def test_change_is_versioned_and_records_before_and_after() -> None:
    client_id = uuid4()
    stored = configuration()
    session = MagicMock()
    session.scalar = AsyncMock(return_value=stored)
    session.flush = AsyncMock()
    session.add = MagicMock()

    result = await change_routing_control(
        session,
        mode="live",
        canary_percent=5,
        customer_allowlist=frozenset({client_id}),
        required_policy_version="policy-v2",
        required_score_version="scores-v2",
        actor="operator-b",
        reason="Start internal canary",
    )

    assert result.configuration.version == 3
    assert result.configuration.mode == "live"
    assert result.configuration.customer_allowlist == [str(client_id)]
    change = session.add.call_args.args[0]
    assert isinstance(change, RoutingRolloutChange)
    assert change.previous_configuration["mode"] == "shadow"
    assert change.new_configuration["mode"] == "live"
    assert change.actor == "operator-b"


@pytest.mark.anyio
async def test_emergency_baseline_removes_every_live_target_and_pin() -> None:
    stored = configuration(
        mode="live",
        canary_percent=25,
        customer_allowlist=[str(uuid4())],
    )
    session = MagicMock()
    session.scalar = AsyncMock(return_value=stored)
    session.flush = AsyncMock()
    session.add = MagicMock()

    result = await emergency_baseline(
        session, actor="incident-commander", reason="Provider incident"
    )

    assert result.configuration.mode == "baseline"
    assert result.configuration.canary_percent == 0
    assert result.configuration.customer_allowlist == []
    assert result.configuration.required_policy_version is None
    assert result.change.action == "emergency_baseline"


@pytest.mark.parametrize("mode", ["shadow", "live"])
@pytest.mark.anyio
async def test_evidence_modes_require_complete_pins(mode: str) -> None:
    session = MagicMock()

    with pytest.raises(RoutingControlError, match="require policy and score pins"):
        await change_routing_control(
            session,
            mode=mode,  # type: ignore[arg-type]
            canary_percent=0,
            customer_allowlist=frozenset(),
            required_policy_version=None,
            required_score_version=None,
            actor="operator",
            reason="Test invalid state",
        )


@pytest.mark.anyio
async def test_baseline_rejects_hidden_rollout_targets() -> None:
    session = MagicMock()

    with pytest.raises(RoutingControlError, match="cannot include rollout targets"):
        await change_routing_control(
            session,
            mode="baseline",
            canary_percent=1,
            customer_allowlist=frozenset({UUID(int=1)}),
            required_policy_version=None,
            required_score_version=None,
            actor="operator",
            reason="Test invalid baseline",
        )
