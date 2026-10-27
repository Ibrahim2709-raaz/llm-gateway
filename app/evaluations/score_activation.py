from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ModelCapabilityScore, ScoreSetActivation

ScoreActivationAction = Literal["activate", "rollback"]


class ScoreActivationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ScoreActivationResult:
    provider: str
    model: str
    score_version: str
    previous_score_version: str | None
    action: ScoreActivationAction
    score_count: int
    valid_until: datetime


async def change_active_score_set(
    session: AsyncSession,
    *,
    provider: str,
    model: str,
    score_version: str,
    action: ScoreActivationAction,
    actor: str,
    reason: str | None = None,
    valid_days: int = 30,
    minimum_samples: int = 5,
    now: datetime | None = None,
) -> ScoreActivationResult:
    provider_name = provider.strip()
    model_name = model.strip()
    version = score_version.strip()
    actor_name = actor.strip()
    normalized_reason = reason.strip() if reason else None
    if not provider_name or not model_name or not version:
        raise ScoreActivationError(
            "invalid_score_identity", "Provider, model, and score version are required"
        )
    if not actor_name or len(actor_name) > 200:
        raise ScoreActivationError(
            "invalid_actor", "Actor must contain 1 to 200 characters"
        )
    if normalized_reason is not None and len(normalized_reason) > 500:
        raise ScoreActivationError(
            "invalid_reason", "Activation reason must be 500 characters or fewer"
        )
    if valid_days < 1 or valid_days > 365:
        raise ScoreActivationError("invalid_validity", "Validity must be between 1 and 365 days")
    if minimum_samples < 1:
        raise ScoreActivationError(
            "invalid_minimum_samples", "Minimum samples must be at least one"
        )

    lock_key = f"score-activation:{provider_name}:{model_name}"
    await session.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(lock_key, 0)))
    )
    rows = (
        (
            await session.execute(
                select(ModelCapabilityScore)
                .where(
                    ModelCapabilityScore.provider == provider_name,
                    ModelCapabilityScore.model == model_name,
                    or_(
                        ModelCapabilityScore.score_version == version,
                        ModelCapabilityScore.status == "active",
                    ),
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    target = [score for score in rows if score.score_version == version]
    if not target:
        raise ScoreActivationError(
            "score_version_not_found",
            f"Score version '{provider_name}/{model_name}/{version}' was not found",
        )
    target_statuses = {score.status for score in target}
    expected_status = "draft" if action == "activate" else "retired"
    if target_statuses != {expected_status}:
        if target_statuses == {"active"}:
            raise ScoreActivationError("already_active", f"Score version '{version}' is active")
        raise ScoreActivationError(
            "invalid_score_status",
            f"All target scores must be {expected_status} to {action}",
        )
    if any(score.sample_count < minimum_samples for score in target):
        raise ScoreActivationError(
            "insufficient_samples",
            f"Every score requires at least {minimum_samples} samples",
        )
    evaluation_run_ids = {score.evaluation_run_id for score in target}
    if len(evaluation_run_ids) != 1:
        raise ScoreActivationError(
            "mixed_evaluation_runs", "A score version must come from one evaluation run"
        )

    active = [score for score in rows if score.status == "active"]
    active_versions = {score.score_version for score in active}
    if len(active_versions) > 1:
        raise ScoreActivationError(
            "multiple_active_versions", "More than one active score version was found"
        )
    previous_version = next(iter(active_versions), None)
    if action == "rollback" and previous_version is None:
        raise ScoreActivationError(
            "no_active_score_version", "Rollback requires a currently active score version"
        )

    activated_at = now or datetime.now(UTC)
    valid_until = activated_at + timedelta(days=valid_days)
    for score in active:
        score.status = "retired"
    if active:
        await session.flush()
    for score in target:
        score.status = "active"
        score.valid_from = activated_at
        score.valid_until = valid_until
    session.add(
        ScoreSetActivation(
            evaluation_run_id=next(iter(evaluation_run_ids)),
            provider=provider_name,
            model=model_name,
            score_version=version,
            previous_score_version=previous_version,
            action=action,
            actor=actor_name,
            reason=normalized_reason,
            activated_at=activated_at,
        )
    )
    await session.flush()
    return ScoreActivationResult(
        provider=provider_name,
        model=model_name,
        score_version=version,
        previous_score_version=previous_version,
        action=action,
        score_count=len(target),
        valid_until=valid_until,
    )
