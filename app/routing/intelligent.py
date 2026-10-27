from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from app.db.models import ModelCapabilityScore, RoutingPolicyVersion
from app.providers.base import ChatProvider
from app.routing.classifier import TaskClassification, estimate_prompt_tokens
from app.routing.evidence import evaluate_cold_start, evaluate_score_evidence
from app.schemas.chat import ChatCompletionRequest

RoutingStrategy = Literal["balanced", "cost", "latency", "quality"]


@dataclass(frozen=True, slots=True)
class OperationalModelStats:
    available: bool = True
    observed_latency_ms: int | None = None
    reliability: float = 1.0

    def __post_init__(self) -> None:
        if self.observed_latency_ms is not None and self.observed_latency_ms < 0:
            raise ValueError("Observed latency must not be negative")
        if not 0 <= self.reliability <= 1:
            raise ValueError("Reliability must be between zero and one")


@dataclass(frozen=True, slots=True)
class EvidenceRoutingCandidate:
    provider: ChatProvider
    estimated_cost_usd: Decimal
    quality_score: float
    latency_ms: int
    reliability: float
    score_version: str
    reason_codes: tuple[str, ...]
    ranking_score: float


@dataclass(frozen=True, slots=True)
class EvidenceRoutingPlan:
    candidates: tuple[EvidenceRoutingCandidate, ...]
    policy_version: str
    score_version: str
    classification: TaskClassification
    required_capabilities: frozenset[str]


def estimate_candidate_cost(
    request: ChatCompletionRequest, provider: ChatProvider
) -> Decimal:
    profile = provider.routing_profile
    input_cost = Decimal(estimate_prompt_tokens(request)) * profile.input_cost_per_million_usd
    output_cost = Decimal(request.max_tokens) * profile.output_cost_per_million_usd
    return (input_cost + output_cost) / Decimal(1_000_000)


class EvidenceDrivenRouter:
    def __init__(
        self,
        providers: tuple[ChatProvider, ...],
        *,
        now: datetime | None = None,
    ) -> None:
        self.providers = providers
        self.now = now or datetime.now(UTC)

    def plan(
        self,
        request: ChatCompletionRequest,
        *,
        classification: TaskClassification,
        policy: RoutingPolicyVersion,
        scores: tuple[ModelCapabilityScore, ...],
        operational_stats: dict[tuple[str, str], OperationalModelStats] | None = None,
    ) -> EvidenceRoutingPlan:
        stats = operational_stats or {}
        required_capabilities = self._required_capabilities(request)
        explicit = request.model != "auto"
        candidates: list[EvidenceRoutingCandidate] = []
        for provider in self.providers:
            if explicit and provider.model != request.model:
                continue
            if (
                request.routing.allowed_providers is not None
                and provider.name not in request.routing.allowed_providers
            ):
                continue
            live = stats.get((provider.name, provider.model), OperationalModelStats())
            if not live.available:
                continue
            estimated_cost = estimate_candidate_cost(request, provider)
            if request.routing.max_cost_usd is not None and estimated_cost > Decimal(
                str(request.routing.max_cost_usd)
            ):
                continue

            score = self._matching_score(provider, classification, scores)
            reason_codes: list[str] = []
            if score is not None:
                eligibility = evaluate_score_evidence(
                    score,
                    policy,
                    required_capabilities=required_capabilities,
                    now=self.now,
                )
            else:
                eligibility = None
            if score is not None and eligibility is not None and eligibility.eligible:
                quality = float(score.quality_score)
                score_version = score.score_version
                reason_codes.append("evaluated_quality")
            else:
                cold_start = evaluate_cold_start(policy, explicit_model=explicit)
                if not cold_start.eligible:
                    continue
                quality = provider.routing_profile.quality_score
                score_version = "static-profile"
                reason_codes.append(cold_start.reason)

            quality_floor = self._quality_floor(policy, classification)
            if not explicit and quality < quality_floor:
                continue
            latency_ms = (
                live.observed_latency_ms
                if live.observed_latency_ms is not None
                else provider.routing_profile.estimated_latency_ms
            )
            ranking_score = self._ranking_score(
                strategy=request.routing.strategy,
                quality=quality,
                cost=estimated_cost,
                latency_ms=latency_ms,
                reliability=live.reliability,
                policy=policy,
                classification=classification,
            )
            candidates.append(
                EvidenceRoutingCandidate(
                    provider=provider,
                    estimated_cost_usd=estimated_cost,
                    quality_score=quality,
                    latency_ms=latency_ms,
                    reliability=live.reliability,
                    score_version=score_version,
                    reason_codes=tuple(reason_codes),
                    ranking_score=ranking_score,
                )
            )
        ranked = sorted(
            candidates,
            key=lambda candidate: (
                -candidate.ranking_score,
                candidate.provider.name,
                candidate.provider.model,
            ),
        )
        return EvidenceRoutingPlan(
            candidates=tuple(ranked),
            policy_version=policy.version,
            score_version=policy.score_version,
            classification=classification,
            required_capabilities=required_capabilities,
        )

    @staticmethod
    def _matching_score(
        provider: ChatProvider,
        classification: TaskClassification,
        scores: tuple[ModelCapabilityScore, ...],
    ) -> ModelCapabilityScore | None:
        matches = [
            score
            for score in scores
            if score.provider == provider.name
            and score.model == provider.model
            and score.task_category == classification.category.value
            and score.difficulty == classification.difficulty.value
        ]
        if len(matches) > 1:
            raise ValueError(
                f"Multiple capability scores match {provider.name}/{provider.model}"
            )
        return matches[0] if matches else None

    @staticmethod
    def _required_capabilities(request: ChatCompletionRequest) -> frozenset[str]:
        capabilities = {"text_generation"}
        if any(message.role == "tool" for message in request.messages):
            capabilities.add("tool_use")
        return frozenset(capabilities)

    @staticmethod
    def _quality_floor(
        policy: RoutingPolicyVersion, classification: TaskClassification
    ) -> float:
        category_floor = policy.minimum_quality.get(classification.category.value, 0)
        if isinstance(category_floor, dict):
            value = category_floor.get(classification.difficulty.value, 0)
        else:
            value = category_floor
        return float(value) if isinstance(value, (int, float)) else 0.0

    @staticmethod
    def _ranking_score(
        *,
        strategy: RoutingStrategy,
        quality: float,
        cost: Decimal,
        latency_ms: int,
        reliability: float,
        policy: RoutingPolicyVersion,
        classification: TaskClassification,
    ) -> float:
        cost_utility = 1 / (1 + float(cost) * 1000)
        latency_utility = 1 / (1 + latency_ms / 1000)
        if strategy == "quality":
            return quality
        if strategy == "cost":
            return cost_utility
        if strategy == "latency":
            return latency_utility
        defaults = {
            "quality": 0.5,
            "latency": 0.2,
            "cost": 0.2,
            "reliability": 0.1,
        }
        configured = policy.strategy_weights.get(classification.difficulty.value, {})
        weights = {
            key: float(configured.get(key, value))
            for key, value in defaults.items()
        }
        if any(value < 0 for value in weights.values()):
            raise ValueError("Routing policy strategy weights must not be negative")
        total = sum(weights.values())
        if total <= 0:
            raise ValueError("Routing policy strategy weights must have a positive sum")
        return (
            weights["quality"] * quality
            + weights["latency"] * latency_utility
            + weights["cost"] * cost_utility
            + weights["reliability"] * reliability
        ) / total
