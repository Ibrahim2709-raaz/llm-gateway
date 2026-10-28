from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ModelCapabilityScore, RoutingPolicyVersion


class RoutingEvidenceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class RoutingEvidenceSnapshot:
    policy: RoutingPolicyVersion
    scores: tuple[ModelCapabilityScore, ...]


class DatabaseRoutingEvidenceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def load_active(self) -> RoutingEvidenceSnapshot:
        policies = (
            (
                await self.session.execute(
                    select(RoutingPolicyVersion).where(
                        RoutingPolicyVersion.status == "active"
                    )
                )
            )
            .scalars()
            .all()
        )
        if not policies:
            raise RoutingEvidenceError(
                "active_policy_missing", "No active routing policy is configured"
            )
        if len(policies) > 1:
            raise RoutingEvidenceError(
                "multiple_active_policies", "More than one active routing policy exists"
            )
        policy = policies[0]
        scores = (
            (
                await self.session.execute(
                    select(ModelCapabilityScore)
                    .where(
                        ModelCapabilityScore.status == "active",
                        ModelCapabilityScore.score_version == policy.score_version,
                    )
                    .order_by(
                        ModelCapabilityScore.provider,
                        ModelCapabilityScore.model,
                        ModelCapabilityScore.task_category,
                        ModelCapabilityScore.difficulty,
                    )
                )
            )
            .scalars()
            .all()
        )
        duplicate_keys: set[tuple[str, str, str, str]] = set()
        seen: set[tuple[str, str, str, str]] = set()
        for score in scores:
            key = (score.provider, score.model, score.task_category, score.difficulty)
            if key in seen:
                duplicate_keys.add(key)
            seen.add(key)
        if duplicate_keys:
            raise RoutingEvidenceError(
                "duplicate_active_scores",
                "Active score snapshot contains duplicate model/task records",
            )
        return RoutingEvidenceSnapshot(policy=policy, scores=tuple(scores))
