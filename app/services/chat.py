import time
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

import structlog
from fastapi import status

from app.cache.service import ResponseCache
from app.core.errors import GatewayError
from app.execution.service import (
    ProviderExecutionExhausted,
    ProviderExecutor,
    Sleeper,
)
from app.observability.metrics import MetricsRecorder
from app.providers.base import ChatProvider, ProviderRequestError
from app.routing.service import ProviderRouter
from app.schemas.chat import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    Choice,
    GatewayMetadata,
    ResponseMessage,
    TokenUsage,
)
from app.usage.service import UsageTracker

logger = structlog.get_logger(__name__)


class ChatCompletionService:
    def __init__(
        self,
        providers: tuple[ChatProvider, ...],
        *,
        max_retries: int = 0,
        retry_base_delay_seconds: float = 0,
        retry_max_delay_seconds: float = 0,
        sleeper: Sleeper | None = None,
        response_cache: ResponseCache | None = None,
        usage_tracker: UsageTracker | None = None,
        metrics: MetricsRecorder | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.router = ProviderRouter(providers)
        self.executor = ProviderExecutor(
            max_retries=max_retries,
            retry_base_delay_seconds=retry_base_delay_seconds,
            retry_max_delay_seconds=retry_max_delay_seconds,
            sleeper=sleeper,
            metrics=metrics,
        )
        self.response_cache = response_cache
        self.usage_tracker = usage_tracker
        self.metrics = metrics
        self.clock = clock

    async def create(
        self,
        request: ChatCompletionRequest,
        request_id: UUID,
        client_id: UUID,
    ) -> ChatCompletionResponse:
        if self.response_cache is not None and not request.routing.disable_cache:
            cached_response = await self.response_cache.get(client_id, request)
            if cached_response is not None:
                self._record_cache_request("hit")
                if self.usage_tracker is not None:
                    await self.usage_tracker.record_cached(request_id, client_id)
                logger.info(
                    "response_cache_hit",
                    request_id=str(request_id),
                    client_id=str(client_id),
                )
                self._record_gateway_request("cached", 0)
                return self._cache_hit_response(cached_response, request_id)
            self._record_cache_request("miss")
        elif request.routing.disable_cache:
            self._record_cache_request("bypass")

        routing_plan = self.router.plan(request)
        if self.usage_tracker is not None:
            try:
                await self.usage_tracker.reserve(
                    request_id,
                    client_id,
                    max(candidate.estimated_cost_usd for candidate in routing_plan.candidates),
                )
            except GatewayError as error:
                if error.code == "budget_exceeded":
                    self._record_gateway_request("budget_rejected", 0)
                raise
        primary = routing_plan.candidates[0]
        logger.info(
            "provider_route_planned",
            request_id=str(request_id),
            provider=primary.provider.name,
            model=primary.provider.model,
            strategy="explicit" if request.model != "auto" else request.routing.strategy,
            task_complexity=routing_plan.task_complexity,
            estimated_cost_usd=str(primary.estimated_cost_usd),
            candidate_count=len(routing_plan.candidates),
        )
        execution_started = self.clock()
        try:
            execution = await self.executor.execute(routing_plan, request, request_id)
        except ProviderRequestError as exc:
            latency_ms = await self._record_failed_usage(request_id, client_id, execution_started)
            self._record_gateway_request("failed", latency_ms / 1000)
            raise GatewayError(
                str(exc),
                code="invalid_request",
                error_type="invalid_request_error",
                status_code=status.HTTP_400_BAD_REQUEST,
                param="max_tokens",
            ) from exc
        except ProviderExecutionExhausted as exc:
            latency_ms = await self._record_failed_usage(request_id, client_id, execution_started)
            self._record_gateway_request("failed", latency_ms / 1000)
            if exc.all_attempts_timed_out:
                raise GatewayError(
                    "All eligible provider attempts timed out.",
                    code="provider_timeout",
                    error_type="provider_error",
                    status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                ) from exc
            raise GatewayError(
                "All eligible provider attempts failed.",
                code="provider_error",
                error_type="provider_error",
                status_code=status.HTTP_502_BAD_GATEWAY,
            ) from exc
        except Exception:
            latency_ms = await self._record_failed_usage(request_id, client_id, execution_started)
            self._record_gateway_request("failed", latency_ms / 1000)
            raise
        provider = execution.candidate.provider
        result = execution.result
        total_tokens = result.prompt_tokens + result.completion_tokens
        routing_strategy = "explicit" if request.model != "auto" else request.routing.strategy

        latency_ms = self._latency_ms(execution_started)
        if self.usage_tracker is not None:
            await self.usage_tracker.settle(
                request_id,
                client_id,
                provider=provider.name,
                model=result.model,
                input_tokens=result.prompt_tokens,
                output_tokens=result.completion_tokens,
                actual_cost_usd=result.cost_usd,
                latency_ms=latency_ms,
            )
        if self.metrics is not None:
            self.metrics.record_provider_usage(
                provider.name,
                result.model,
                result.prompt_tokens,
                result.completion_tokens,
                float(result.cost_usd),
            )
        self._record_gateway_request("success", latency_ms / 1000)

        response = ChatCompletionResponse(
            id=f"chatcmpl_{uuid4().hex}",
            created=int(datetime.now(UTC).timestamp()),
            model=result.model,
            choices=[
                Choice(
                    index=0,
                    message=ResponseMessage(content=result.content),
                    finish_reason=result.finish_reason,
                )
            ],
            usage=TokenUsage(
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                total_tokens=total_tokens,
            ),
            gateway=GatewayMetadata(
                request_id=request_id,
                provider=provider.name,
                cost_usd=float(result.cost_usd),
                cached=False,
                routing_strategy=routing_strategy,
                task_complexity=routing_plan.task_complexity,
                estimated_cost_usd=float(execution.candidate.estimated_cost_usd),
                attempts=list(execution.attempts),
            ),
        )
        if self.response_cache is not None and not request.routing.disable_cache:
            await self.response_cache.set(client_id, request, response)
        return response

    async def _record_failed_usage(
        self,
        request_id: UUID,
        client_id: UUID,
        execution_started: float,
    ) -> int:
        latency_ms = self._latency_ms(execution_started)
        if self.usage_tracker is not None:
            await self.usage_tracker.fail(
                request_id,
                client_id,
                latency_ms=latency_ms,
            )
        return latency_ms

    def _record_gateway_request(self, outcome: str, duration_seconds: float) -> None:
        if self.metrics is not None:
            self.metrics.record_gateway_request(outcome, duration_seconds)

    def _record_cache_request(self, result: str) -> None:
        if self.metrics is not None:
            self.metrics.record_cache_request(result)

    def _latency_ms(self, started_at: float) -> int:
        return max(0, round((self.clock() - started_at) * 1000))

    @staticmethod
    def _cache_hit_response(
        cached_response: ChatCompletionResponse,
        request_id: UUID,
    ) -> ChatCompletionResponse:
        gateway = cached_response.gateway.model_copy(
            update={
                "request_id": request_id,
                "cost_usd": 0.0,
                "cached": True,
                "attempts": [],
            }
        )
        return cached_response.model_copy(
            update={
                "id": f"chatcmpl_{uuid4().hex}",
                "created": int(datetime.now(UTC).timestamp()),
                "gateway": gateway,
            }
        )
