from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.db.models import (
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationDataset,
    EvaluationRun,
    ModelCapabilityScore,
)
from app.platform.evaluation_console import (
    list_evaluation_failures,
    list_evaluation_runs,
    list_score_versions,
)

NOW = datetime(2026, 11, 9, 12, tzinfo=UTC)


def execution_result(rows: list[object]) -> MagicMock:
    result = MagicMock()
    result.all.return_value = rows
    return result


@pytest.mark.anyio
async def test_list_evaluation_runs_uses_safe_aggregate_metadata() -> None:
    dataset = EvaluationDataset(id=uuid4(), name="coding", version="1.0", status="active")
    run = EvaluationRun(
        id=uuid4(),
        dataset_id=dataset.id,
        provider="openai",
        model="model-a",
        status="completed",
        runner_version="runner-v1",
        aggregate_metrics={
            "case_count": 10,
            "failed_case_count": 2,
            "average_score": 0.8,
            "total_cost_usd": "0.12",
        },
        started_at=NOW,
        completed_at=NOW + timedelta(minutes=2),
    )
    database = MagicMock()
    database.execute = AsyncMock(return_value=execution_result([(run, dataset)]))

    views = await list_evaluation_runs(database, limit=10)

    assert views[0].case_count == 10
    assert views[0].failed_case_count == 2
    assert views[0].total_cost_usd == Decimal("0.12")
    assert not hasattr(views[0], "prompt")
    assert not hasattr(views[0], "actual_output")


@pytest.mark.anyio
async def test_failure_review_excludes_prompt_and_output_content() -> None:
    run_id = uuid4()
    case = EvaluationCase(
        id=uuid4(),
        dataset_id=uuid4(),
        external_id="reasoning-17",
        task_category="reasoning",
        difficulty="medium",
        prompt={"messages": [{"content": "private prompt"}]},
        scoring_configuration={},
    )
    result = EvaluationCaseResult(
        id=uuid4(),
        evaluation_run_id=run_id,
        evaluation_case_id=case.id,
        outcome="failed",
        actual_output={"text": "private output"},
        score=Decimal("0"),
        metrics={},
        prompt_tokens=20,
        completion_tokens=5,
        cost_usd=Decimal("0.001"),
        latency_ms=500,
        error_code="provider_error",
    )
    database = MagicMock()
    database.scalar = AsyncMock(return_value=run_id)
    database.execute = AsyncMock(return_value=execution_result([(result, case)]))

    failures = await list_evaluation_failures(database, run_id=run_id)

    assert failures[0].case_external_id == "reasoning-17"
    assert failures[0].error_code == "provider_error"
    assert not hasattr(failures[0], "prompt")
    assert not hasattr(failures[0], "actual_output")


@pytest.mark.anyio
async def test_failure_review_rejects_unknown_run() -> None:
    database = MagicMock()
    database.scalar = AsyncMock(return_value=None)

    with pytest.raises(ValueError, match="evaluation_run_not_found"):
        await list_evaluation_failures(database, run_id=uuid4())


@pytest.mark.anyio
async def test_score_versions_are_grouped_with_weighted_quality() -> None:
    run_id = uuid4()
    entries = [
        ModelCapabilityScore(
            id=uuid4(), evaluation_run_id=run_id, provider="openai", model="model-a",
            score_version="scores-v2", task_category="coding", difficulty="simple",
            quality_score=Decimal("0.9"), sample_count=10, capabilities=["text_generation"],
            status="draft", valid_until=None,
        ),
        ModelCapabilityScore(
            id=uuid4(), evaluation_run_id=run_id, provider="openai", model="model-a",
            score_version="scores-v2", task_category="reasoning", difficulty="medium",
            quality_score=Decimal("0.5"), sample_count=30, capabilities=["text_generation"],
            status="draft", valid_until=None,
        ),
    ]
    scalar_result = MagicMock()
    scalar_result.all.return_value = entries
    database = MagicMock()
    database.scalars = AsyncMock(return_value=scalar_result)

    summaries = await list_score_versions(database)

    assert summaries[0].sample_count == 40
    assert summaries[0].score_count == 2
    assert summaries[0].weighted_quality_score == pytest.approx(0.6)
    assert summaries[0].task_categories == ("coding", "reasoning")
