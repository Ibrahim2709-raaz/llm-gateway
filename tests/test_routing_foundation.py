from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.cli.routing_foundation import build_parser, format_inspection
from app.db.models import EvaluationDataset, RoutingPolicyVersion
from app.routing.foundation import (
    BASELINE_DATASET_NAME,
    BASELINE_DATASET_VERSION,
    BASELINE_POLICY_VERSION,
    DatasetSummary,
    FoundationInspection,
    PolicySummary,
    inspect_routing_foundation,
    seed_routing_foundation,
)


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
    session.execute.side_effect = [dataset_result, policy_result]
    session.scalar.return_value = 3

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
    assert session.execute.await_count == 2


def test_routing_foundation_cli_parser_and_output() -> None:
    assert build_parser().parse_args(["seed"]).command == "seed"
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
    )

    output = format_inspection(inspection)

    assert "core-task-taxonomy 1.0.0 [draft]" in output
    assert "baseline-v1 [active]" in output
    assert "minimum_samples=5 cold_start=static_profile" in output
    assert "Capability scores: 0" in output
