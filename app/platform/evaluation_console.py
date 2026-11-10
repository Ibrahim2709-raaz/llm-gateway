from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationDataset,
    EvaluationRun,
    ModelCapabilityScore,
)


@dataclass(frozen=True, slots=True)
class EvaluationRunView:
    id: UUID
    dataset_name: str
    dataset_version: str
    provider: str
    model: str
    status: str
    runner_version: str
    case_count: int
    failed_case_count: int
    average_score: float | None
    total_cost_usd: Decimal
    started_at: datetime | None
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class EvaluationFailureView:
    result_id: UUID
    case_external_id: str
    task_category: str
    difficulty: str
    outcome: str
    score: Decimal | None
    error_code: str | None
    prompt_tokens: int
    completion_tokens: int
    cost_usd: Decimal
    latency_ms: int


@dataclass(frozen=True, slots=True)
class ScoreVersionView:
    provider: str
    model: str
    score_version: str
    status: str
    score_count: int
    sample_count: int
    weighted_quality_score: float
    task_categories: tuple[str, ...]
    valid_until: datetime | None


async def list_evaluation_runs(
    database: AsyncSession, *, limit: int = 100
) -> list[EvaluationRunView]:
    statement = (
        select(EvaluationRun, EvaluationDataset)
        .join(EvaluationDataset, EvaluationDataset.id == EvaluationRun.dataset_id)
        .order_by(EvaluationRun.created_at.desc(), EvaluationRun.id)
        .limit(limit)
    )
    rows = (await database.execute(statement)).all()
    views = []
    for run, dataset in rows:
        metrics = run.aggregate_metrics
        average = metrics.get("average_score")
        views.append(
            EvaluationRunView(
                id=run.id,
                dataset_name=dataset.name,
                dataset_version=dataset.version,
                provider=run.provider,
                model=run.model,
                status=run.status,
                runner_version=run.runner_version,
                case_count=int(metrics.get("case_count", 0)),
                failed_case_count=int(metrics.get("failed_case_count", 0)),
                average_score=float(average) if average is not None else None,
                total_cost_usd=Decimal(str(metrics.get("total_cost_usd", "0"))),
                started_at=run.started_at,
                completed_at=run.completed_at,
            )
        )
    return views


async def list_evaluation_failures(
    database: AsyncSession, *, run_id: UUID, limit: int = 100
) -> list[EvaluationFailureView]:
    run_exists = await database.scalar(select(EvaluationRun.id).where(EvaluationRun.id == run_id))
    if run_exists is None:
        raise ValueError("evaluation_run_not_found")
    statement = (
        select(EvaluationCaseResult, EvaluationCase)
        .join(EvaluationCase, EvaluationCase.id == EvaluationCaseResult.evaluation_case_id)
        .where(
            EvaluationCaseResult.evaluation_run_id == run_id,
            or_(
                EvaluationCaseResult.outcome != "completed",
                EvaluationCaseResult.score == Decimal("0"),
            ),
        )
        .order_by(EvaluationCase.external_id, EvaluationCaseResult.id)
        .limit(limit)
    )
    return [
        EvaluationFailureView(
            result_id=result.id,
            case_external_id=case.external_id,
            task_category=case.task_category,
            difficulty=case.difficulty,
            outcome=result.outcome,
            score=result.score,
            error_code=result.error_code,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            cost_usd=result.cost_usd,
            latency_ms=result.latency_ms,
        )
        for result, case in (await database.execute(statement)).all()
    ]


async def list_score_versions(database: AsyncSession) -> list[ScoreVersionView]:
    scores = list(
        (
            await database.scalars(
                select(ModelCapabilityScore).order_by(
                    ModelCapabilityScore.created_at.desc(), ModelCapabilityScore.id
                )
            )
        ).all()
    )
    grouped: dict[tuple[str, str, str, str], list[ModelCapabilityScore]] = {}
    for score in scores:
        grouped.setdefault(
            (score.provider, score.model, score.score_version, score.status), []
        ).append(score)
    views = []
    for (provider, model, version, status), entries in grouped.items():
        samples = sum(item.sample_count for item in entries)
        weighted = sum(float(item.quality_score) * item.sample_count for item in entries) / samples
        expiries = [item.valid_until for item in entries if item.valid_until is not None]
        views.append(
            ScoreVersionView(
                provider=provider,
                model=model,
                score_version=version,
                status=status,
                score_count=len(entries),
                sample_count=samples,
                weighted_quality_score=weighted,
                task_categories=tuple(sorted({item.task_category for item in entries})),
                valid_until=min(expiries) if expiries else None,
            )
        )
    return views
