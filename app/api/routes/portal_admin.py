from dataclasses import asdict
from decimal import Decimal
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import GatewayError
from app.db.models import EvaluationRun, ProviderPricingProposal, RoutingPolicyVersion
from app.db.session import get_database_session
from app.evaluations.operations import (
    create_candidate_scores,
    create_evaluation_run,
    list_evaluation_datasets,
    preview_evaluation_run,
)
from app.evaluations.score_activation import (
    ScoreActivationError,
    change_active_score_set,
    compare_score_version,
)
from app.evaluations.service import DatabaseEvaluationService, EvaluationRunError
from app.identity.audit import append_audit_event
from app.identity.authorization import (
    PLATFORM_MANAGE,
    AuthorizationError,
    require_platform_permission,
)
from app.identity.dependencies import (
    BrowserIdentity,
    authenticate_portal_session,
    require_portal_csrf,
)
from app.platform.evaluation_console import (
    list_evaluation_failures,
    list_evaluation_runs,
    list_score_versions,
)
from app.platform.model_catalog import build_model_catalog
from app.platform.pricing import (
    PricingProposalError,
    create_pricing_proposal,
    list_pricing_proposals,
    review_pricing_proposal,
)
from app.platform.routing_operations import build_routing_operational_summary
from app.providers.base import ChatProvider
from app.routing.control_plane import (
    DatabaseRoutingControlResolver,
    RoutingControlError,
    change_routing_control,
    emergency_baseline,
    model_runtime,
    serialize_runtime,
)
from app.routing.policy_activation import PolicyActivationError, change_active_policy
from app.routing.rollout import RoutingRuntimeConfiguration
from app.schemas.portal_admin import (
    ChangeRoutingPolicyRequest,
    ChangeRoutingRolloutRequest,
    ChangeScoreActivationRequest,
    ClassifierDistributionResponse,
    CreateEvaluationRunRequest,
    CreatePricingProposalRequest,
    EmergencyBaselineRequest,
    EvaluationDatasetListResponse,
    EvaluationDatasetResponse,
    EvaluationExecutionResponse,
    EvaluationFailureListResponse,
    EvaluationFailureResponse,
    EvaluationPreflightRequest,
    EvaluationPreflightResponse,
    EvaluationRunListResponse,
    EvaluationRunResponse,
    ExecuteEvaluationRunRequest,
    ModelCatalogEntry,
    ModelCatalogResponse,
    PricingProposalListResponse,
    PricingProposalResponse,
    PublishEvaluationScoresRequest,
    PublishEvaluationScoresResponse,
    ReviewPricingProposalRequest,
    RoutingConcentrationResponse,
    RoutingOperationalSummaryResponse,
    RoutingPolicyActivationResponse,
    RoutingPolicyListResponse,
    RoutingPolicyResponse,
    RoutingRolloutResponse,
    ScoreActivationResponse,
    ScoreComparisonResponse,
    ScoreComparisonRowResponse,
    ScoreVersionListResponse,
    ScoreVersionResponse,
)

router = APIRouter(prefix="/v1/portal/admin", tags=["Portal administration"])


def _rollout_response(runtime: RoutingRuntimeConfiguration) -> RoutingRolloutResponse:
    data = serialize_runtime(runtime)
    return RoutingRolloutResponse(**data)


def _require_platform_operator(identity: BrowserIdentity) -> None:
    try:
        require_platform_permission(identity.principal, permission=PLATFORM_MANAGE)
    except AuthorizationError as exc:
        raise GatewayError(
            "Platform operator permission is required.",
            code=exc.code,
            error_type="authorization_error",
            status_code=status.HTTP_403_FORBIDDEN,
        ) from exc


def _proposal_response(proposal: ProviderPricingProposal) -> PricingProposalResponse:
    return PricingProposalResponse(
        id=proposal.id,
        provider=proposal.provider,
        model=proposal.model,
        input_cost_per_million_usd=proposal.input_cost_per_million,
        output_cost_per_million_usd=proposal.output_cost_per_million,
        source_url=proposal.source_url,
        rationale=proposal.rationale,
        status=proposal.status,
        proposed_by_user_id=proposal.proposed_by_user_id,
        reviewed_by_user_id=proposal.reviewed_by_user_id,
        review_note=proposal.review_note,
        created_at=proposal.created_at,
        reviewed_at=proposal.reviewed_at,
    )


