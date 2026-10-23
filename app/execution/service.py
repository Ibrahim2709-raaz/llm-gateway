import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import structlog

from app.observability.metrics import MetricsRecorder
from app.providers.base import (
    ProviderRateLimitError,
    ProviderRequestError,
    ProviderResult,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.routing.service import RoutingCandidate, RoutingPlan
from app.schemas.chat import ChatCompletionRequest, RoutingAttempt

logger = structlog.get_logger(__name__)
RetryableProviderError = ProviderTimeoutError | ProviderRateLimitError | ProviderUnavailableError
Sleeper = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ProviderExecutionResult:
    candidate: RoutingCandidate
    result: ProviderResult
    attempts: tuple[RoutingAttempt, ...]


class ProviderExecutionExhausted(Exception):
    def __init__(
        self,
        attempts: tuple[RoutingAttempt, ...],
        last_error: RetryableProviderError,
    ) -> None:
        super().__init__("All eligible provider attempts failed")
        self.attempts = attempts
        self.last_error = last_error

    @property
    def all_attempts_timed_out(self) -> bool:
        return bool(self.attempts) and all(
            attempt.outcome == "timeout" for attempt in self.attempts
        )


class ProviderExecutor:
    def __init__(
        self,
        *,
        max_retries: int,
        retry_base_delay_seconds: float,
        retry_max_delay_seconds: float,
        sleeper: Sleeper | None = None,
        metrics: MetricsRecorder | None = None,
    ) -> None:
        self.max_retries = max_retries
        self.retry_base_delay_seconds = retry_base_delay_seconds
        self.retry_max_delay_seconds = retry_max_delay_seconds
        self.sleeper = sleeper or asyncio.sleep
        self.metrics = metrics

    async def execute(
        self,
        plan: RoutingPlan,
        request: ChatCompletionRequest,
        request_id: UUID,
    ) -> ProviderExecutionResult:
        attempts: list[RoutingAttempt] = []
        last_error: RetryableProviderError | None = None
        for candidate_index, candidate in enumerate(plan.candidates):
            provider = candidate.provider
            if candidate_index > 0 and self.metrics is not None:
                self.metrics.record_fallback(
                    plan.candidates[candidate_index - 1].provider.name,
                    provider.name,
                )
            for retry_index in range(self.max_retries + 1):
                try:
                    result = await provider.complete(request)
                except ProviderRequestError:
                    self._record_attempt(provider.name, provider.model, "error")
                    self._log_attempt(
                        request_id,
                        provider.name,
                        provider.model,
                        "error",
                        retry_index,
                        candidate_index,
                    )
                    raise
                except (
                    ProviderTimeoutError,
                    ProviderRateLimitError,
                    ProviderUnavailableError,
                ) as error:
                    last_error = error
                    outcome = self._outcome(error)
                    self._record_attempt(provider.name, provider.model, outcome)
                    attempts.append(
                        RoutingAttempt(
                            provider=provider.name,
                            model=provider.model,
                            outcome=outcome,
                        )
                    )
                    self._log_attempt(
                        request_id,
                        provider.name,
                        provider.model,
                        outcome,
                        retry_index,
                        candidate_index,
                    )
                    if retry_index < self.max_retries:
                        await self.sleeper(self._retry_delay(retry_index))
                        continue
                    break
                attempts.append(
                    RoutingAttempt(
                        provider=provider.name,
                        model=result.model,
                        outcome="success",
                    )
                )
                self._record_attempt(provider.name, result.model, "success")
                self._log_attempt(
                    request_id,
                    provider.name,
                    result.model,
                    "success",
                    retry_index,
                    candidate_index,
                )
                return ProviderExecutionResult(
                    candidate=candidate,
                    result=result,
                    attempts=tuple(attempts),
                )

        if last_error is None:
            raise RuntimeError("A routing plan must contain at least one candidate")
        raise ProviderExecutionExhausted(tuple(attempts), last_error)

    def _record_attempt(self, provider: str, model: str, outcome: str) -> None:
        if self.metrics is not None:
            self.metrics.record_provider_attempt(provider, model, outcome)

    def _retry_delay(self, retry_index: int) -> float:
        return min(
            self.retry_base_delay_seconds * (2.0**retry_index),
            self.retry_max_delay_seconds,
        )

    @staticmethod
    def _outcome(
        error: RetryableProviderError,
    ) -> Literal["timeout", "rate_limited", "unavailable"]:
        if isinstance(error, ProviderTimeoutError):
            return "timeout"
        if isinstance(error, ProviderRateLimitError):
            return "rate_limited"
        return "unavailable"

    @staticmethod
    def _log_attempt(
        request_id: UUID,
        provider: str,
        model: str,
        outcome: str,
        retry_index: int,
        candidate_index: int,
    ) -> None:
        logger.info(
            "provider_attempt_completed",
            request_id=str(request_id),
            provider=provider,
            model=model,
            outcome=outcome,
            attempt=retry_index + 1,
            route_position=candidate_index + 1,
        )
