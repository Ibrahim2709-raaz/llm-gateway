from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ModelCapabilityScore,
    RoutingDecisionSnapshot,
    RoutingPolicyVersion,
)
from app.routing.service import RoutingPlan


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

    async def record_decision(
        self,
        *,
        request_id: UUID,
        client_id: UUID,
        plan: RoutingPlan,
        created_at: datetime,
    ) -> RoutingDecisionSnapshot:
        primary = plan.candidates[0]
        snapshot = RoutingDecisionSnapshot(
            request_id=request_id,
            client_id=client_id,
            routing_policy_id=None,
            task_category=plan.classification.category.value,
            difficulty=plan.classification.difficulty.value,
            classification_confidence=Decimal(str(plan.classification.confidence)),
            classifier_version=plan.classification.classifier_version,
            policy_version=plan.policy_version,
            score_version=primary.score_version,
            selected_provider=primary.provider.name,
            selected_model=primary.provider.model,
            reason_codes=list(primary.reason_codes),
            candidate_count=len(plan.candidates),
            created_at=created_at,
        )
        self.session.add(snapshot)
        await self.session.flush()
        return snapshot

    async def list_decisions(
        self, *, client_id: UUID, limit: int
    ) -> list[RoutingDecisionSnapshot]:
        if not 1 <= limit <= 200:
            raise ValueError("Decision limit must be between 1 and 200")
        statement = (
            select(RoutingDecisionSnapshot)
            .where(RoutingDecisionSnapshot.client_id == client_id)
            .order_by(RoutingDecisionSnapshot.created_at.desc(), RoutingDecisionSnapshot.id)
            .limit(limit)
        )
        return list((await self.session.scalars(statement)).all())