def _find_provider(request: Request, *, provider: str, model: str) -> ChatProvider:
    match = next(
        (
            candidate
            for candidate in request.app.state.resources.providers
            if candidate.name == provider and candidate.model == model
        ),
        None,
    )
    if match is None:
        raise GatewayError(
            "The requested provider model is not enabled.",
            code="provider_not_available",
            status_code=status.HTTP_409_CONFLICT,
        )
    return cast(ChatProvider, match)


def _evaluation_error(error: ValueError | TypeError) -> GatewayError:
    message = str(error)
    not_found = "was not found" in message
    return GatewayError(
        message,
        code="evaluation_resource_not_found" if not_found else "evaluation_invalid",
        status_code=(status.HTTP_404_NOT_FOUND if not_found else status.HTTP_409_CONFLICT),
    )


@router.get("/models", response_model=ModelCatalogResponse, operation_id="listAdminModels")
async def list_admin_models(
    request: Request,
    identity: Annotated[BrowserIdentity, Depends(authenticate_portal_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ModelCatalogResponse:
    _require_platform_operator(identity)

    available = frozenset(provider.name for provider in request.app.state.resources.providers)
    models = build_model_catalog(settings, available_provider_names=available)
    return ModelCatalogResponse(
        data=[
            ModelCatalogEntry(
                provider=model.provider,
                model=model.model,
                availability="available" if model.enabled else "disabled",
                input_cost_per_million_usd=model.input_cost_per_million_usd,
                output_cost_per_million_usd=model.output_cost_per_million_usd,
                context_window_tokens=model.context_window_tokens,
                capabilities=["text_generation"],
                estimated_latency_ms=model.estimated_latency_ms,
                quality_score=model.quality_score,
                max_complexity=model.max_complexity,
                auto_selectable=model.auto_selectable,
            )
            for model in models
        ]
    )


@router.get(
    "/pricing-proposals",
    response_model=PricingProposalListResponse,
    operation_id="listAdminPricingProposals",
)
async def list_admin_pricing_proposals(
    identity: Annotated[BrowserIdentity, Depends(authenticate_portal_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> PricingProposalListResponse:
    _require_platform_operator(identity)
    proposals = await list_pricing_proposals(database, limit=limit)
    return PricingProposalListResponse(data=[_proposal_response(item) for item in proposals])


@router.post(
    "/pricing-proposals",
    response_model=PricingProposalResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createAdminPricingProposal",
)
async def create_admin_pricing_proposal(
    payload: CreatePricingProposalRequest,
    identity: Annotated[BrowserIdentity, Depends(require_portal_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> PricingProposalResponse:
    _require_platform_operator(identity)
    try:
        proposal = await create_pricing_proposal(
            database,
            provider=payload.provider,
            model=payload.model,
            input_cost_per_million=payload.input_cost_per_million_usd,
            output_cost_per_million=payload.output_cost_per_million_usd,
            source_url=str(payload.source_url),
            rationale=payload.rationale,
            proposed_by_user_id=identity.authentication.user.id,
        )
        await append_audit_event(
            database,
            principal=identity.principal,
            action="provider_pricing.proposed",
            resource_type="provider_pricing_proposal",
            resource_id=str(proposal.id),
            event_data={
                "provider": proposal.provider,
                "model": proposal.model,
                "input_cost_per_million_usd": str(proposal.input_cost_per_million),
                "output_cost_per_million_usd": str(proposal.output_cost_per_million),
                "source_url": proposal.source_url,
            },
        )
        await database.commit()
    except Exception:
        await database.rollback()
        raise
    return _proposal_response(proposal)


@router.get(
    "/routing/policies",
    response_model=RoutingPolicyListResponse,
    operation_id="listAdminRoutingPolicies",
)
async def list_admin_routing_policies(
    identity: Annotated[BrowserIdentity, Depends(authenticate_portal_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> RoutingPolicyListResponse:
    _require_platform_operator(identity)
    policies = (
        (
            await database.execute(
                select(RoutingPolicyVersion).order_by(
                    RoutingPolicyVersion.created_at.desc()
                )
            )
        )
        .scalars()
        .all()
    )
    return RoutingPolicyListResponse(
        data=[
            RoutingPolicyResponse(
                version=policy.version,
                status=policy.status,
                classifier_version=policy.classifier_version,
                score_version=policy.score_version,
                minimum_sample_count=policy.minimum_sample_count,
                cold_start_mode=policy.cold_start_mode,
                activated_at=policy.activated_at,
            )
            for policy in policies
        ]
    )


@router.post(
    "/routing/policies/activation",
    response_model=RoutingPolicyActivationResponse,
    operation_id="changeAdminRoutingPolicy",
)
async def change_admin_routing_policy(
    payload: ChangeRoutingPolicyRequest,
    identity: Annotated[BrowserIdentity, Depends(require_portal_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> RoutingPolicyActivationResponse:
    _require_platform_operator(identity)
    try:
        result = await change_active_policy(
            database,
            version=payload.version,
            action=payload.action,
            actor=str(identity.authentication.user.id),
            reason=payload.reason,
        )
        await append_audit_event(
            database,
            principal=identity.principal,
            action=f"routing_policy.{payload.action}",
            resource_type="routing_policy_version",
            resource_id=result.policy_version,
            event_data={
                "policy_version": result.policy_version,
                "previous_policy_version": result.previous_policy_version,
                "reason": payload.reason,
            },
        )
        await database.commit()
    except PolicyActivationError as exc:
        await database.rollback()
        raise GatewayError(str(exc), code=exc.code, status_code=409) from exc
    except Exception:
        await database.rollback()
        raise
    return RoutingPolicyActivationResponse(
        policy_version=result.policy_version,
        previous_policy_version=result.previous_policy_version,
        action=result.action,
        activated_at=result.activated_at,
    )


@router.get(
    "/routing/rollout",
    response_model=RoutingRolloutResponse,
    operation_id="getAdminRoutingRollout",
)
async def get_admin_routing_rollout(
    identity: Annotated[BrowserIdentity, Depends(authenticate_portal_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RoutingRolloutResponse:
    _require_platform_operator(identity)
    runtime = await DatabaseRoutingControlResolver(database, settings).load()
    return _rollout_response(runtime)


@router.get(
    "/routing/summary",
    response_model=RoutingOperationalSummaryResponse,
    operation_id="getAdminRoutingOperationalSummary",
)
async def get_admin_routing_operational_summary(
    identity: Annotated[BrowserIdentity, Depends(authenticate_portal_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    window_hours: Annotated[int, Query(ge=1, le=720)] = 24,
) -> RoutingOperationalSummaryResponse:
    _require_platform_operator(identity)
    runtime = await DatabaseRoutingControlResolver(database, settings).load()
    summary = await build_routing_operational_summary(
        database, runtime=runtime, window_hours=window_hours
    )
    return RoutingOperationalSummaryResponse(
        window_hours=summary.window_hours,
        request_count=summary.request_count,
        concentration=[
            RoutingConcentrationResponse(**asdict(item)) for item in summary.concentration
        ],
        classifier_distribution=[
            ClassifierDistributionResponse(**asdict(item))
            for item in summary.classifier_distribution
        ],
        current_cost_usd=summary.current_cost_usd,
        previous_cost_usd=summary.previous_cost_usd,
        cost_drift_percent=summary.cost_drift_percent,
        active_policy_version=summary.active_policy_version,
        active_score_version=summary.active_score_version,
        evidence_valid_until=summary.evidence_valid_until,
        evidence_expired_count=summary.evidence_expired_count,
        alert_state=summary.alert_state,
        alert_reasons=list(summary.alert_reasons),
        grafana_url=str(settings.grafana_public_url),
    )


@router.put(
    "/routing/rollout",
    response_model=RoutingRolloutResponse,
    operation_id="changeAdminRoutingRollout",
)
async def change_admin_routing_rollout(
    payload: ChangeRoutingRolloutRequest,
    identity: Annotated[BrowserIdentity, Depends(require_portal_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RoutingRolloutResponse:
    _require_platform_operator(identity)
    resolver = DatabaseRoutingControlResolver(database, settings)
    before = await resolver.load()
    try:
        result = await change_routing_control(
            database,
            mode=payload.mode,
            canary_percent=payload.canary_percent,
            customer_allowlist=frozenset(payload.customer_allowlist),
            required_policy_version=payload.required_policy_version,
            required_score_version=payload.required_score_version,
            actor=str(identity.authentication.user.id),
            reason=payload.reason,
        )
        after = model_runtime(result.configuration)
        await append_audit_event(
            database,
            principal=identity.principal,
            action="routing_rollout.updated",
            resource_type="routing_rollout_configuration",
            resource_id=str(result.configuration.id),
            event_data={
                "before": serialize_runtime(before),
                "after": serialize_runtime(after),
                "reason": payload.reason,
            },
        )
        await database.commit()
    except RoutingControlError as exc:
        await database.rollback()
        raise GatewayError(
            str(exc), code="routing_rollout_invalid", status_code=409
        ) from exc
    except Exception:
        await database.rollback()
        raise
    return _rollout_response(after)


@router.post(
    "/routing/rollout/emergency-baseline",
    response_model=RoutingRolloutResponse,
    operation_id="emergencyAdminRoutingBaseline",
)
async def emergency_admin_routing_baseline(
    payload: EmergencyBaselineRequest,
    identity: Annotated[BrowserIdentity, Depends(require_portal_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> RoutingRolloutResponse:
    _require_platform_operator(identity)
    try:
        result = await emergency_baseline(
            database,
            actor=str(identity.authentication.user.id),
            reason=payload.reason,
        )
        runtime = model_runtime(result.configuration)
        await append_audit_event(
            database,
            principal=identity.principal,
            action="routing_rollout.emergency_baseline",
            resource_type="routing_rollout_configuration",
            resource_id=str(result.configuration.id),
            event_data={
                "after": serialize_runtime(runtime),
                "reason": payload.reason,
            },
        )
        await database.commit()
    except RoutingControlError as exc:
        await database.rollback()
        raise GatewayError(
            str(exc), code="routing_rollout_invalid", status_code=409
        ) from exc
    except Exception:
        await database.rollback()
        raise
    return _rollout_response(runtime)


@router.get(
    "/evaluations/datasets",
    response_model=EvaluationDatasetListResponse,
    operation_id="listAdminEvaluationDatasets",
)
async def list_admin_evaluation_datasets(
    identity: Annotated[BrowserIdentity, Depends(authenticate_portal_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> EvaluationDatasetListResponse:
    _require_platform_operator(identity)
    datasets = await list_evaluation_datasets(database)
    return EvaluationDatasetListResponse(
        data=[EvaluationDatasetResponse(**asdict(dataset)) for dataset in datasets]
    )


@router.post(
    "/evaluations/preflight",
    response_model=EvaluationPreflightResponse,
    operation_id="previewAdminEvaluationRun",
)
async def preview_admin_evaluation_run(
    payload: EvaluationPreflightRequest,
    request: Request,
    identity: Annotated[BrowserIdentity, Depends(authenticate_portal_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> EvaluationPreflightResponse:
    _require_platform_operator(identity)
    provider = _find_provider(request, provider=payload.provider, model=payload.model)
    try:
        preview = await preview_evaluation_run(
            database,
            dataset_name=payload.dataset_name,
            dataset_version=payload.dataset_version,
            provider=provider,
        )
    except (ValueError, TypeError) as exc:
        raise _evaluation_error(exc) from exc
    return EvaluationPreflightResponse(**asdict(preview))


@router.post(
    "/evaluations/runs",
    response_model=EvaluationRunResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createAdminEvaluationRun",
)
async def create_admin_evaluation_run(
    payload: CreateEvaluationRunRequest,
    request: Request,
    identity: Annotated[BrowserIdentity, Depends(require_portal_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> EvaluationRunResponse:
    _require_platform_operator(identity)
    provider = _find_provider(request, provider=payload.provider, model=payload.model)
    try:
        preview = await preview_evaluation_run(
            database,
            dataset_name=payload.dataset_name,
            dataset_version=payload.dataset_version,
            provider=provider,
        )
        if payload.approved_max_cost_usd < preview.estimated_max_cost_usd:
            raise GatewayError(
                "Approved cost ceiling is below the current preflight estimate.",
                code="evaluation_cost_ceiling_too_low",
                status_code=status.HTTP_409_CONFLICT,
            )
        run = await create_evaluation_run(
            database,
            dataset_name=payload.dataset_name,
            dataset_version=payload.dataset_version,
            provider=provider.name,
            model=provider.model,
        )
        run.configuration = {
            **run.configuration,
            "estimated_max_cost_usd": str(preview.estimated_max_cost_usd),
            "approved_max_cost_usd": str(payload.approved_max_cost_usd),
            "created_by_user_id": str(identity.authentication.user.id),
        }
        run.aggregate_metrics = {
            "case_count": preview.case_count,
            "failed_case_count": 0,
            "total_cost_usd": "0",
        }
        await append_audit_event(
            database,
            principal=identity.principal,
            action="evaluation_run.created",
            resource_type="evaluation_run",
            resource_id=str(run.id),
            event_data={
                "dataset_name": preview.dataset_name,
                "dataset_version": preview.dataset_version,
                "provider": preview.provider,
                "model": preview.model,
                "estimated_max_cost_usd": str(preview.estimated_max_cost_usd),
                "approved_max_cost_usd": str(payload.approved_max_cost_usd),
            },
        )
        await database.commit()
    except GatewayError:
        await database.rollback()
        raise
    except (ValueError, TypeError) as exc:
        await database.rollback()
        raise _evaluation_error(exc) from exc
    except Exception:
        await database.rollback()
        raise
    return EvaluationRunResponse(
        id=run.id,
        dataset_name=preview.dataset_name,
        dataset_version=preview.dataset_version,
        provider=run.provider,
        model=run.model,
        status=run.status,
        runner_version=run.runner_version,
        case_count=preview.case_count,
        failed_case_count=0,
        average_score=None,
        total_cost_usd=0,
        started_at=None,
        completed_at=None,
    )


@router.get(
    "/evaluations/runs",
    response_model=EvaluationRunListResponse,
    operation_id="listAdminEvaluationRuns",
)
async def list_admin_evaluation_runs(
    identity: Annotated[BrowserIdentity, Depends(authenticate_portal_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> EvaluationRunListResponse:
    _require_platform_operator(identity)
    runs = await list_evaluation_runs(database, limit=limit)
    return EvaluationRunListResponse(data=[EvaluationRunResponse(**asdict(run)) for run in runs])


@router.get(
    "/evaluations/runs/{run_id}/failures",
    response_model=EvaluationFailureListResponse,
    operation_id="listAdminEvaluationFailures",
)
async def list_admin_evaluation_failures(
    run_id: UUID,
    identity: Annotated[BrowserIdentity, Depends(authenticate_portal_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> EvaluationFailureListResponse:
    _require_platform_operator(identity)
    try:
        failures = await list_evaluation_failures(database, run_id=run_id, limit=limit)
    except ValueError as exc:
        raise GatewayError(
            "Evaluation run was not found.",
            code="evaluation_run_not_found",
            status_code=status.HTTP_404_NOT_FOUND,
        ) from exc
    return EvaluationFailureListResponse(
        data=[EvaluationFailureResponse(**asdict(item)) for item in failures]
    )


@router.post(
    "/evaluations/runs/{run_id}/execute",
    response_model=EvaluationExecutionResponse,
    operation_id="executeAdminEvaluationRun",
)
async def execute_admin_evaluation_run(
    run_id: UUID,
    _payload: ExecuteEvaluationRunRequest,
    request: Request,
    identity: Annotated[BrowserIdentity, Depends(require_portal_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> EvaluationExecutionResponse:
    _require_platform_operator(identity)
    run = await database.get(EvaluationRun, run_id)
    if run is None:
        raise GatewayError(
            "Evaluation run was not found.",
            code="evaluation_run_not_found",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    provider = _find_provider(request, provider=run.provider, model=run.model)
    try:
        preview = await preview_evaluation_run(
            database,
            dataset_name=str(run.configuration.get("dataset_name", "")),
            dataset_version=str(run.configuration.get("dataset_version", "")),
            provider=provider,
        )
        approved_ceiling = Decimal(
            str(run.configuration.get("approved_max_cost_usd", "0"))
        )
        if preview.estimated_max_cost_usd > approved_ceiling:
            raise GatewayError(
                "The current evaluation estimate exceeds the approved cost ceiling.",
                code="evaluation_cost_ceiling_exceeded",
                status_code=status.HTTP_409_CONFLICT,
            )
        await append_audit_event(
            database,
            principal=identity.principal,
            action="evaluation_run.execution_requested",
            resource_type="evaluation_run",
            resource_id=str(run_id),
            event_data={
                "provider": run.provider,
                "model": run.model,
                "current_estimated_max_cost_usd": str(
                    preview.estimated_max_cost_usd
                ),
                "approved_max_cost_usd": str(approved_ceiling),
            },
        )
        await database.commit()
        summary = await DatabaseEvaluationService(
            database, request.app.state.resources.providers
        ).execute(run_id)
        await append_audit_event(
            database,
            principal=identity.principal,
            action="evaluation_run.completed",
            resource_type="evaluation_run",
            resource_id=str(run_id),
            event_data={
                "provider": run.provider,
                "model": run.model,
                "completed_cases": summary.completed_cases,
                "failed_cases": summary.failed_cases,
                "total_cost_usd": str(summary.total_cost_usd),
            },
        )
        await database.commit()
    except GatewayError:
        await database.rollback()
        raise
    except EvaluationRunError as exc:
        await database.rollback()
        error_status = (
            status.HTTP_404_NOT_FOUND
            if exc.code == "run_not_found"
            else status.HTTP_409_CONFLICT
        )
        raise GatewayError(str(exc), code=exc.code, status_code=error_status) from exc
    except (ValueError, TypeError) as exc:
        await database.rollback()
        raise _evaluation_error(exc) from exc
    except Exception:
        await database.rollback()
        raise
    return EvaluationExecutionResponse(**asdict(summary))


@router.post(
    "/evaluations/runs/{run_id}/scores",
    response_model=PublishEvaluationScoresResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="publishAdminEvaluationScores",
)
async def publish_admin_evaluation_scores(
    run_id: UUID,
    payload: PublishEvaluationScoresRequest,
    identity: Annotated[BrowserIdentity, Depends(require_portal_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> PublishEvaluationScoresResponse:
    _require_platform_operator(identity)
    try:
        summary = await create_candidate_scores(
            database, run_id=run_id, score_version=payload.score_version
        )
        await append_audit_event(
            database,
            principal=identity.principal,
            action="evaluation_scores.published",
            resource_type="evaluation_run",
            resource_id=str(run_id),
            event_data={
                "score_version": summary.score_version,
                "score_count": summary.score_count,
                "sample_count": summary.sample_count,
            },
        )
        await database.commit()
    except (ValueError, TypeError) as exc:
        await database.rollback()
        raise _evaluation_error(exc) from exc
    except Exception:
        await database.rollback()
        raise
    return PublishEvaluationScoresResponse(**asdict(summary))


@router.get(
    "/evaluations/scores",
    response_model=ScoreVersionListResponse,
    operation_id="listAdminScoreVersions",
)
async def list_admin_score_versions(
    identity: Annotated[BrowserIdentity, Depends(authenticate_portal_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> ScoreVersionListResponse:
    _require_platform_operator(identity)
    scores = await list_score_versions(database)
    return ScoreVersionListResponse(
        data=[
            ScoreVersionResponse(
                provider=item.provider,
                model=item.model,
                score_version=item.score_version,
                status=item.status,
                score_count=item.score_count,
                sample_count=item.sample_count,
                weighted_quality_score=item.weighted_quality_score,
                task_categories=list(item.task_categories),
                valid_until=item.valid_until,
            )
            for item in scores
        ]
    )


@router.get(
    "/evaluations/scores/compare",
    response_model=ScoreComparisonResponse,
    operation_id="compareAdminScoreVersion",
)
async def compare_admin_score_version(
    provider: Annotated[str, Query(min_length=1, max_length=64)],
    model: Annotated[str, Query(min_length=1, max_length=128)],
    score_version: Annotated[str, Query(min_length=1, max_length=64)],
    identity: Annotated[BrowserIdentity, Depends(authenticate_portal_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> ScoreComparisonResponse:
    _require_platform_operator(identity)
    try:
        comparison = await compare_score_version(
            database,
            provider=provider,
            model=model,
            candidate_version=score_version,
        )
    except ScoreActivationError as exc:
        raise GatewayError(
            str(exc),
            code=exc.code,
            status_code=status.HTTP_409_CONFLICT,
        ) from exc
    return ScoreComparisonResponse(
        provider=comparison.provider,
        model=comparison.model,
        candidate_version=comparison.candidate_version,
        candidate_status=comparison.candidate_status,
        active_version=comparison.active_version,
        rows=[
            ScoreComparisonRowResponse(
                task_category=row.task_category,
                difficulty=row.difficulty,
                candidate_quality=row.candidate_quality,
                active_quality=row.active_quality,
                quality_delta=row.quality_delta,
                candidate_samples=row.candidate_samples,
                candidate_variance=row.candidate_variance,
                capabilities=list(row.capabilities),
            )
            for row in comparison.rows
        ],
    )


@router.post(
    "/evaluations/scores/activation",
    response_model=ScoreActivationResponse,
    operation_id="changeAdminScoreActivation",
)
async def change_admin_score_activation(
    payload: ChangeScoreActivationRequest,
    identity: Annotated[BrowserIdentity, Depends(require_portal_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> ScoreActivationResponse:
    _require_platform_operator(identity)
    try:
        result = await change_active_score_set(
            database,
            provider=payload.provider,
            model=payload.model,
            score_version=payload.score_version,
            action=payload.action,
            actor=str(identity.authentication.user.id),
            reason=payload.reason,
            valid_days=payload.valid_days,
            minimum_samples=payload.minimum_samples,
        )
        await append_audit_event(
            database,
            principal=identity.principal,
            action=f"score_set.{payload.action}",
            resource_type="model_capability_score",
            resource_id=f"{result.provider}/{result.model}/{result.score_version}",
            event_data={
                "provider": result.provider,
                "model": result.model,
                "score_version": result.score_version,
                "previous_score_version": result.previous_score_version,
                "action": result.action,
                "reason": payload.reason,
                "valid_until": result.valid_until.isoformat(),
            },
        )
        await database.commit()
    except ScoreActivationError as exc:
        await database.rollback()
        raise GatewayError(
            str(exc),
            code=exc.code,
            status_code=status.HTTP_409_CONFLICT,
        ) from exc
    except Exception:
        await database.rollback()
        raise
    return ScoreActivationResponse(**asdict(result))


@router.post(
    "/pricing-proposals/{proposal_id}/review",
    response_model=PricingProposalResponse,
    operation_id="reviewAdminPricingProposal",
)
async def review_admin_pricing_proposal(
    proposal_id: UUID,
    payload: ReviewPricingProposalRequest,
    identity: Annotated[BrowserIdentity, Depends(require_portal_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> PricingProposalResponse:
    _require_platform_operator(identity)
    try:
        proposal = await review_pricing_proposal(
            database,
            proposal_id=proposal_id,
            reviewer_user_id=identity.authentication.user.id,
            decision=payload.decision,
            review_note=payload.review_note,
        )
        await append_audit_event(
            database,
            principal=identity.principal,
            action=f"provider_pricing.{payload.decision}",
            resource_type="provider_pricing_proposal",
            resource_id=str(proposal.id),
            event_data={
                "provider": proposal.provider,
                "model": proposal.model,
                "decision": payload.decision,
            },
        )
        await database.commit()
    except PricingProposalError as exc:
        await database.rollback()
        error_status = (
            status.HTTP_404_NOT_FOUND
            if exc.code == "pricing_proposal_not_found"
            else status.HTTP_409_CONFLICT
        )
        raise GatewayError(exc.message, code=exc.code, status_code=error_status) from exc
    except Exception:
        await database.rollback()
        raise
    return _proposal_response(proposal)
