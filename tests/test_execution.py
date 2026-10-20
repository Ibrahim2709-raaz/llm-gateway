from decimal import Decimal
from uuid import uuid4

import pytest

from app.execution.service import ProviderExecutionExhausted, ProviderExecutor
from app.providers.base import (
    ModelRoutingProfile,
    ProviderRateLimitError,
    ProviderRequestError,
    ProviderResult,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.routing.service import RoutingCandidate, RoutingPlan
from app.schemas.chat import ChatCompletionRequest, ChatMessage


class SequenceProvider:
    routing_profile = ModelRoutingProfile(
        input_cost_per_million_usd=Decimal("1"),
        output_cost_per_million_usd=Decimal("1"),
        estimated_latency_ms=100,
        quality_score=0.8,
    )

    def __init__(self, name: str, outcomes: list[Exception | ProviderResult]) -> None:
        self.name = name
        self.model = f"{name}-1"
        self.outcomes = outcomes
        self.call_count = 0

    async def complete(self, request: ChatCompletionRequest) -> ProviderResult:
        outcome = self.outcomes[min(self.call_count, len(self.outcomes) - 1)]
        self.call_count += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def result(model: str) -> ProviderResult:
    return ProviderResult(
        content="ok",
        model=model,
        prompt_tokens=1,
        completion_tokens=1,
        finish_reason="stop",
    )


def plan(*providers: SequenceProvider) -> RoutingPlan:
    return RoutingPlan(
        candidates=tuple(
            RoutingCandidate(provider=provider, estimated_cost_usd=Decimal("0.001"))
            for provider in providers
        ),
        task_complexity="simple",
    )


def request() -> ChatCompletionRequest:
    return ChatCompletionRequest(messages=[ChatMessage(role="user", content="Hello")])


def executor(delays: list[float], *, max_retries: int = 2) -> ProviderExecutor:
    async def record_delay(delay: float) -> None:
        delays.append(delay)

    return ProviderExecutor(
        max_retries=max_retries,
        retry_base_delay_seconds=0.1,
        retry_max_delay_seconds=0.15,
        sleeper=record_delay,
    )


@pytest.mark.anyio
async def test_executor_retries_with_capped_exponential_backoff() -> None:
    provider = SequenceProvider(
        "primary",
        [
            ProviderTimeoutError("timeout"),
            ProviderRateLimitError("limited"),
            result("primary-1"),
        ],
    )
    delays: list[float] = []

    execution = await executor(delays).execute(plan(provider), request(), uuid4())

    assert provider.call_count == 3
    assert delays == [0.1, 0.15]
    assert [attempt.outcome for attempt in execution.attempts] == [
        "timeout",
        "rate_limited",
        "success",
    ]


@pytest.mark.anyio
async def test_executor_falls_back_after_primary_retries_are_exhausted() -> None:
    primary = SequenceProvider("primary", [ProviderUnavailableError("down")])
    fallback = SequenceProvider("fallback", [result("fallback-1")])
    delays: list[float] = []

    execution = await executor(delays, max_retries=1).execute(
        plan(primary, fallback), request(), uuid4()
    )

    assert primary.call_count == 2
    assert fallback.call_count == 1
    assert execution.candidate.provider is fallback
    assert [attempt.provider for attempt in execution.attempts] == [
        "primary",
        "primary",
        "fallback",
    ]
    assert [attempt.outcome for attempt in execution.attempts] == [
        "unavailable",
        "unavailable",
        "success",
    ]


@pytest.mark.anyio
async def test_permanent_request_error_is_not_retried_or_fallen_back() -> None:
    primary = SequenceProvider("primary", [ProviderRequestError("invalid")])
    fallback = SequenceProvider("fallback", [result("fallback-1")])
    delays: list[float] = []

    with pytest.raises(ProviderRequestError):
        await executor(delays).execute(plan(primary, fallback), request(), uuid4())

    assert primary.call_count == 1
    assert fallback.call_count == 0
    assert delays == []


@pytest.mark.anyio
async def test_exhausted_execution_reports_all_timeouts() -> None:
    first = SequenceProvider("first", [ProviderTimeoutError("timeout")])
    second = SequenceProvider("second", [ProviderTimeoutError("timeout")])

    with pytest.raises(ProviderExecutionExhausted) as error:
        await executor([], max_retries=0).execute(plan(first, second), request(), uuid4())

    assert error.value.all_attempts_timed_out is True
    assert len(error.value.attempts) == 2


@pytest.mark.anyio
async def test_exhausted_execution_distinguishes_mixed_failures() -> None:
    first = SequenceProvider("first", [ProviderTimeoutError("timeout")])
    second = SequenceProvider("second", [ProviderUnavailableError("down")])

    with pytest.raises(ProviderExecutionExhausted) as error:
        await executor([], max_retries=0).execute(plan(first, second), request(), uuid4())

    assert error.value.all_attempts_timed_out is False
    assert isinstance(error.value.last_error, ProviderUnavailableError)
