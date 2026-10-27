from dataclasses import dataclass
from decimal import Decimal

from fastapi import status

from app.core.errors import GatewayError
from app.providers.base import ChatProvider, ModelRoutingProfile, TaskComplexity
from app.routing.classifier import TaskClassification, classify_request, estimate_prompt_tokens
from app.schemas.chat import ChatCompletionRequest

COMPLEXITY_LEVEL = {"simple": 1, "medium": 2, "complex": 3}


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    provider: ChatProvider
    task_complexity: TaskComplexity
    estimated_cost_usd: Decimal


@dataclass(frozen=True, slots=True)
class RoutingCandidate:
    provider: ChatProvider
    estimated_cost_usd: Decimal


@dataclass(frozen=True, slots=True)
class RoutingPlan:
    candidates: tuple[RoutingCandidate, ...]
    task_complexity: TaskComplexity
    classification: TaskClassification


def classify_task(request: ChatCompletionRequest) -> TaskComplexity:
    return classify_request(request).difficulty.value


def estimate_max_cost(request: ChatCompletionRequest, profile: ModelRoutingProfile) -> Decimal:
    input_cost = Decimal(estimate_prompt_tokens(request)) * profile.input_cost_per_million_usd
    output_cost = Decimal(request.max_tokens) * profile.output_cost_per_million_usd
    return (input_cost + output_cost) / Decimal(1_000_000)


class ProviderRouter:
    def __init__(self, providers: tuple[ChatProvider, ...]) -> None:
        self.providers = providers

    def route(self, request: ChatCompletionRequest) -> RoutingDecision:
        plan = self.plan(request)
        selected = plan.candidates[0]
        return RoutingDecision(
            provider=selected.provider,
            task_complexity=plan.task_complexity,
            estimated_cost_usd=selected.estimated_cost_usd,
        )

    def plan(self, request: ChatCompletionRequest) -> RoutingPlan:
        classification = classify_request(request)
        task_complexity: TaskComplexity = classification.difficulty.value
        known_model = any(provider.model == request.model for provider in self.providers)
        if request.model != "auto" and not known_model:
            raise GatewayError(
                f"Model '{request.model}' was not found.",
                code="model_not_found",
                error_type="invalid_request_error",
                status_code=status.HTTP_404_NOT_FOUND,
                param="model",
            )

        candidates = self._eligible_candidates(request, task_complexity)
        if not candidates:
            raise GatewayError(
                "No provider satisfies the request policy.",
                code="no_provider_available",
                error_type="service_unavailable_error",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                param=self._rejection_param(request),
            )

        if request.model != "auto":
            ranked_candidates = candidates
        else:
            auto_candidates = [
                candidate
                for candidate in candidates
                if candidate.provider.routing_profile.auto_selectable
            ]
            ranked_candidates = self._rank(
                auto_candidates or candidates, request.routing.strategy, task_complexity
            )
        return RoutingPlan(
            candidates=tuple(ranked_candidates),
            task_complexity=task_complexity,
            classification=classification,
        )

    def _eligible_candidates(
        self,
        request: ChatCompletionRequest,
        task_complexity: TaskComplexity,
    ) -> list[RoutingCandidate]:
        candidates: list[RoutingCandidate] = []
        for provider in self.providers:
            if not self._matches_non_cost_policy(provider, request, task_complexity):
                continue
            profile = provider.routing_profile
            estimated_cost = estimate_max_cost(request, profile)
            if request.routing.max_cost_usd is not None and estimated_cost > Decimal(
                str(request.routing.max_cost_usd)
            ):
                continue
            candidates.append(
                RoutingCandidate(provider=provider, estimated_cost_usd=estimated_cost)
            )
        return candidates

    @staticmethod
    def _matches_non_cost_policy(
        provider: ChatProvider,
        request: ChatCompletionRequest,
        task_complexity: TaskComplexity,
    ) -> bool:
        if (
            request.routing.allowed_providers is not None
            and provider.name not in request.routing.allowed_providers
        ):
            return False
        if request.model != "auto" and provider.model != request.model:
            return False
        return not (
            request.model == "auto"
            and COMPLEXITY_LEVEL[provider.routing_profile.max_complexity]
            < COMPLEXITY_LEVEL[task_complexity]
        )

    @staticmethod
    def _rank(
        candidates: list[RoutingCandidate],
        strategy: str,
        task_complexity: TaskComplexity,
    ) -> list[RoutingCandidate]:
        if strategy == "cost":
            return sorted(
                candidates,
                key=lambda candidate: (
                    candidate.estimated_cost_usd,
                    -candidate.provider.routing_profile.quality_score,
                ),
            )
        if strategy == "latency":
            return sorted(
                candidates,
                key=lambda candidate: (
                    candidate.provider.routing_profile.estimated_latency_ms,
                    -candidate.provider.routing_profile.quality_score,
                ),
            )
        if strategy == "quality":
            return sorted(
                candidates,
                key=lambda candidate: (
                    -candidate.provider.routing_profile.quality_score,
                    candidate.provider.routing_profile.estimated_latency_ms,
                ),
            )
        return sorted(
            candidates,
            key=lambda candidate: -ProviderRouter._balanced_score(candidate, task_complexity),
        )

    @staticmethod
    def _balanced_score(candidate: RoutingCandidate, task_complexity: TaskComplexity) -> float:
        profile = candidate.provider.routing_profile
        weights = {
            "simple": (0.30, 0.35, 0.35),
            "medium": (0.50, 0.25, 0.25),
            "complex": (0.75, 0.15, 0.10),
        }
        quality_weight, latency_weight, cost_weight = weights[task_complexity]
        latency_utility = 1 / (1 + profile.estimated_latency_ms / 1000)
        cost_utility = 1 / (1 + float(candidate.estimated_cost_usd) * 1000)
        return (
            quality_weight * profile.quality_score
            + latency_weight * latency_utility
            + cost_weight * cost_utility
        )

    def _rejection_param(self, request: ChatCompletionRequest) -> str:
        task_complexity = classify_task(request)
        if request.routing.max_cost_usd is not None and any(
            self._matches_non_cost_policy(provider, request, task_complexity)
            for provider in self.providers
        ):
            return "routing.max_cost_usd"
        if request.model != "auto":
            if request.routing.allowed_providers is not None and any(
                provider.model == request.model for provider in self.providers
            ):
                return "routing.allowed_providers"
            return "model"
        return "routing.allowed_providers"
