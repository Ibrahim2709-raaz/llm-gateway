from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from app.db.models import ModelCapabilityScore, RoutingPolicyVersion
from app.providers.base import ModelRoutingProfile, ProviderResult
from app.routing.classifier import classify_request
from app.routing.intelligent import EvidenceDrivenRouter
from app.routing.replay import ReplayScenario, run_offline_replay
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


def request(*, strategy: str = "balanced", max_cost: float | None = None) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        messages=[ChatMessage(role="user", content="Prove this theorem")],
        max_tokens=100,
        routing=RoutingOptions(strategy=strategy, max_cost_usd=max_cost),  # type: ignore[arg-type]
    )


def policy(*, cold_start: str = "exclude") -> RoutingPolicyVersion:
    return RoutingPolicyVersion(
        version="policy-v2",
        status="active",
        classifier_version="task-rules-v1",
        score_version="scores-v2",
        strategy_weights={},
        minimum_quality={},
        minimum_sample_count=5,
        cold_start_mode=cold_start,
    )


def score(provider: Provider, quality: str) -> ModelCapabilityScore:
    return ModelCapabilityScore(
        evaluation_run_id=uuid4(),
        provider=provider.name,
        model=provider.model,
        score_version="scores-v2",
        task_category="reasoning",
        difficulty="complex",
        quality_score=Decimal(quality),
        sample_count=20,
        capabilities=["text_generation"],
        status="active",
        valid_from=NOW - timedelta(days=1),
        valid_until=NOW + timedelta(days=30),
    )


def test_offline_replay_passes_policy_safe_deterministic_matrix() -> None:
    cheap = Provider("cheap", cost="1", latency=300, quality=0.5)
    strong = Provider("strong", cost="5", latency=100, quality=0.9)
    request_value = request(strategy="quality", max_cost=0.001)
    scenario = ReplayScenario(
        name="quality-with-ceiling",
        request=request_value,
        classification=classify_request(request_value),
        policy=policy(),
        scores=(score(cheap, "0.6"), score(strong, "0.95")),
        operational_stats={},
        remaining_budget_usd=Decimal("0.001"),
    )

    report = run_offline_replay(
        EvidenceDrivenRouter((cheap, strong)),
        (scenario,),
    )

    assert report.passed is True
    assert report.scenario_count == 1
    assert report.violations == ()


def test_offline_replay_detects_budget_breach_independently() -> None:
    provider = Provider("model", cost="5", latency=100, quality=0.8)
    request_value = request()
    scenario = ReplayScenario(
        name="insufficient-budget",
        request=request_value,
        classification=classify_request(request_value),
        policy=policy(),
        scores=(score(provider, "0.8"),),
        operational_stats={},
        remaining_budget_usd=Decimal("0"),
    )

    report = run_offline_replay(EvidenceDrivenRouter((provider,)), (scenario,))

    assert report.passed is False
    assert [violation.code for violation in report.violations] == [
        "remaining_budget_breach"
    ]


def test_offline_replay_detects_disallowed_provider_in_tampered_plan() -> None:
    provider = Provider("blocked", cost="1", latency=100, quality=0.8)
    request_value = request()
    request_value = request_value.model_copy(
        update={
            "routing": RoutingOptions(allowed_providers=["allowed"]),
        }
    )
    scenario = ReplayScenario(
        name="allowlist",
        request=request_value,
        classification=classify_request(request_value),
        policy=policy(cold_start="static_profile"),
        scores=(),
        operational_stats={},
    )

    report = run_offline_replay(EvidenceDrivenRouter((provider,)), (scenario,))

    assert report.passed is True
    assert report.violations == ()
