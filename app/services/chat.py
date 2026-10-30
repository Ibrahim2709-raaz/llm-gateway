import time
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

import structlog
from fastapi import status

from app.cache.service import ResponseCache
from app.core.errors import GatewayError
from app.execution.circuit_breaker import ProviderCircuitBreaker
from app.execution.service import (
    ProviderExecutionExhausted,
    ProviderExecutor,
    Sleeper,
)
from app.observability.metrics import MetricsRecorder
from app.providers.base import ChatProvider, ProviderRequestError
from app.routing.classifier import classify_request
from app.routing.intelligent import EvidenceDrivenRouter
from app.routing.repository import (
    DatabaseRoutingEvidenceRepository,
    RoutingEvidenceError,
    RoutingEvidenceSnapshot,
)
from app.routing.rollout import IntelligentRoutingRollout
from app.routing.service import ProviderRouter, RoutingCandidate, RoutingPlan
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
        circuit_breaker: ProviderCircuitBreaker | None = None,
        intelligent_routing_enabled: bool = False,
        intelligent_routing_shadow_enabled: bool = False,
        intelligent_routing_rollout: IntelligentRoutingRollout | None = None,
        intelligent_routing_required_policy_version: str | None = None,
        intelligent_routing_required_score_version: str | None = None,
        evidence_repository: DatabaseRoutingEvidenceRepository | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.providers = providers
        self.router = ProviderRouter(providers)
        self.intelligent_routing_enabled = intelligent_routing_enabled
        self.intelligent_routing_shadow_enabled = intelligent_routing_shadow_enabled
        self.intelligent_routing_rollout = intelligent_routing_rollout
        self.intelligent_routing_required_policy_version = (
            intelligent_routing_required_policy_version
        )
        self.intelligent_routing_required_score_version = intelligent_routing_required_score_version
        self.evidence_repository = evidence_repository
        if intelligent_routing_enabled and intelligent_routing_shadow_enabled:
            raise ValueError("Live and shadow intelligent routing cannot both be enabled")
        self.executor = ProviderExecutor(
            max_retries=max_retries,
            retry_base_delay_seconds=retry_base_delay_seconds,
            retry_max_delay_seconds=retry_max_delay_seconds,
            sleeper=sleeper,
            metrics=metrics,
            circuit_breaker=circuit_breaker,
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
        live_routing_selected = self._live_routing_selected(client_id)
        if (
            self.response_cache is not None
            and not request.routing.disable_cache
            and not live_routing_selected
        ):
            cached_response = await self.response_cache.get(client_id, request)
            if cached_response is not None:
                self._record_cache_request("hit")
                if self.metrics is not None:
                    self.metrics.record_cache_savings(
                        cached_response.gateway.provider,
                        cached_response.model,
                        cached_response.gateway.cost_usd,
                    )
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
        elif request.routing.disable_cache or live_routing_selected:
            self._record_cache_request("bypass")

        routing_plan = await self._plan(request, client_id)
        if self.evidence_repository is not None:
            await self.evidence_repository.record_decision(
                request_id=request_id,
                client_id=client_id,
                plan=routing_plan,
                created_at=datetime.now(UTC),
            )
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
            task_category=routing_plan.classification.category.value,
            classification_confidence=routing_plan.classification.confidence,
            classifier_version=routing_plan.classification.classifier_version,
            routing_policy_version=routing_plan.policy_version,
            score_version=primary.score_version,
            routing_reasons=list(primary.reason_codes),
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
        if routing_plan.policy_version != "baseline-v1" and self.metrics is not None:
            self.metrics.record_intelligent_route(
                provider.name,
                result.model,
                routing_plan.classification.category.value,
                routing_plan.classification.difficulty.value,
                routing_strategy,
                routing_plan.policy_version,
                execution.candidate.score_version,
                float(execution.candidate.estimated_cost_usd),
                float(result.cost_usd),
                latency_ms / 1000,
            )

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
                task_category=routing_plan.classification.category.value,
                classification_confidence=routing_plan.classification.confidence,
                classifier_version=routing_plan.classification.classifier_version,
                classification_reasons=list(routing_plan.classification.reason_codes),
                routing_policy_version=routing_plan.policy_version,
                score_version=execution.candidate.score_version,
                routing_reasons=list(execution.candidate.reason_codes),
                evaluated_quality=execution.candidate.quality_score,
                estimated_cost_usd=float(execution.candidate.estimated_cost_usd),
                attempts=list(execution.attempts),
            ),
        )
        if (
            self.response_cache is not None
            and not request.routing.disable_cache
            and not live_routing_selected
        ):
            await self.response_cache.set(client_id, request, response)
        return response

    async def _plan(self, request: ChatCompletionRequest, client_id: UUID) -> RoutingPlan:
        if self.intelligent_routing_shadow_enabled:
            baseline = self.router.plan(request)
            await self._record_shadow_decision(request, baseline)
            return baseline
        if not self._live_routing_selected(client_id):
            return self.router.plan(request)
        if self.evidence_repository is None:
            raise RuntimeError("Intelligent routing requires an evidence repository")
        try:
            snapshot = await self.evidence_repository.load_active()
        except RoutingEvidenceError as exc:
            raise GatewayError(
                "Active routing evidence is unavailable.",
                code="routing_evidence_unavailable",
                error_type="service_unavailable_error",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from exc
        self._record_evidence_freshness(snapshot)
        if (
            self.intelligent_routing_required_policy_version is not None
            and snapshot.policy.version != self.intelligent_routing_required_policy_version
        ) or (
            self.intelligent_routing_required_score_version is not None
            and snapshot.policy.score_version
            != self.intelligent_routing_required_score_version
        ):
            raise GatewayError(
                "Active routing evidence does not match the versions pinned for this rollout.",
                code="routing_evidence_version_mismatch",
                error_type="service_unavailable_error",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        classification = classify_request(request)
        evidence_plan = EvidenceDrivenRouter(self.providers).plan(
            request,
            classification=classification,
            policy=snapshot.policy,
            scores=snapshot.scores,
        )
        if not evidence_plan.candidates:
            raise GatewayError(
                "No provider has eligible routing evidence for this request.",
                code="no_evidence_eligible_provider",
                error_type="service_unavailable_error",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return RoutingPlan(
            candidates=tuple(
                RoutingCandidate(
                    provider=candidate.provider,
                    estimated_cost_usd=candidate.estimated_cost_usd,
                    quality_score=candidate.quality_score,
                    score_version=candidate.score_version,
                    reason_codes=candidate.reason_codes,
                    ranking_score=candidate.ranking_score,
                )
                for candidate in evidence_plan.candidates
            ),
            task_complexity=classification.difficulty.value,
            classification=classification,
            policy_version=evidence_plan.policy_version,
        )

    def _live_routing_selected(self, client_id: UUID) -> bool:
        return self.intelligent_routing_enabled and (
            self.intelligent_routing_rollout is None
            or self.intelligent_routing_rollout.includes(client_id)
        )

    async def _record_shadow_decision(
        self, request: ChatCompletionRequest, baseline: RoutingPlan
    ) -> None:
        if self.evidence_repository is None:
            logger.warning("intelligent_shadow_skipped", reason="evidence_repository_missing")
            return
        try:
            snapshot = await self.evidence_repository.load_active()
            self._record_evidence_freshness(snapshot)
            classification = classify_request(request)
            shadow = EvidenceDrivenRouter(self.providers).plan(
                request,
                classification=classification,
                policy=snapshot.policy,
                scores=snapshot.scores,
            )
            baseline_primary = baseline.candidates[0]
            shadow_primary = shadow.candidates[0] if shadow.candidates else None
            logger.info(
                "intelligent_shadow_decision",
                baseline_provider=baseline_primary.provider.name,
                baseline_model=baseline_primary.provider.model,
                shadow_provider=(shadow_primary.provider.name if shadow_primary else None),
                shadow_model=(shadow_primary.provider.model if shadow_primary else None),
                agreed=(
                    shadow_primary is not None
                    and shadow_primary.provider.name == baseline_primary.provider.name
                    and shadow_primary.provider.model == baseline_primary.provider.model
                ),
                task_category=classification.category.value,
                task_difficulty=classification.difficulty.value,
                policy_version=snapshot.policy.version,
                score_version=snapshot.policy.score_version,
                candidate_count=len(shadow.candidates),
            )
            if self.metrics is not None:
                shadow_result = (
                    "no_candidate"
                    if shadow_primary is None
                    else (
                        "agreement"
                        if shadow_primary.provider.name == baseline_primary.provider.name
                        and shadow_primary.provider.model == baseline_primary.provider.model
                        else "disagreement"
                    )
                )
                self.metrics.record_shadow_decision(
                    shadow_result,
                    classification.category.value,
                    classification.difficulty.value,
                    classification.confidence,
                    snapshot.policy.version,
                    snapshot.policy.score_version,
                )
        except Exception as exc:
            logger.warning(
                "intelligent_shadow_skipped",
                reason=type(exc).__name__,
            )
            if self.metrics is not None:
                self.metrics.record_shadow_decision(
                    "skipped",
                    "unknown",
                    "unknown",
                    0,
                    "unknown",
                    "unknown",
                )

    def _record_evidence_freshness(self, snapshot: RoutingEvidenceSnapshot) -> None:
        if self.metrics is None:
            return
        expiries = [score.valid_until for score in snapshot.scores if score.valid_until]
        if not expiries:
            return
        self.metrics.record_routing_evidence(
            snapshot.policy.version,
            snapshot.policy.score_version,
            min(expiries).timestamp(),
        )

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
