from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.db.models import ModelCapabilityScore, RoutingPolicyVersion
from app.providers.base import ModelRoutingProfile, ProviderResult
from app.routing.classifier import classify_request
from app.routing.intelligent import EvidenceDrivenRouter, OperationalModelStats
from app.schemas.chat import ChatCompletionRequest, ChatMessage, RoutingOptions

NOW = datetime(2026, 10, 28, tzinfo=UTC)


class Provider:
    def __init__(self, name: str, *, cost: str, latency: int, quality: float) -> None:
        self.name = name
        self.model = f"{name}-1"
        self.routing_profile = ModelRoutingProfile(
            input_cost_per_million_usd=Decimal(cost),
            output_cost_per_million_usd=Decimal(cost),
            estimated_latency_ms=latency,
            quality_score=quality,
        )

    async def complete(self, request: ChatCompletionRequest) -> ProviderResult:
        raise NotImplementedError


def request(
    *,
    strategy: str = "balanced",
    model: str = "auto",
    max_cost: float | None = None,
    allowed_providers: list[str] | None = None,
    tool_context: bool = False,
) -> ChatCompletionRequest:
    messages = [ChatMessage(role="user", content="Prove this theorem")]
    if tool_context:
        messages.append(ChatMessage(role="tool", content="tool result"))
    return ChatCompletionRequest(
        model=model,
        messages=messages,
        max_tokens=100,
        routing=RoutingOptions(
            strategy=strategy,  # type: ignore[arg-type]
            max_cost_usd=max_cost,
            allowed_providers=allowed_providers,
        ),
    )


def policy(*, cold_start: str = "exclude", quality_floor: float = 0.0) -> RoutingPolicyVersion:
    return RoutingPolicyVersion(
        version="policy-v2",
        status="active",
        classifier_version="task-rules-v1",
        score_version="scores-v2",
        strategy_weights={
            "complex": {"quality": 0.75, "latency": 0.10, "cost": 0.10, "reliability": 0.05}
        },
        minimum_quality={"reasoning": {"complex": quality_floor}},
        minimum_sample_count=5,
        cold_start_mode=cold_start,
    )


def score(provider: Provider, quality: str, **overrides: object) -> ModelCapabilityScore:
    values: dict[str, object] = {
        "id": uuid4(),
        "evaluation_run_id": uuid4(),
        "provider": provider.name,
        "model": provider.model,
        "score_version": "scores-v2",
        "task_category": "reasoning",
        "difficulty": "complex",
        "quality_score": Decimal(quality),
        "sample_count": 20,
        "score_variance": Decimal("0.01"),
        "capabilities": ["text_generation"],
        "status": "active",
        "valid_from": NOW - timedelta(days=1),
        "valid_until": NOW + timedelta(days=30),
    }
    values.update(overrides)
    return ModelCapabilityScore(**values)


def plan(
    providers: tuple[Provider, ...],
    request_value: ChatCompletionRequest,
    scores: tuple[ModelCapabilityScore, ...],
    *,
    policy_value: RoutingPolicyVersion | None = None,
    stats: dict[tuple[str, str], OperationalModelStats] | None = None,
):
    return EvidenceDrivenRouter(providers, now=NOW).plan(  # type: ignore[arg-type]
        request_value,
        classification=classify_request(request_value),
        policy=policy_value or policy(),
        scores=scores,
        operational_stats=stats,
    )


def test_quality_strategy_uses_task_specific_evidence() -> None:
    cheap = Provider("cheap", cost="1", latency=100, quality=0.9)
    strong = Provider("strong", cost="20", latency=1000, quality=0.5)

    result = plan(
        (cheap, strong),
        request(strategy="quality"),
        (score(cheap, "0.6"), score(strong, "0.95")),
    )

    assert result.candidates[0].provider is strong
    assert result.candidates[0].quality_score == 0.95
    assert result.candidates[0].reason_codes == ("evaluated_quality",)


def test_cost_and_latency_strategies_apply_quality_floor_first() -> None:
    cheap = Provider("cheap", cost="1", latency=800, quality=0.9)
    fast = Provider("fast", cost="10", latency=50, quality=0.9)
    qualified = Provider("qualified", cost="20", latency=500, quality=0.9)
    scores = (score(cheap, "0.4"), score(fast, "0.5"), score(qualified, "0.8"))
    policy_value = policy(quality_floor=0.7)

    cost_plan = plan(
        (cheap, fast, qualified),
        request(strategy="cost"),
        scores,
        policy_value=policy_value,
    )
    latency_plan = plan(
        (cheap, fast, qualified),
        request(strategy="latency"),
        scores,
        policy_value=policy_value,
    )

    assert [item.provider.name for item in cost_plan.candidates] == ["qualified"]
    assert [item.provider.name for item in latency_plan.candidates] == ["qualified"]


