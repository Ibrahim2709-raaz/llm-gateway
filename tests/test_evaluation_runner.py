from decimal import Decimal
from uuid import uuid4

import pytest

from app.evaluations.runner import EvaluationCaseSpec, EvaluationRunner
from app.providers.base import (
    ModelRoutingProfile,
    ProviderRequestError,
    ProviderResult,
    ProviderTimeoutError,
)
from app.schemas.chat import ChatCompletionRequest


class EvaluationProvider:
    name = "evaluation"
    model = "evaluation-1"
    routing_profile = ModelRoutingProfile(
        input_cost_per_million_usd=Decimal("1"),
        output_cost_per_million_usd=Decimal("2"),
        estimated_latency_ms=10,
        quality_score=0.5,
    )

    def __init__(self, result: ProviderResult | Exception) -> None:
        self.result = result
        self.calls = 0

    async def complete(self, request: ChatCompletionRequest) -> ProviderResult:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def case() -> EvaluationCaseSpec:
    return EvaluationCaseSpec(
        id=uuid4(),
        prompt={"messages": [{"role": "user", "content": "Capital of France?"}]},
        expected_output={"text": "Paris"},
        scoring_configuration={"method": "exact_match"},
    )


@pytest.mark.anyio
async def test_runner_records_output_score_usage_cost_and_latency() -> None:
    provider = EvaluationProvider(
        ProviderResult(
            content="Paris",
            model="evaluation-1",
            prompt_tokens=5,
            completion_tokens=1,
            finish_reason="stop",
            cost_usd=Decimal("0.000007"),
        )
    )
    times = iter([1.0, 1.025])
    runner = EvaluationRunner(provider, clock=lambda: next(times))

    batch = await runner.run([case()])

    result = batch.results[0]
    assert result.outcome == "completed"
    assert result.actual_output == {"text": "Paris"}
    assert result.score == Decimal("1")
    assert result.prompt_tokens == 5
    assert result.completion_tokens == 1
    assert result.cost_usd == Decimal("0.000007")
    assert result.latency_ms == 25
    assert result.error_code is None


@pytest.mark.anyio
async def test_runner_skips_completed_cases_when_resuming() -> None:
    first = case()
    second = case()
    provider = EvaluationProvider(
        ProviderResult("Paris", "evaluation-1", 1, 1, "stop")
    )
    runner = EvaluationRunner(provider)

    batch = await runner.run([first, second], completed_case_ids=frozenset({first.id}))

    assert batch.skipped_case_ids == (first.id,)
    assert [result.evaluation_case_id for result in batch.results] == [second.id]
    assert provider.calls == 1


@pytest.mark.parametrize(
    ("error", "outcome", "error_code"),
    [
        (ProviderTimeoutError("timeout"), "timed_out", "provider_timeout"),
        (ProviderRequestError("refused"), "refused", "provider_refused"),
    ],
)
@pytest.mark.anyio
async def test_runner_preserves_provider_failures(
    error: Exception, outcome: str, error_code: str
) -> None:
    runner = EvaluationRunner(EvaluationProvider(error))

    batch = await runner.run([case()])

    result = batch.results[0]
    assert result.outcome == outcome
    assert result.error_code == error_code
    assert result.actual_output is None
    assert result.score is None
