from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.cli import routing_foundation as routing_cli
from app.cli.routing_foundation import build_parser, format_inspection
from app.db.models import (
    EvaluationDataset,
    RoutingPolicyActivation,
    RoutingPolicyVersion,
    RoutingRolloutChange,
    RoutingRolloutConfiguration,
)
from app.routing.control_plane import RoutingControlChangeResult
from app.routing.foundation import (
    BASELINE_DATASET_NAME,
    BASELINE_DATASET_VERSION,
    BASELINE_POLICY_VERSION,
    ActivationSummary,
    DatasetSummary,
    FoundationInspection,
    PolicySummary,
    inspect_routing_foundation,
    seed_routing_foundation,
)
from app.routing.rollout import IntelligentRoutingRollout, RoutingRuntimeConfiguration


class AsyncSessionContext:
    def __init__(self, session: Mock) -> None:
        self.session = session

    async def __aenter__(self) -> Mock:
        return self.session

    async def __aexit__(self, *_args: object) -> None:
        return None


class FakeSessionFactory:
    def __init__(self) -> None:
        self.session = Mock()

    def begin(self) -> AsyncSessionContext:
        return AsyncSessionContext(self.session)

    def __call__(self) -> AsyncSessionContext:
        return AsyncSessionContext(self.session)


@pytest.mark.anyio
async def test_seed_routing_foundation_creates_versioned_baseline_records() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.scalar.side_effect = [None, None]

    result = await seed_routing_foundation(session)

    assert result.dataset_created is True
    assert result.policy_created is True
    dataset = session.add.call_args_list[0].args[0]
    policy = session.add.call_args_list[1].args[0]
    assert isinstance(dataset, EvaluationDataset)
    assert dataset.name == BASELINE_DATASET_NAME
    assert dataset.version == BASELINE_DATASET_VERSION
    assert dataset.status == "draft"
    assert dataset.configuration["production_prompt_storage"] is False
    assert isinstance(policy, RoutingPolicyVersion)
    assert policy.version == BASELINE_POLICY_VERSION
    assert policy.status == "active"
    assert policy.classifier_version == "complexity-rules-v1"
    assert policy.minimum_sample_count == 5
    assert policy.cold_start_mode == "static_profile"
    assert policy.strategy_weights["complex"]["quality"] == 0.75
    session.flush.assert_awaited_once()


@pytest.mark.anyio
async def test_seed_routing_foundation_is_idempotent() -> None:
    dataset = EvaluationDataset(
        name=BASELINE_DATASET_NAME,
        version=BASELINE_DATASET_VERSION,
        description="Existing",
        status="draft",
        configuration={},
    )
    policy = RoutingPolicyVersion(
        version=BASELINE_POLICY_VERSION,
        status="active",
        classifier_version="complexity-rules-v1",
        score_version="static-model-profiles-v1",
        strategy_weights={},
        minimum_quality={},
        minimum_sample_count=5,
        cold_start_mode="static_profile",
    )
    session = AsyncMock(spec=AsyncSession)
    session.scalar.side_effect = [dataset, policy]

    result = await seed_routing_foundation(session)

    assert result.dataset_created is False
    assert result.policy_created is False
    session.add.assert_not_called()
    session.flush.assert_awaited_once()


@pytest.mark.anyio
async def test_inspect_routing_foundation_returns_safe_summary() -> None:
    dataset = EvaluationDataset(
        name=BASELINE_DATASET_NAME,
        version=BASELINE_DATASET_VERSION,
        description="Existing",
        status="draft",
        configuration={},
    )
    policy = RoutingPolicyVersion(
        version=BASELINE_POLICY_VERSION,
        status="active",
        classifier_version="complexity-rules-v1",
        score_version="static-model-profiles-v1",
        strategy_weights={},
        minimum_quality={},
        minimum_sample_count=5,
        cold_start_mode="static_profile",
    )
    dataset_result = Mock()
    dataset_result.scalars.return_value.all.return_value = [dataset]
    policy_result = Mock()
    policy_result.scalars.return_value.all.return_value = [policy]
    session = AsyncMock(spec=AsyncSession)
    session.scalar.return_value = 3
    activation_result = Mock()
    activation_result.all.return_value = []
    session.execute.side_effect = [dataset_result, policy_result, activation_result]

    inspection = await inspect_routing_foundation(session)

    assert inspection.datasets == (
        DatasetSummary(name=BASELINE_DATASET_NAME, version="1.0.0", status="draft"),
    )
    assert inspection.policies == (
        PolicySummary(
            version=BASELINE_POLICY_VERSION,
            status="active",
            classifier_version="complexity-rules-v1",
            score_version="static-model-profiles-v1",
            minimum_sample_count=5,
            cold_start_mode="static_profile",
        ),
    )
    assert inspection.capability_score_count == 3
    assert inspection.activations == ()
    assert session.execute.await_count == 3


