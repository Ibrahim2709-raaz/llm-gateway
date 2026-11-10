from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.routes.portal_admin import (
    change_admin_score_activation,
    compare_admin_score_version,
    create_admin_evaluation_run,
    create_admin_pricing_proposal,
    execute_admin_evaluation_run,
    list_admin_evaluation_failures,
    list_admin_evaluation_runs,
    list_admin_models,
    list_admin_pricing_proposals,
    list_admin_score_versions,
    preview_admin_evaluation_run,
    publish_admin_evaluation_scores,
    review_admin_pricing_proposal,
)
from app.core.config import Settings
from app.core.errors import GatewayError
from app.db.models import EvaluationRun, ProviderPricingProposal
from app.evaluations.operations import CandidateScoreSummary, EvaluationRunPreflight
from app.evaluations.score_activation import (
    ScoreActivationResult,
    ScoreComparisonRow,
    ScoreVersionComparison,
)
from app.evaluations.service import EvaluationRunSummary
from app.identity.authorization import Principal
from app.platform.evaluation_console import (
    EvaluationFailureView,
    EvaluationRunView,
    ScoreVersionView,
)
from app.platform.model_catalog import build_model_catalog
from app.platform.pricing import PricingProposalError
from app.providers.mock import MockProvider
from app.schemas.portal_admin import (
    ChangeScoreActivationRequest,
    CreateEvaluationRunRequest,
    CreatePricingProposalRequest,
    EvaluationPreflightRequest,
    ExecuteEvaluationRunRequest,
    PublishEvaluationScoresRequest,
    ReviewPricingProposalRequest,
)

NOW = datetime(2026, 11, 9, 12, tzinfo=UTC)


def operator_identity() -> MagicMock:
    user_id = uuid4()
    identity = MagicMock()
    identity.authentication.user.id = user_id
    identity.principal = Principal(
        actor_type="user",
        actor_id=user_id,
        organization_id=uuid4(),
        organization_role="owner",
        platform_role="platform_operator",
    )
    return identity


def request_with_providers(*names: str) -> MagicMock:
    providers = tuple(SimpleNamespace(name=name) for name in names)
    request = MagicMock()
    request.app.state.resources.providers = providers
    return request


def request_with_provider_objects(*providers: object) -> MagicMock:
    request = MagicMock()
    request.app.state.resources.providers = tuple(providers)
    return request


def pricing_proposal(*, proposer_id: UUID | None = None) -> ProviderPricingProposal:
    return ProviderPricingProposal(
        id=uuid4(),
        provider="openai",
        model="model-a",
        input_cost_per_million=Decimal("1.25"),
        output_cost_per_million=Decimal("5.5"),
        source_url="https://provider.example/pricing",
        rationale="Official provider price changed.",
        status="pending",
        proposed_by_user_id=proposer_id or uuid4(),
        created_at=NOW,
        updated_at=NOW,
    )


def test_model_catalog_reports_configured_availability_and_no_secrets() -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        openai_enabled=True,
        openai_api_key="secret-not-for-the-catalog",
        openai_context_window_tokens=128_000,
    )

    models = build_model_catalog(settings, available_provider_names=frozenset({"openai", "mock"}))

    openai = next(model for model in models if model.provider == "openai")
    anthropic = next(model for model in models if model.provider == "anthropic")
    assert openai.enabled is True
    assert openai.context_window_tokens == 128_000
    assert anthropic.enabled is False
    assert "secret-not-for-the-catalog" not in repr(models)


@pytest.mark.anyio
async def test_platform_operator_can_list_model_catalog() -> None:
    settings = Settings(_env_file=None, app_env="test")

    response = await list_admin_models(
        request_with_providers("mock"), operator_identity(), settings
    )

    assert response.credentials_exposed is False
    assert [item.provider for item in response.data] == [
        "openai",
        "anthropic",
        "local",
        "mock",
    ]
    mock = response.data[-1]
    assert mock.availability == "available"
    assert mock.auto_selectable is False
    assert mock.capabilities == ["text_generation"]


@pytest.mark.anyio
async def test_organization_owner_without_platform_role_is_denied() -> None:
    identity = operator_identity()
    identity.principal = Principal(
        actor_type="user",
        actor_id=uuid4(),
        organization_id=uuid4(),
        organization_role="owner",
    )

    with pytest.raises(GatewayError) as error:
        await list_admin_models(
            request_with_providers("mock"),
            identity,
            Settings(_env_file=None, app_env="test"),
        )

    assert error.value.status_code == 403
    assert error.value.code == "platform_permission_denied"


