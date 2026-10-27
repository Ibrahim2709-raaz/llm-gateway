from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from time import perf_counter
from typing import Any
from uuid import UUID

from app.evaluations.scoring import score_output
from app.providers.base import (
    ChatProvider,
    ProviderError,
    ProviderRequestError,
    ProviderTimeoutError,
)
from app.schemas.chat import ChatCompletionRequest


@dataclass(frozen=True, slots=True)
class EvaluationCaseSpec:
    id: UUID
    prompt: dict[str, Any]
    expected_output: dict[str, Any] | None
    scoring_configuration: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CaseEvaluation:
    evaluation_case_id: UUID
    outcome: str
    actual_output: dict[str, Any] | None
    score: Decimal | None
    metrics: dict[str, Any]
    prompt_tokens: int
    completion_tokens: int
    cost_usd: Decimal
    latency_ms: int
    error_code: str | None


@dataclass(frozen=True, slots=True)
class EvaluationBatchResult:
    results: tuple[CaseEvaluation, ...]
    skipped_case_ids: tuple[UUID, ...]


class EvaluationRunner:
    def __init__(
        self,
        provider: ChatProvider,
        *,
        clock: Callable[[], float] = perf_counter,
    ) -> None:
        self.provider = provider
        self.clock = clock

    async def run(
        self,
        cases: Sequence[EvaluationCaseSpec],
        *,
        completed_case_ids: frozenset[UUID] = frozenset(),
    ) -> EvaluationBatchResult:
        results: list[CaseEvaluation] = []
        skipped: list[UUID] = []
        for case in cases:
            if case.id in completed_case_ids:
                skipped.append(case.id)
                continue
            results.append(await self._evaluate_case(case))
        return EvaluationBatchResult(tuple(results), tuple(skipped))

    async def _evaluate_case(self, case: EvaluationCaseSpec) -> CaseEvaluation:
        started = self.clock()
        try:
            request = ChatCompletionRequest.model_validate(case.prompt)
            provider_result = await self.provider.complete(request)
            scoring = score_output(
                provider_result.content,
                expected_output=case.expected_output,
                configuration=case.scoring_configuration,
            )
            return CaseEvaluation(
                evaluation_case_id=case.id,
                outcome="completed",
                actual_output={"text": provider_result.content},
                score=scoring.score,
                metrics=scoring.metrics,
                prompt_tokens=provider_result.prompt_tokens,
                completion_tokens=provider_result.completion_tokens,
                cost_usd=provider_result.cost_usd,
                latency_ms=self._latency_ms(started),
                error_code=None,
            )
        except ProviderTimeoutError:
            return self._failed_result(case.id, "timed_out", "provider_timeout", started)
        except ProviderRequestError:
            return self._failed_result(case.id, "refused", "provider_refused", started)
        except ProviderError:
            return self._failed_result(case.id, "failed", "provider_error", started)
        except (ValueError, TypeError):
            return self._failed_result(case.id, "failed", "invalid_evaluation_case", started)

    def _failed_result(
        self,
        case_id: UUID,
        outcome: str,
        error_code: str,
        started: float,
    ) -> CaseEvaluation:
        return CaseEvaluation(
            evaluation_case_id=case_id,
            outcome=outcome,
            actual_output=None,
            score=None,
            metrics={},
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=Decimal("0"),
            latency_ms=self._latency_ms(started),
            error_code=error_code,
        )

    def _latency_ms(self, started: float) -> int:
        return max(0, round((self.clock() - started) * 1000))