def test_routing_foundation_cli_parser_and_output() -> None:
    assert build_parser().parse_args(["seed"]).command == "seed"
    activation_args = build_parser().parse_args(
        ["activate", "candidate-v1", "--actor", "release-bot", "--reason", "gate passed"]
    )
    assert activation_args.version == "candidate-v1"
    assert activation_args.actor == "release-bot"
    emergency_args = build_parser().parse_args(
        [
            "emergency-baseline",
            "--actor",
            "incident-commander",
            "--reason",
            "provider incident",
        ]
    )
    assert emergency_args.command == "emergency-baseline"
    rollout_args = build_parser().parse_args(
        [
            "rollout-set",
            "--mode",
            "live",
            "--canary-percent",
            "5",
            "--policy-version",
            "policy-v2",
            "--score-version",
            "scores-v2",
            "--actor",
            "release-bot",
            "--reason",
            "start canary",
        ]
    )
    assert rollout_args.canary_percent == 5
    inspection = FoundationInspection(
        datasets=(DatasetSummary("core-task-taxonomy", "1.0.0", "draft"),),
        policies=(
            PolicySummary(
                "baseline-v1",
                "active",
                "complexity-rules-v1",
                "static-model-profiles-v1",
                5,
                "static_profile",
            ),
        ),
        capability_score_count=0,
        activations=(
            ActivationSummary("candidate-v1", "baseline-v1", "activate", "release-bot"),
        ),
    )

    output = format_inspection(inspection)

    assert "core-task-taxonomy 1.0.0 [draft]" in output
    assert "baseline-v1 [active]" in output
    assert "minimum_samples=5 cold_start=static_profile" in output
    assert "Capability scores: 0" in output
    assert "activate candidate-v1 from=baseline-v1 actor=release-bot" in output


@pytest.mark.anyio
async def test_inspection_returns_activation_history_without_reason_or_secrets() -> None:
    baseline = RoutingPolicyVersion(
        id=uuid4(),
        version="baseline-v1",
        status="retired",
        classifier_version="complexity-rules-v1",
        score_version="static-model-profiles-v1",
        strategy_weights={},
        minimum_quality={},
        minimum_sample_count=5,
        cold_start_mode="static_profile",
    )
    candidate = RoutingPolicyVersion(
        id=uuid4(),
        version="candidate-v1",
        status="active",
        classifier_version="classifier-v1",
        score_version="scores-v1",
        strategy_weights={},
        minimum_quality={},
        minimum_sample_count=5,
        cold_start_mode="static_profile",
    )
    event = RoutingPolicyActivation(
        policy_id=candidate.id,
        previous_policy_id=baseline.id,
        action="activate",
        actor="release-bot",
        reason="private operational detail",
    )
    dataset_result = Mock()
    dataset_result.scalars.return_value.all.return_value = []
    policy_result = Mock()
    policy_result.scalars.return_value.all.return_value = [baseline, candidate]
    activation_result = Mock()
    activation_result.all.return_value = [(event, "candidate-v1")]
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = [dataset_result, policy_result, activation_result]
    session.scalar.return_value = 0

    inspection = await inspect_routing_foundation(session)

    assert inspection.activations == (
        ActivationSummary("candidate-v1", "baseline-v1", "activate", "release-bot"),
    )


@pytest.mark.anyio
async def test_cli_dispatches_rollout_show_update_and_emergency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = SimpleNamespace(dispose=AsyncMock())
    factory = FakeSessionFactory()
    runtime = RoutingRuntimeConfiguration(
        mode="shadow",
        rollout=IntelligentRoutingRollout(percentage=0),
        required_policy_version="policy-v2",
        required_score_version="scores-v2",
        source="database",
        version=2,
    )
    configuration = RoutingRolloutConfiguration(
        id=uuid4(),
        name="default",
        mode="baseline",
        canary_percent=0,
        customer_allowlist=[],
        required_policy_version=None,
        required_score_version=None,
        version=3,
        updated_by_actor="incident-commander",
    )
    change = RoutingRolloutChange(
        id=uuid4(),
        configuration_id=configuration.id,
        action="emergency_baseline",
        actor="incident-commander",
        reason="provider incident",
        previous_configuration={},
        new_configuration={},
    )
    result = RoutingControlChangeResult(configuration, change)
    resolver = Mock(load=AsyncMock(return_value=runtime))
    update = AsyncMock(return_value=result)
    emergency = AsyncMock(return_value=result)
    monkeypatch.setattr(
        routing_cli, "get_settings", Mock(return_value=SimpleNamespace(database_url="db"))
    )
    monkeypatch.setattr(routing_cli, "create_async_engine", Mock(return_value=engine))
    monkeypatch.setattr(routing_cli, "session_factory", Mock(return_value=factory))
    monkeypatch.setattr(
        routing_cli, "DatabaseRoutingControlResolver", Mock(return_value=resolver)
    )
    monkeypatch.setattr(routing_cli, "change_routing_control", update)
    monkeypatch.setattr(routing_cli, "emergency_baseline", emergency)

    shown = await routing_cli.run("rollout-show")
    changed = await routing_cli.run(
        "rollout-set",
        mode="live",
        canary_percent=5,
        allow_clients=[str(uuid4())],
        policy_version="policy-v2",
        score_version="scores-v2",
        actor="release-bot",
        reason="start canary",
    )
    rolled_back = await routing_cli.run(
        "emergency-baseline",
        actor="incident-commander",
        reason="provider incident",
    )

    assert "shadow" in shown
    assert "baseline" in changed
    assert "Emergency baseline enabled" in rolled_back
    update.assert_awaited_once()
    emergency.assert_awaited_once()
    assert engine.dispose.await_count == 3
