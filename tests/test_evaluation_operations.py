from decimal import Decimal
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.cli.evaluation_harness import build_parser
from app.db.models import EvaluationCase, EvaluationDataset, EvaluationRun
from app.evaluations.operations import (
    SMOKE_DATASET_NAME,
    SMOKE_DATASET_VERSION,
    create_evaluation_run,
    inspect_evaluation_run,
    seed_smoke_evaluation_dataset,
)
from app.evaluations.service import RUNNER_VERSION


def scalar_result(values: list[str]) -> Mock:
    result = Mock()
    result.scalars.return_value.all.return_value = values
    return result


@pytest.mark.anyio
async def test_smoke_dataset_seed_is_idempotent() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.scalar.return_value = None
    session.execute.return_value = scalar_result([])

    created = await seed_smoke_evaluation_dataset(session)

    assert created.dataset_created is True
    assert created.cases_created == 3
    dataset = session.add.call_args_list[0].args[0]
    assert isinstance(dataset, EvaluationDataset)
    assert dataset.configuration["production_prompt_storage"] is False
    cases = [call.args[0] for call in session.add.call_args_list[1:]]
    assert all(isinstance(case, EvaluationCase) for case in cases)
    assert {case.external_id for case in cases} == {
        "exact-capital",
        "required-phrases",
        "unscored-chat",
    }

    existing_session = AsyncMock(spec=AsyncSession)
    existing_session.scalar.return_value = dataset
    existing_session.execute.return_value = scalar_result(
        ["exact-capital", "required-phrases", "unscored-chat"]
    )
    repeated = await seed_smoke_evaluation_dataset(existing_session)

    assert repeated.dataset_created is False
    assert repeated.cases_created == 0
    existing_session.add.assert_not_called()


@pytest.mark.anyio
async def test_create_run_pins_dataset_provider_model_and_runner() -> None:
    dataset = EvaluationDataset(
        id=uuid4(),
        name=SMOKE_DATASET_NAME,
        version=SMOKE_DATASET_VERSION,
        description="Synthetic",
        status="active",
        configuration={},
    )
    session = AsyncMock(spec=AsyncSession)
    session.scalar.return_value = dataset

    run = await create_evaluation_run(
        session,
        dataset_name=dataset.name,
        dataset_version=dataset.version,
        provider="mock",
        model="mock-1",
    )

    assert run.status == "pending"
    assert run.runner_version == RUNNER_VERSION
    assert run.provider == "mock"
    assert run.model == "mock-1"
    assert run.configuration["dataset_version"] == SMOKE_DATASET_VERSION
    session.add.assert_called_once_with(run)


@pytest.mark.anyio
async def test_inspect_run_returns_sanitized_aggregates() -> None:
    run = EvaluationRun(
        id=uuid4(),
        dataset_id=uuid4(),
        provider="mock",
        model="mock-1",
        status="completed",
        runner_version=RUNNER_VERSION,
        configuration={},
        aggregate_metrics={"average_score": 1.0},
    )
    session = AsyncMock(spec=AsyncSession)
    session.get.return_value = run
    session.scalar.side_effect = [3, 1, Decimal("0.25")]

    inspection = await inspect_evaluation_run(session, run.id)

    assert inspection.result_count == 3
    assert inspection.failed_result_count == 1
    assert inspection.total_cost_usd == Decimal("0.25")
    assert not hasattr(inspection, "configuration")


def test_evaluation_cli_parser_supports_full_operator_workflow() -> None:
    assert build_parser().parse_args(["seed-smoke"]).command == "seed-smoke"
    create = build_parser().parse_args(
        ["create", "--provider", "mock", "--model", "mock-1"]
    )
    assert create.dataset == SMOKE_DATASET_NAME
    assert create.dataset_version == SMOKE_DATASET_VERSION
    run_id = uuid4()
    assert build_parser().parse_args(["resume", str(run_id)]).run_id == run_id
