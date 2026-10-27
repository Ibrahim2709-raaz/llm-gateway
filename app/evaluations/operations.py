from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationDataset,
    EvaluationRun,
    ModelCapabilityScore,
)
from app.evaluations.service import RUNNER_VERSION

SMOKE_DATASET_NAME = "evaluation-smoke"
SMOKE_DATASET_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class SeedEvaluationDatasetResult:
    dataset_id: UUID
    dataset_created: bool
    cases_created: int


@dataclass(frozen=True, slots=True)
class EvaluationRunInspection:
    run_id: UUID
    dataset_id: UUID
    provider: str
    model: str
    status: str
    runner_version: str
    result_count: int
    failed_result_count: int
    total_cost_usd: Decimal
    aggregate_metrics: dict[str, object]


@dataclass(frozen=True, slots=True)
class CandidateScoreSummary:
    score_version: str
    score_count: int
    sample_count: int


async def seed_smoke_evaluation_dataset(
    session: AsyncSession,
) -> SeedEvaluationDatasetResult:
    dataset = await session.scalar(
        select(EvaluationDataset).where(
            EvaluationDataset.name == SMOKE_DATASET_NAME,
            EvaluationDataset.version == SMOKE_DATASET_VERSION,
        )
    )
    dataset_created = dataset is None
    if dataset is None:
        dataset = EvaluationDataset(
            id=uuid4(),
            name=SMOKE_DATASET_NAME,
            version=SMOKE_DATASET_VERSION,
            description="Synthetic deterministic smoke cases for the evaluation harness.",
            status="active",
            license_name="CC0-1.0",
            configuration={
                "synthetic": True,
                "production_prompt_storage": False,
            },
        )
        session.add(dataset)
        await session.flush()

    existing_id_rows = await session.execute(
        select(EvaluationCase.external_id).where(
            EvaluationCase.dataset_id == dataset.id
        )
    )
    existing_ids = set(existing_id_rows.scalars().all())
    case_definitions = (
        (
            "exact-capital",
            "reasoning",
            "simple",
            "Say Paris",
            {"text": "Mock response: Say Paris"},
            {"method": "exact_match"},
        ),
        (
            "required-phrases",
            "summarization",
            "simple",
            "Mention Redis and caching",
            {"required_phrases": ["Redis", "caching"]},
            {"method": "contains_all"},
        ),
        (
            "unscored-chat",
            "general_chat",
            "simple",
            "Hello evaluation harness",
            None,
            {"method": "unscored"},
        ),
    )
    cases_created = 0
    for external_id, category, difficulty, prompt, expected, scoring in case_definitions:
        if external_id in existing_ids:
            continue
        session.add(
            EvaluationCase(
                dataset_id=dataset.id,
                external_id=external_id,
                task_category=category,
                difficulty=difficulty,
                prompt={
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                    "max_tokens": 128,
                },
                expected_output=expected,
                scoring_configuration=scoring,
                source="synthetic:evaluation-smoke-v1",
            )
        )
        cases_created += 1
    await session.flush()
    return SeedEvaluationDatasetResult(dataset.id, dataset_created, cases_created)


async def create_evaluation_run(
    session: AsyncSession,
    *,
    dataset_name: str,
    dataset_version: str,
    provider: str,
    model: str,
) -> EvaluationRun:
    dataset = await session.scalar(
        select(EvaluationDataset).where(
            EvaluationDataset.name == dataset_name,
            EvaluationDataset.version == dataset_version,
            EvaluationDataset.status == "active",
        )
    )
    if dataset is None:
        raise ValueError(
            f"Active evaluation dataset '{dataset_name}/{dataset_version}' was not found"
        )
    run = EvaluationRun(
        id=uuid4(),
        dataset_id=dataset.id,
        provider=provider.strip(),
        model=model.strip(),
        status="pending",
        runner_version=RUNNER_VERSION,
        configuration={
            "dataset_name": dataset.name,
            "dataset_version": dataset.version,
        },
        aggregate_metrics={},
    )
    if not run.provider or not run.model:
        raise ValueError("Provider and model must not be empty")
    session.add(run)
    await session.flush()
    return run