def test_admin_model_catalog_is_in_openapi_contract(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert "/v1/portal/admin/models" in response.json()["paths"]
    assert "/v1/portal/admin/pricing-proposals" in response.json()["paths"]
    assert "/v1/portal/admin/evaluations/runs" in response.json()["paths"]
    assert "/v1/portal/admin/evaluations/scores" in response.json()["paths"]
    assert "/v1/portal/admin/evaluations/datasets" in response.json()["paths"]
    assert "/v1/portal/admin/evaluations/preflight" in response.json()["paths"]
    assert (
        "/v1/portal/admin/evaluations/runs/{run_id}/execute"
        in response.json()["paths"]
    )
    assert (
        "/v1/portal/admin/evaluations/scores/activation"
        in response.json()["paths"]
    )


@pytest.mark.anyio
async def test_operator_can_preflight_evaluation_without_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preview = EvaluationRunPreflight(
        dataset_name="evaluation-smoke",
        dataset_version="1.0.0",
        provider="mock",
        model="mock-1",
        case_count=3,
        estimated_input_tokens=20,
        maximum_output_tokens=384,
        estimated_max_cost_usd=Decimal("0"),
        paid_provider=False,
    )
    preflight = AsyncMock(return_value=preview)
    monkeypatch.setattr("app.api.routes.portal_admin.preview_evaluation_run", preflight)

    response = await preview_admin_evaluation_run(
        EvaluationPreflightRequest(
            dataset_name=preview.dataset_name,
            dataset_version=preview.dataset_version,
            provider="mock",
            model="mock-1",
        ),
        request_with_provider_objects(MockProvider()),
        operator_identity(),
        MagicMock(),
    )

    assert response.case_count == 3
    assert response.paid_provider is False
    preflight.assert_awaited_once()


@pytest.mark.anyio
async def test_evaluation_creation_rejects_stale_cost_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preview = EvaluationRunPreflight(
        dataset_name="evaluation-smoke",
        dataset_version="1.0.0",
        provider="mock",
        model="mock-1",
        case_count=3,
        estimated_input_tokens=20,
        maximum_output_tokens=384,
        estimated_max_cost_usd=Decimal("0.05"),
        paid_provider=True,
    )
    monkeypatch.setattr(
        "app.api.routes.portal_admin.preview_evaluation_run",
        AsyncMock(return_value=preview),
    )
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()

    with pytest.raises(GatewayError) as error:
        await create_admin_evaluation_run(
            CreateEvaluationRunRequest(
                dataset_name=preview.dataset_name,
                dataset_version=preview.dataset_version,
                provider="mock",
                model="mock-1",
                approved_max_cost_usd=Decimal("0.04"),
                confirmation="CREATE_EVALUATION",
            ),
            request_with_provider_objects(MockProvider()),
            operator_identity(),
            database,
        )

    assert error.value.code == "evaluation_cost_ceiling_too_low"
    database.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_evaluation_creation_pins_ceiling_and_audits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = operator_identity()
    preview = EvaluationRunPreflight(
        dataset_name="evaluation-smoke",
        dataset_version="1.0.0",
        provider="mock",
        model="mock-1",
        case_count=3,
        estimated_input_tokens=20,
        maximum_output_tokens=384,
        estimated_max_cost_usd=Decimal("0"),
        paid_provider=False,
    )
    run = EvaluationRun(
        id=uuid4(),
        dataset_id=uuid4(),
        provider="mock",
        model="mock-1",
        status="pending",
        runner_version="evaluation-runner-v1",
        configuration={
            "dataset_name": preview.dataset_name,
            "dataset_version": preview.dataset_version,
        },
        aggregate_metrics={},
    )
    monkeypatch.setattr(
        "app.api.routes.portal_admin.preview_evaluation_run",
        AsyncMock(return_value=preview),
    )
    create = AsyncMock(return_value=run)
    audit = AsyncMock()
    monkeypatch.setattr("app.api.routes.portal_admin.create_evaluation_run", create)
    monkeypatch.setattr("app.api.routes.portal_admin.append_audit_event", audit)
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()

    response = await create_admin_evaluation_run(
        CreateEvaluationRunRequest(
            dataset_name=preview.dataset_name,
            dataset_version=preview.dataset_version,
            provider="mock",
            model="mock-1",
            approved_max_cost_usd=Decimal("0"),
            confirmation="CREATE_EVALUATION",
        ),
        request_with_provider_objects(MockProvider()),
        identity,
        database,
    )

    assert response.status == "pending"
    assert run.configuration["approved_max_cost_usd"] == "0"
    assert run.configuration["created_by_user_id"] == str(identity.authentication.user.id)
    audit.assert_awaited_once()
    database.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_confirmed_evaluation_execution_is_audited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = EvaluationRun(
        id=uuid4(),
        dataset_id=uuid4(),
        provider="mock",
        model="mock-1",
        status="pending",
        runner_version="evaluation-runner-v1",
        configuration={
            "dataset_name": "evaluation-smoke",
            "dataset_version": "1.0.0",
            "approved_max_cost_usd": "0",
        },
        aggregate_metrics={},
    )
    preview = EvaluationRunPreflight(
        dataset_name="evaluation-smoke",
        dataset_version="1.0.0",
        provider="mock",
        model="mock-1",
        case_count=3,
        estimated_input_tokens=20,
        maximum_output_tokens=384,
        estimated_max_cost_usd=Decimal("0"),
        paid_provider=False,
    )
    summary = EvaluationRunSummary(
        run_id=run.id,
        status="completed",
        completed_cases=3,
        skipped_cases=0,
        failed_cases=0,
        average_score=1.0,
        total_cost_usd=Decimal("0"),
    )
    service = MagicMock()
    service.execute = AsyncMock(return_value=summary)
    monkeypatch.setattr(
        "app.api.routes.portal_admin.DatabaseEvaluationService",
        MagicMock(return_value=service),
    )
    monkeypatch.setattr(
        "app.api.routes.portal_admin.preview_evaluation_run",
        AsyncMock(return_value=preview),
    )
    audit = AsyncMock()
    monkeypatch.setattr("app.api.routes.portal_admin.append_audit_event", audit)
    database = MagicMock()
    database.get = AsyncMock(return_value=run)
    database.commit = AsyncMock()
    database.rollback = AsyncMock()

    response = await execute_admin_evaluation_run(
        run.id,
        ExecuteEvaluationRunRequest(confirmation="RUN_EVALUATION"),
        request_with_provider_objects(MockProvider()),
        operator_identity(),
        database,
    )

    assert response.status == "completed"
    service.execute.assert_awaited_once_with(run.id)
    assert audit.await_count == 2
    assert database.commit.await_count == 2


@pytest.mark.anyio
async def test_score_activation_is_attributed_and_audited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = operator_identity()
    result = ScoreActivationResult(
        provider="mock",
        model="mock-1",
        score_version="scores-v2",
        previous_score_version="scores-v1",
        action="activate",
        score_count=3,
        valid_until=NOW,
    )
    activate = AsyncMock(return_value=result)
    audit = AsyncMock()
    monkeypatch.setattr("app.api.routes.portal_admin.change_active_score_set", activate)
    monkeypatch.setattr("app.api.routes.portal_admin.append_audit_event", audit)
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()

    response = await change_admin_score_activation(
        ChangeScoreActivationRequest(
            provider="mock",
            model="mock-1",
            score_version="scores-v2",
            action="activate",
            reason="Approved evaluation evidence.",
            confirmation="CHANGE_SCORE_SET",
        ),
        identity,
        database,
    )

    assert response.previous_score_version == "scores-v1"
    assert activate.await_args.kwargs["actor"] == str(identity.authentication.user.id)
    audit.assert_awaited_once()
    database.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_operator_publishes_draft_scores_with_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary = CandidateScoreSummary(
        score_version="scores-v2", score_count=2, sample_count=20
    )
    publish = AsyncMock(return_value=summary)
    audit = AsyncMock()
    monkeypatch.setattr("app.api.routes.portal_admin.create_candidate_scores", publish)
    monkeypatch.setattr("app.api.routes.portal_admin.append_audit_event", audit)
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()
    run_id = uuid4()

    response = await publish_admin_evaluation_scores(
        run_id,
        PublishEvaluationScoresRequest(score_version="scores-v2"),
        operator_identity(),
        database,
    )

    assert response.score_count == 2
    publish.assert_awaited_once_with(
        database, run_id=run_id, score_version="scores-v2"
    )
    audit.assert_awaited_once()
    database.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_operator_compares_candidate_with_active_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    comparison = ScoreVersionComparison(
        provider="mock",
        model="mock-1",
        candidate_version="scores-v2",
        candidate_status="draft",
        active_version="scores-v1",
        rows=(
            ScoreComparisonRow(
                task_category="reasoning",
                difficulty="simple",
                candidate_quality=Decimal("0.9"),
                active_quality=Decimal("0.8"),
                quality_delta=Decimal("0.1"),
                candidate_samples=20,
                candidate_variance=Decimal("0.01"),
                capabilities=("text_generation",),
            ),
        ),
    )
    compare = AsyncMock(return_value=comparison)
    monkeypatch.setattr("app.api.routes.portal_admin.compare_score_version", compare)

    response = await compare_admin_score_version(
        "mock", "mock-1", "scores-v2", operator_identity(), MagicMock()
    )

    assert response.active_version == "scores-v1"
    assert response.rows[0].quality_delta == Decimal("0.1")


@pytest.mark.anyio
async def test_operator_can_inspect_evaluation_summaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = EvaluationRunView(
        id=uuid4(), dataset_name="smoke", dataset_version="1.0", provider="mock",
        model="mock-1", status="completed", runner_version="runner-v1", case_count=3,
        failed_case_count=1, average_score=0.5, total_cost_usd=Decimal("0"),
        started_at=NOW, completed_at=NOW,
    )
    score = ScoreVersionView(
        provider="mock", model="mock-1", score_version="scores-v1", status="draft",
        score_count=2, sample_count=3, weighted_quality_score=0.5,
        task_categories=("reasoning",), valid_until=None,
    )
    monkeypatch.setattr(
        "app.api.routes.portal_admin.list_evaluation_runs", AsyncMock(return_value=[run])
    )
    monkeypatch.setattr(
        "app.api.routes.portal_admin.list_score_versions", AsyncMock(return_value=[score])
    )

    runs = await list_admin_evaluation_runs(operator_identity(), MagicMock(), 100)
    scores = await list_admin_score_versions(operator_identity(), MagicMock())

    assert runs.data[0].failed_case_count == 1
    assert scores.data[0].sample_count == 3


@pytest.mark.anyio
async def test_failure_endpoint_never_exposes_prompts_or_outputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = EvaluationFailureView(
        result_id=uuid4(), case_external_id="case-1", task_category="coding",
        difficulty="simple", outcome="failed", score=Decimal("0"),
        error_code="provider_error", prompt_tokens=2, completion_tokens=0,
        cost_usd=Decimal("0"), latency_ms=10,
    )
    monkeypatch.setattr(
        "app.api.routes.portal_admin.list_evaluation_failures",
        AsyncMock(return_value=[failure]),
    )

    response = await list_admin_evaluation_failures(
        uuid4(), operator_identity(), MagicMock(), 100
    )

    serialized = response.model_dump()
    assert serialized["prompts_exposed"] is False
    assert serialized["outputs_exposed"] is False
    assert "prompt" not in serialized["data"][0]
    assert "actual_output" not in serialized["data"][0]


@pytest.mark.anyio
async def test_platform_operator_can_list_pricing_proposals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = pricing_proposal()
    listing = AsyncMock(return_value=[item])
    monkeypatch.setattr("app.api.routes.portal_admin.list_pricing_proposals", listing)

    response = await list_admin_pricing_proposals(operator_identity(), MagicMock(), 25)

    assert response.data[0].id == item.id
    assert response.data[0].status == "pending"
    listing.assert_awaited_once_with(ANY, limit=25)


@pytest.mark.anyio
async def test_create_pricing_proposal_is_audited_and_committed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = operator_identity()
    item = pricing_proposal(proposer_id=identity.authentication.user.id)
    create = AsyncMock(return_value=item)
    audit = AsyncMock()
    monkeypatch.setattr("app.api.routes.portal_admin.create_pricing_proposal", create)
    monkeypatch.setattr("app.api.routes.portal_admin.append_audit_event", audit)
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()
    payload = CreatePricingProposalRequest(
        provider="openai",
        model="model-a",
        input_cost_per_million_usd=Decimal("1.25"),
        output_cost_per_million_usd=Decimal("5.5"),
        source_url="https://provider.example/pricing",
        rationale="Official provider price changed.",
    )

    response = await create_admin_pricing_proposal(payload, identity, database)

    assert response.id == item.id
    create.assert_awaited_once()
    audit.assert_awaited_once()
    database.commit.assert_awaited_once()
    database.rollback.assert_not_awaited()


@pytest.mark.anyio
async def test_pricing_review_conflict_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    review = AsyncMock(
        side_effect=PricingProposalError(
            "independent_pricing_review_required", "A different operator is required"
        )
    )
    monkeypatch.setattr("app.api.routes.portal_admin.review_pricing_proposal", review)
    database = MagicMock()
    database.commit = AsyncMock()
    database.rollback = AsyncMock()

    with pytest.raises(GatewayError) as error:
        await review_admin_pricing_proposal(
            uuid4(),
            ReviewPricingProposalRequest(
                decision="approved", review_note="Checked source."
            ),
            operator_identity(),
            database,
        )

    assert error.value.status_code == 409
    database.rollback.assert_awaited_once()