def test_hard_filters_remove_expired_over_budget_and_unavailable_candidates() -> None:
    expired = Provider("expired", cost="1", latency=100, quality=0.9)
    expensive = Provider("expensive", cost="100", latency=100, quality=0.9)
    down = Provider("down", cost="1", latency=100, quality=0.9)
    valid = Provider("valid", cost="1", latency=100, quality=0.9)
    scores = (
        score(expired, "0.9", valid_until=NOW),
        score(expensive, "0.9"),
        score(down, "0.9"),
        score(valid, "0.9"),
    )

    result = plan(
        (expired, expensive, down, valid),
        request(strategy="quality", max_cost=0.0002),
        scores,
        stats={(down.name, down.model): OperationalModelStats(available=False)},
    )

    assert [item.provider.name for item in result.candidates] == ["valid"]


def test_missing_evidence_follows_cold_start_policy() -> None:
    provider = Provider("new", cost="1", latency=100, quality=0.7)

    excluded = plan((provider,), request(), (), policy_value=policy(cold_start="exclude"))
    fallback = plan((provider,), request(), (), policy_value=policy(cold_start="static_profile"))

    assert excluded.candidates == ()
    assert fallback.candidates[0].score_version == "static-profile"
    assert fallback.candidates[0].reason_codes == ("static_profile_fallback",)


def test_explicit_model_preserves_selection_without_evidence() -> None:
    provider = Provider("chosen", cost="1", latency=100, quality=0.7)
    explicit_request = request(model=provider.model)

    result = plan(
        (provider,),
        explicit_request,
        (),
        policy_value=policy(cold_start="explicit_only", quality_floor=0.99),
    )

    assert result.candidates[0].provider is provider
    assert result.candidates[0].reason_codes == ("explicit_model_fallback",)


def test_balanced_ranking_is_deterministic_with_provider_tie_breaker() -> None:
    beta = Provider("beta", cost="1", latency=100, quality=0.8)
    alpha = Provider("alpha", cost="1", latency=100, quality=0.8)

    result = plan((beta, alpha), request(), (score(beta, "0.8"), score(alpha, "0.8")))

    assert [item.provider.name for item in result.candidates] == ["alpha", "beta"]


def test_provider_allowlist_is_a_hard_filter() -> None:
    allowed = Provider("allowed", cost="1", latency=100, quality=0.8)
    blocked = Provider("blocked", cost="1", latency=100, quality=0.9)

    result = plan(
        (allowed, blocked),
        request(allowed_providers=["allowed"]),
        (score(allowed, "0.8"), score(blocked, "0.9")),
    )

    assert [item.provider.name for item in result.candidates] == ["allowed"]


def test_required_tool_capability_filters_evidence() -> None:
    text_only = Provider("text", cost="1", latency=100, quality=0.9)
    tool_capable = Provider("tool", cost="1", latency=100, quality=0.8)

    result = plan(
        (text_only, tool_capable),
        request(tool_context=True),
        (
            score(text_only, "0.9"),
            score(tool_capable, "0.8", capabilities=["text_generation", "tool_use"]),
        ),
    )

    assert result.required_capabilities == frozenset({"text_generation", "tool_use"})
    assert [item.provider.name for item in result.candidates] == ["tool"]


def test_wrong_version_and_insufficient_samples_are_not_routed() -> None:
    old = Provider("old", cost="1", latency=100, quality=0.9)
    sparse = Provider("sparse", cost="1", latency=100, quality=0.9)

    result = plan(
        (old, sparse),
        request(),
        (
            score(old, "0.9", score_version="scores-v1"),
            score(sparse, "0.9", sample_count=4),
        ),
    )

    assert result.candidates == ()


def test_operational_stats_reject_invalid_values() -> None:
    with pytest.raises(ValueError, match="Reliability"):
        OperationalModelStats(reliability=1.1)
    with pytest.raises(ValueError, match="latency"):
        OperationalModelStats(observed_latency_ms=-1)