async def inspect_evaluation_run(
    session: AsyncSession, run_id: UUID
) -> EvaluationRunInspection:
    run = await session.get(EvaluationRun, run_id)
    if run is None:
        raise ValueError(f"Evaluation run '{run_id}' was not found")
    result_count = int(
        await session.scalar(
            select(func.count(EvaluationCaseResult.id)).where(
                EvaluationCaseResult.evaluation_run_id == run.id
            )
        )
        or 0
    )
    failed_count = int(
        await session.scalar(
            select(func.count(EvaluationCaseResult.id)).where(
                EvaluationCaseResult.evaluation_run_id == run.id,
                EvaluationCaseResult.outcome != "completed",
            )
        )
        or 0
    )
    total_cost = (
        await session.scalar(
            select(func.sum(EvaluationCaseResult.cost_usd)).where(
                EvaluationCaseResult.evaluation_run_id == run.id
            )
        )
        or Decimal("0")
    )
    return EvaluationRunInspection(
        run_id=run.id,
        dataset_id=run.dataset_id,
        provider=run.provider,
        model=run.model,
        status=run.status,
        runner_version=run.runner_version,
        result_count=result_count,
        failed_result_count=failed_count,
        total_cost_usd=total_cost,
        aggregate_metrics=run.aggregate_metrics,
    )


async def create_candidate_scores(
    session: AsyncSession,
    *,
    run_id: UUID,
    score_version: str,
) -> CandidateScoreSummary:
    normalized_version = score_version.strip()
    if not normalized_version:
        raise ValueError("Score version must not be empty")
    run = await session.get(EvaluationRun, run_id)
    if run is None:
        raise ValueError(f"Evaluation run '{run_id}' was not found")
    if run.status != "completed":
        raise ValueError("Candidate scores require a completed evaluation run")

    expected_case_count = int(
        await session.scalar(
            select(func.count(EvaluationCase.id)).where(
                EvaluationCase.dataset_id == run.dataset_id
            )
        )
        or 0
    )
    result_rows = (
        await session.execute(
            select(EvaluationCaseResult, EvaluationCase)
            .join(
                EvaluationCase,
                EvaluationCase.id == EvaluationCaseResult.evaluation_case_id,
            )
            .where(EvaluationCaseResult.evaluation_run_id == run.id)
        )
    ).all()
    if len(result_rows) != expected_case_count:
        raise ValueError("Evaluation run is incomplete and cannot publish candidate scores")
    if any(result.outcome != "completed" for result, _case in result_rows):
        raise ValueError("Evaluation run contains failed cases and cannot publish candidate scores")

    existing_count = int(
        await session.scalar(
            select(func.count(ModelCapabilityScore.id)).where(
                ModelCapabilityScore.provider == run.provider,
                ModelCapabilityScore.model == run.model,
                ModelCapabilityScore.score_version == normalized_version,
            )
        )
        or 0
    )
    if existing_count:
        raise ValueError(f"Candidate score version '{normalized_version}' already exists")

    grouped: dict[tuple[str, str], list[tuple[Decimal, EvaluationCase]]] = defaultdict(list)
    for result, case in result_rows:
        if result.score is not None:
            grouped[(case.task_category, case.difficulty)].append((result.score, case))
    if not grouped:
        raise ValueError("Evaluation run has no deterministic scores to aggregate")

    sample_count = 0
    for (category, difficulty), samples in sorted(grouped.items()):
        values = [score for score, _case in samples]
        mean = sum(values, Decimal("0")) / len(values)
        variance = sum((score - mean) ** 2 for score in values) / len(values)
        capabilities = ["text_generation"]
        if all(
            case.scoring_configuration.get("method") == "json_fields"
            for _score, case in samples
        ):
            capabilities.append("structured_output")
        session.add(
            ModelCapabilityScore(
                evaluation_run_id=run.id,
                provider_config_id=run.provider_config_id,
                provider=run.provider,
                model=run.model,
                score_version=normalized_version,
                task_category=category,
                difficulty=difficulty,
                quality_score=mean,
                sample_count=len(values),
                score_variance=variance,
                capabilities=capabilities,
                status="draft",
                valid_from=None,
                valid_until=None,
            )
        )
        sample_count += len(values)
    run.aggregate_metrics = {
        **run.aggregate_metrics,
        "candidate_score_version": normalized_version,
        "candidate_score_count": len(grouped),
    }
    await session.flush()
    return CandidateScoreSummary(normalized_version, len(grouped), sample_count)
