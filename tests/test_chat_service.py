from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.core.errors import GatewayError
from app.db.models import ModelCapabilityScore, RoutingPolicyVersion
from app.providers.base import (
    ModelRoutingProfile,
    ProviderRateLimitError,
    ProviderRequestError,
    ProviderResult,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.routing.repository import (
    DatabaseRoutingEvidenceRepository,
    RoutingEvidenceSnapshot,
)
from app.schemas.chat import ChatCompletionRequest, ChatMessage, RoutingOptions
from app.services.chat import ChatCompletionService


class SuccessfulProvider:
    name = "successful"
    model = "successful-1"
    routing_profile = ModelRoutingProfile(
        input_cost_per_million_usd=Decimal("1"),
        output_cost_per_million_usd=Decimal("2"),
        estimated_latency_ms=100,
        quality_score=0.8,
    )

    async def complete(self, request: ChatCompletionRequest) -> ProviderResult:
        return ProviderResult(
            content=request.messages[-1].content,
            model=self.model,
            prompt_tokens=10,
            completion_tokens=5,
            finish_reason="stop",
            cost_usd=Decimal("0.25"),
        )


class FailingProvider:
    name = "failing"
    model = "failing-1"
    routing_profile = ModelRoutingProfile(
        input_cost_per_million_usd=Decimal("1"),
        output_cost_per_million_usd=Decimal("2"),
        estimated_latency_ms=100,
        quality_score=0.8,
    )

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def complete(self, request: ChatCompletionRequest) -> ProviderResult:
        raise self.error


class ShadowProvider:
    def __init__(self, name: str, *, static_quality: float) -> None:
        self.name = name
        self.model = f"{name}-1"
        self.calls = 0
        self.routing_profile = ModelRoutingProfile(
            input_cost_per_million_usd=Decimal("1"),
            output_cost_per_million_usd=Decimal("1"),
            estimated_latency_ms=100,
            quality_score=static_quality,
        )

    async def complete(self, request: ChatCompletionRequest) -> ProviderResult:
        self.calls += 1
        return ProviderResult(
            content=self.name,
            model=self.model,
            prompt_tokens=1,
            completion_tokens=1,
            finish_reason="stop",
        )


def make_request(
    *, model: str = "auto", allowed_providers: list[str] | None = None
) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model=model,
        messages=[ChatMessage(role="user", content="Hello")],
        routing=RoutingOptions(allowed_providers=allowed_providers),
    )


@pytest.mark.anyio
async def test_service_selects_explicit_model_and_reports_cost() -> None:
    provider = SuccessfulProvider()
    service = ChatCompletionService((provider,))

    result = await service.create(make_request(model=provider.model), uuid4(), uuid4())

    assert result.model == provider.model
    assert result.gateway.provider == provider.name
    assert result.gateway.cost_usd == 0.25
    assert result.gateway.routing_strategy == "explicit"
    assert result.gateway.task_complexity == "simple"
    assert result.gateway.estimated_cost_usd > 0


@pytest.mark.anyio
async def test_service_rejects_known_model_excluded_by_policy() -> None:
    provider = SuccessfulProvider()
    service = ChatCompletionService((provider,))

    with pytest.raises(GatewayError) as captured:
        await service.create(
            make_request(model=provider.model, allowed_providers=["another"]),
            uuid4(),
            uuid4(),
        )

    assert captured.value.status_code == 503
    assert captured.value.code == "no_provider_available"


@pytest.mark.anyio
async def test_intelligent_routing_uses_versioned_evidence_metadata() -> None:
    provider = SuccessfulProvider()
    policy = RoutingPolicyVersion(
        version="policy-v2",
        status="active",
        classifier_version="task-rules-v1",
        score_version="scores-v2",
        strategy_weights={},
        minimum_quality={},
        minimum_sample_count=5,
        cold_start_mode="exclude",
    )
    now = datetime.now(UTC)
    score = ModelCapabilityScore(
        evaluation_run_id=uuid4(),
        provider=provider.name,
        model=provider.model,
        score_version="scores-v2",
        task_category="general_chat",
        difficulty="simple",
        quality_score=Decimal("0.91"),
        sample_count=20,
        capabilities=["text_generation"],
        status="active",
        valid_from=now - timedelta(days=1),
        valid_until=now + timedelta(days=30),
    )
    repository = AsyncMock(spec=DatabaseRoutingEvidenceRepository)
    repository.load_active.return_value = RoutingEvidenceSnapshot(policy, (score,))
    service = ChatCompletionService(
        (provider,),
        intelligent_routing_enabled=True,
        evidence_repository=repository,
    )

    result = await service.create(make_request(), uuid4(), uuid4())

    assert result.gateway.routing_policy_version == "policy-v2"
    assert result.gateway.score_version == "scores-v2"
    assert result.gateway.evaluated_quality == 0.91
    assert result.gateway.routing_reasons == ["evaluated_quality"]


@pytest.mark.anyio
async def test_intelligent_routing_fails_safely_without_eligible_evidence() -> None:
    provider = SuccessfulProvider()
    policy = RoutingPolicyVersion(
        version="policy-v2",
        status="active",
        classifier_version="task-rules-v1",
        score_version="scores-v2",
        strategy_weights={},
        minimum_quality={},
        minimum_sample_count=5,
        cold_start_mode="exclude",
    )
    repository = AsyncMock(spec=DatabaseRoutingEvidenceRepository)
    repository.load_active.return_value = RoutingEvidenceSnapshot(policy, ())
    service = ChatCompletionService(
        (provider,),
        intelligent_routing_enabled=True,
        evidence_repository=repository,
    )

    with pytest.raises(GatewayError) as captured:
        await service.create(make_request(), uuid4(), uuid4())

    assert captured.value.code == "no_evidence_eligible_provider"


