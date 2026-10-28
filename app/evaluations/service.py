from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationRun,
)
from app.evaluations.runner import EvaluationCaseSpec, EvaluationRunner
from app.providers.base import ChatProvider

RUNNER_VERSION = "evaluation-runner-v1"


class EvaluationRunError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class EvaluationRunSummary:
    run_id: UUID
    status: str
    completed_cases: int
    skipped_cases: int
    failed_cases: int
    average_score: float | None
    total_cost_usd: Decimal


class DatabaseEvaluationService:
    def __init__(
        self,
        session: AsyncSession,
        providers: tuple[ChatProvider, ...],
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.session = session
        self.providers = providers
        self.now = now

    async def execute(self, run_id: UUID, *, resume: bool = False) -> EvaluationRunSummary:
        run = await self.session.scalar(
            select(EvaluationRun)
            .where(EvaluationRun.id == run_id)
            .with_for_update()
        )
        if run is None:
            raise EvaluationRunError("run_not_found", f"Evaluation run '{run_id}' was not found")
        if run.runner_version != RUNNER_VERSION:
            raise EvaluationRunError(
                "runner_version_mismatch",
                f"Run requires runner '{run.runner_version}', not '{RUNNER_VERSION}'",
            )
        if run.status == "running" and not resume:
            raise EvaluationRunError(
                "run_already_running", "Run is already active; use explicit resume mode"
            )
        if run.status not in {"pending", "running"}:
            raise EvaluationRunError(
                "run_not_executable", f"Run in status '{run.status}' cannot be executed"
            )

        provider = next(
            (
                candidate
                for candidate in self.providers
                if candidate.name == run.provider and candidate.model == run.model
            ),
            None,
        )
        if provider is None:
            raise EvaluationRunError(
                "provider_not_available",
                f"Provider model '{run.provider}/{run.model}' is not enabled",
            )
        cases = (
            (
                await self.session.execute(
                    select(EvaluationCase)
                    .where(EvaluationCase.dataset_id == run.dataset_id)
                    .order_by(EvaluationCase.external_id)
                )
            )
            .scalars()
            .all()
        )
        if not cases:
            raise EvaluationRunError("dataset_empty", "Evaluation dataset contains no cases")
        existing_result_rows = await self.session.execute(
            select(EvaluationCaseResult).where(
                EvaluationCaseResult.evaluation_run_id == run.id
            )
        )
        existing_results = list(existing_result_rows.scalars().all())
        completed_case_ids = frozenset(result.evaluation_case_id for result in existing_results)

        if run.started_at is None:
            run.started_at = self.now()
        run.status = "running"
        await self.session.commit()

        runner = EvaluationRunner(provider)
        new_results: list[EvaluationCaseResult] = []
        try:
            for case in cases:
                if case.id in completed_case_ids:
                    continue
                batch = await runner.run(
                    [
                        EvaluationCaseSpec(
                            id=case.id,
                            prompt=case.prompt,
                            expected_output=case.expected_output,
                            scoring_configuration=case.scoring_configuration,
                        )
                    ]
                )
                evaluated = batch.results[0]
                stored = EvaluationCaseResult(
                    evaluation_run_id=run.id,
                    evaluation_case_id=evaluated.evaluation_case_id,
                    outcome=evaluated.outcome,
                    actual_output=evaluated.actual_output,
                    score=evaluated.score,
                    metrics=evaluated.metrics,
                    prompt_tokens=evaluated.prompt_tokens,
                    completion_tokens=evaluated.completion_tokens,
                    cost_usd=evaluated.cost_usd,
                    latency_ms=evaluated.latency_ms,
                    error_code=evaluated.error_code,
                )
                self.session.add(stored)
                await self.session.commit()
                new_results.append(stored)
        except Exception:
            await self.session.rollback()
            raise

        all_results = [*existing_results, *new_results]
        scored = [result.score for result in all_results if result.score is not None]
        average_score = sum(scored, Decimal("0")) / len(scored) if scored else None
        total_cost = sum((result.cost_usd for result in all_results), Decimal("0"))
        failed_cases = sum(result.outcome != "completed" for result in all_results)
        run.aggregate_metrics = {
            "case_count": len(all_results),
            "failed_case_count": failed_cases,
            "average_score": float(average_score) if average_score is not None else None,
            "total_cost_usd": str(total_cost),
            "prompt_tokens": sum(result.prompt_tokens for result in all_results),
            "completion_tokens": sum(result.completion_tokens for result in all_results),
        }
        run.status = "completed"
        run.completed_at = self.now()
        await self.session.commit()
        return EvaluationRunSummary(
            run_id=run.id,
            status=run.status,
            completed_cases=len(all_results),
            skipped_cases=len(existing_results),
            failed_cases=failed_cases,
            average_score=float(average_score) if average_score is not None else None,
            total_cost_usd=total_cost,
        )
