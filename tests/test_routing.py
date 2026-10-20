from decimal import Decimal

import pytest

from app.core.errors import GatewayError
from app.providers.base import ModelRoutingProfile, ProviderResult
from app.providers.mock import MockProvider
from app.routing.service import ProviderRouter, classify_task, estimate_max_cost
from app.schemas.chat import ChatCompletionRequest, ChatMessage, RoutingOptions


class ProfiledProvider:
    def __init__(
        self,
        name: str,
        *,
        input_cost: str,
        output_cost: str,
        latency_ms: int,
        quality: float,
        max_complexity: str = "complex",
        auto_selectable: bool = True,
    ) -> None:
        self.name = name
        self.model = f"{name}-1"
        self.routing_profile = ModelRoutingProfile(
            input_cost_per_million_usd=Decimal(input_cost),
            output_cost_per_million_usd=Decimal(output_cost),
            estimated_latency_ms=latency_ms,
            quality_score=quality,
            max_complexity=max_complexity,  # type: ignore[arg-type]
            auto_selectable=auto_selectable,
        )

    async def complete(self, request: ChatCompletionRequest) -> ProviderResult:
        return ProviderResult(
            content=request.messages[-1].content,
            model=self.model,
            prompt_tokens=1,
            completion_tokens=1,
            finish_reason="stop",
        )


def make_providers() -> tuple[ProfiledProvider, ProfiledProvider, ProfiledProvider]:
    return (
        ProfiledProvider(
            "cheap",
            input_cost="1",
            output_cost="1",
            latency_ms=900,
            quality=0.3,
            max_complexity="simple",
        ),
        ProfiledProvider(
            "fast",
            input_cost="10",
            output_cost="10",
            latency_ms=100,
            quality=0.6,
        ),
        ProfiledProvider(
            "quality",
            input_cost="20",
            output_cost="20",
            latency_ms=1500,
            quality=0.95,
        ),
    )


def make_request(
    *,
    strategy: str = "balanced",
    content: str = "Hello",
    model: str = "auto",
    allowed_providers: list[str] | None = None,
    max_cost_usd: float | None = None,
) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model=model,
        messages=[ChatMessage(role="user", content=content)],
        max_tokens=100,
        routing=RoutingOptions(
            strategy=strategy,  # type: ignore[arg-type]
            allowed_providers=allowed_providers,
            max_cost_usd=max_cost_usd,
        ),
    )


@pytest.mark.parametrize(
    ("strategy", "expected_provider"),
    [("cost", "cheap"), ("latency", "fast"), ("quality", "quality")],
)
def test_router_honors_requested_strategy(strategy: str, expected_provider: str) -> None:
    router = ProviderRouter(make_providers())  # type: ignore[arg-type]

    decision = router.route(make_request(strategy=strategy))

    assert decision.provider.name == expected_provider
    assert decision.task_complexity == "simple"


def test_routing_plan_preserves_ranked_fallback_order() -> None:
    router = ProviderRouter(make_providers())  # type: ignore[arg-type]

    routing_plan = router.plan(make_request(strategy="latency"))

    assert [candidate.provider.name for candidate in routing_plan.candidates] == [
        "fast",
        "cheap",
        "quality",
    ]


def test_balanced_routing_prioritizes_quality_for_complex_tasks() -> None:
    router = ProviderRouter(make_providers())  # type: ignore[arg-type]

    decision = router.route(make_request(content="Design a distributed architecture step-by-step"))

    assert decision.task_complexity == "complex"
    assert decision.provider.name == "quality"


def test_complexity_capability_removes_ineligible_models() -> None:
    cheap, fast, quality = make_providers()
    router = ProviderRouter((cheap, fast, quality))  # type: ignore[arg-type]

    decision = router.route(
        make_request(strategy="cost", content="Debug this algorithm step-by-step")
    )

    assert decision.task_complexity == "complex"
    assert decision.provider.name == "fast"


def test_cost_limit_removes_expensive_candidates() -> None:
    _, fast, quality = make_providers()
    router = ProviderRouter((fast, quality))  # type: ignore[arg-type]

    decision = router.route(make_request(strategy="quality", max_cost_usd=0.0015))

    assert decision.provider.name == "fast"
    assert decision.estimated_cost_usd <= Decimal("0.0015")


def test_router_rejects_when_cost_limit_removes_every_candidate() -> None:
    router = ProviderRouter(make_providers())  # type: ignore[arg-type]

    with pytest.raises(GatewayError) as error:
        router.route(make_request(max_cost_usd=0))

    assert error.value.code == "no_provider_available"
    assert error.value.param == "routing.max_cost_usd"


def test_router_honors_provider_allowlist() -> None:
    router = ProviderRouter(make_providers())  # type: ignore[arg-type]

    decision = router.route(make_request(strategy="quality", allowed_providers=["cheap"]))

    assert decision.provider.name == "cheap"


def test_explicit_model_bypasses_automatic_ranking() -> None:
    cheap, fast, quality = make_providers()
    router = ProviderRouter((cheap, fast, quality))  # type: ignore[arg-type]

    decision = router.route(make_request(model=cheap.model, strategy="quality"))

    assert decision.provider is cheap


def test_unknown_explicit_model_returns_not_found() -> None:
    router = ProviderRouter(make_providers())  # type: ignore[arg-type]

    with pytest.raises(GatewayError) as error:
        router.route(make_request(model="missing-1"))

    assert error.value.status_code == 404
    assert error.value.code == "model_not_found"


def test_non_selectable_mock_is_ignored_when_a_real_provider_exists() -> None:
    real = ProfiledProvider(
        "real",
        input_cost="10",
        output_cost="10",
        latency_ms=500,
        quality=0.8,
    )
    router = ProviderRouter((MockProvider(), real))

    decision = router.route(make_request(strategy="cost"))

    assert decision.provider is real


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("Hello", "simple"),
        ("x" * 800, "medium"),
        ("Please debug this algorithm", "complex"),
    ],
)
def test_task_classifier(content: str, expected: str) -> None:
    assert classify_task(make_request(content=content)) == expected


def test_cost_estimate_uses_prompt_and_maximum_output_tokens() -> None:
    provider = make_providers()[0]
    request = make_request(content="12345678")

    estimate = estimate_max_cost(request, provider.routing_profile)

    assert estimate == Decimal("0.000104")