@pytest.mark.anyio
async def test_shadow_routing_never_changes_baseline_execution() -> None:
    baseline_winner = ShadowProvider("baseline", static_quality=0.95)
    evidence_winner = ShadowProvider("evidence", static_quality=0.40)
    policy = RoutingPolicyVersion(
        version="policy-v2",
        status="active",
        classifier_version="task-rules-v1",
        score_version="scores-v2",
        strategy_weights={"simple": {"quality": 1.0, "latency": 0, "cost": 0}},
        minimum_quality={},
        minimum_sample_count=5,
        cold_start_mode="exclude",
    )
    now = datetime.now(UTC)

    def evidence(provider: ShadowProvider, quality: str) -> ModelCapabilityScore:
        return ModelCapabilityScore(
            evaluation_run_id=uuid4(),
            provider=provider.name,
            model=provider.model,
            score_version="scores-v2",
            task_category="general_chat",
            difficulty="simple",
            quality_score=Decimal(quality),
            sample_count=20,
            capabilities=["text_generation"],
            status="active",
            valid_from=now - timedelta(days=1),
            valid_until=now + timedelta(days=30),
        )

    repository = AsyncMock(spec=DatabaseRoutingEvidenceRepository)
    repository.load_active.return_value = RoutingEvidenceSnapshot(
        policy,
        (evidence(baseline_winner, "0.50"), evidence(evidence_winner, "0.99")),
    )
    service = ChatCompletionService(
        (baseline_winner, evidence_winner),  # type: ignore[arg-type]
        intelligent_routing_shadow_enabled=True,
        evidence_repository=repository,
    )

    result = await service.create(make_request(), uuid4(), uuid4())

    assert result.gateway.provider == "baseline"
    assert result.gateway.routing_policy_version == "baseline-v1"
    assert baseline_winner.calls == 1
    assert evidence_winner.calls == 0
    repository.load_active.assert_awaited_once()


@pytest.mark.anyio
async def test_service_returns_fallback_attempt_metadata() -> None:
    primary = FailingProvider(ProviderUnavailableError("down"))
    fallback = SuccessfulProvider()
    service = ChatCompletionService((primary, fallback))

    result = await service.create(make_request(), uuid4(), uuid4())

    assert result.gateway.provider == fallback.name
    assert [attempt.model_dump() for attempt in result.gateway.attempts] == [
        {"provider": "failing", "model": "failing-1", "outcome": "unavailable"},
        {"provider": "successful", "model": "successful-1", "outcome": "success"},
    ]


@pytest.mark.anyio
async def test_service_reserves_and_settles_durable_usage() -> None:
    tracker = Mock()
    tracker.reserve = AsyncMock()
    tracker.settle = AsyncMock()
    tracker.fail = AsyncMock()
    tracker.record_cached = AsyncMock()
    metrics = Mock()
    clock_values = iter([10.0, 10.123])
    provider = SuccessfulProvider()
    service = ChatCompletionService(
        (provider,),
        usage_tracker=tracker,
        metrics=metrics,
        clock=lambda: next(clock_values),
    )
    request_id = uuid4()
    client_id = uuid4()

    await service.create(make_request(), request_id, client_id)

    tracker.reserve.assert_awaited_once()
    reserved_request_id, reserved_client_id, reserved_cost = tracker.reserve.await_args.args
    assert reserved_request_id == request_id
    assert reserved_client_id == client_id
    assert reserved_cost > 0
    tracker.settle.assert_awaited_once_with(
        request_id,
        client_id,
        provider="successful",
        model="successful-1",
        input_tokens=10,
        output_tokens=5,
        actual_cost_usd=Decimal("0.25"),
        latency_ms=123,
    )
    tracker.fail.assert_not_awaited()
    metrics.record_provider_attempt.assert_called_once_with("successful", "successful-1", "success")
    metrics.record_provider_usage.assert_called_once_with("successful", "successful-1", 10, 5, 0.25)
    metrics.record_gateway_request.assert_called_once_with("success", 0.123)


@pytest.mark.anyio
async def test_service_stops_before_provider_call_when_budget_is_exceeded() -> None:
    provider = SuccessfulProvider()
    provider.complete = AsyncMock()  # type: ignore[method-assign]
    tracker = Mock()
    tracker.reserve = AsyncMock(
        side_effect=GatewayError(
            "The monthly usage budget would be exceeded.",
            code="budget_exceeded",
            error_type="budget_error",
            status_code=402,
        )
    )
    tracker.settle = AsyncMock()
    tracker.fail = AsyncMock()
    tracker.record_cached = AsyncMock()
    metrics = Mock()
    service = ChatCompletionService((provider,), usage_tracker=tracker, metrics=metrics)

    with pytest.raises(GatewayError) as captured:
        await service.create(make_request(), uuid4(), uuid4())

    assert captured.value.status_code == 402
    provider.complete.assert_not_awaited()
    tracker.settle.assert_not_awaited()
    tracker.fail.assert_not_awaited()
    metrics.record_gateway_request.assert_called_once_with("budget_rejected", 0)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider_error", "status_code", "code"),
    [
        (ProviderRequestError("at least 16 tokens"), 400, "invalid_request"),
        (ProviderTimeoutError("timeout"), 504, "provider_timeout"),
        (ProviderRateLimitError("limited"), 502, "provider_error"),
        (ProviderUnavailableError("down"), 502, "provider_error"),
    ],
)
async def test_service_maps_provider_errors(
    provider_error: Exception, status_code: int, code: str
) -> None:
    service = ChatCompletionService((FailingProvider(provider_error),))

    with pytest.raises(GatewayError) as captured:
        await service.create(make_request(), uuid4(), uuid4())

    assert captured.value.status_code == status_code
    assert captured.value.code == code
