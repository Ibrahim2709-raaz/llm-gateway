from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import EvaluationCase, EvaluationCaseResult, EvaluationRun
from app.evaluations.service import (
    RUNNER_VERSION,
    DatabaseEvaluationService,
    EvaluationRunError,
)
from app.providers.base import ModelRoutingProfile, ProviderResult
from app.schemas.chat import ChatCompletionRequest

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


class EvaluationProvider:
    name = "mock"
    model = "mock-eval-1"
    routing_profile = ModelRoutingProfile(
        input_cost_per_million_usd=Decimal("1"),
        output_cost_per_million_usd=Decimal("1"),
        estimated_latency_ms=1,
        quality_score=0.5,
    )

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, request: ChatCompletionRequest) -> ProviderResult:
        self.calls += 1
        return ProviderResult(
            content="Paris",
            model=self.model,
            prompt_tokens=4,
            completion_tokens=1,
            finish_reason="stop",
            cost_usd=Decimal("0.000005"),
        )


def evaluation_run(*, status: str = "pending") -> EvaluationRun:
    return EvaluationRun(
        id=uuid4(),
        dataset_id=uuid4(),
        provider="mock",
        model="mock-eval-1",
        status=status,
        runner_version=RUNNER_VERSION,
        configuration={},
        aggregate_metrics={},
    )


def evaluation_case(run: EvaluationRun, external_id: str = "case-1") -> EvaluationCase:
    return EvaluationCase(
        id=uuid4(),
        dataset_id=run.dataset_id,
        external_id=external_id,
        task_category="reasoning",
        difficulty="simple",
        prompt={"messages": [{"role": "user", "content": "Capital of France?"}]},
        expected_output={"text": "Paris"},
        scoring_configuration={"method": "exact_match"},
    )


def query_result(values: list[object]) -> Mock:
    result = Mock()
    result.scalars.return_value.all.return_value = values
    return result


@pytest.mark.anyio
async def test_database_service_commits_each_case_and_completes_run() -> None:
    run = evaluation_run()
    case = evaluation_case(run)
    session = AsyncMock(spec=AsyncSession)
    session.scalar.return_value = run
    session.execute.side_effect = [query_result([case]), query_result([])]
    provider = EvaluationProvider()
    service = DatabaseEvaluationService(session, (provider,), now=lambda: NOW)

    summary = await service.execute(run.id)

    assert run.status == "completed"
    assert run.started_at == NOW
    assert run.completed_at == NOW
    assert run.aggregate_metrics["case_count"] == 1
    assert run.aggregate_metrics["average_score"] == 1.0
    assert summary.completed_cases == 1
    assert summary.skipped_cases == 0
    assert summary.total_cost_usd == Decimal("0.000005")
    stored = session.add.call_args.args[0]
    assert isinstance(stored, EvaluationCaseResult)
    assert stored.evaluation_case_id == case.id
    assert stored.score == Decimal("1")
    assert session.commit.await_count == 3


@pytest.mark.anyio
async def test_resume_skips_immutable_existing_results() -> None:
    run = evaluation_run(status="running")
    first = evaluation_case(run, "case-1")
    second = evaluation_case(run, "case-2")
    existing = EvaluationCaseResult(
        evaluation_run_id=run.id,
        evaluation_case_id=first.id,
        outcome="completed",
        actual_output={"text": "Paris"},
        score=Decimal("1"),
        metrics={"method": "exact_match"},
        prompt_tokens=4,
        completion_tokens=1,
        cost_usd=Decimal("0.000005"),
        latency_ms=1,
    )
    session = AsyncMock(spec=AsyncSession)
    session.scalar.return_value = run
    session.execute.side_effect = [query_result([first, second]), query_result([existing])]
    provider = EvaluationProvider()

    summary = await DatabaseEvaluationService(
        session, (provider,), now=lambda: NOW
    ).execute(run.id, resume=True)

    assert provider.calls == 1
    assert summary.completed_cases == 2
    assert summary.skipped_cases == 1
    assert session.add.call_count == 1


@pytest.mark.anyio
async def test_running_run_requires_explicit_resume() -> None:
    run = evaluation_run(status="running")
    session = AsyncMock(spec=AsyncSession)
    session.scalar.return_value = run

    with pytest.raises(EvaluationRunError) as error:
        await DatabaseEvaluationService(session, (EvaluationProvider(),)).execute(run.id)

    assert error.value.code == "run_already_running"
    session.commit.assert_not_awaited()
