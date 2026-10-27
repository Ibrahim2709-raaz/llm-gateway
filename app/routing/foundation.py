from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import EvaluationDataset, ModelCapabilityScore, RoutingPolicyVersion
from app.routing.taxonomy import TASK_CATEGORIES, TASK_DIFFICULTIES

BASELINE_DATASET_NAME = "core-task-taxonomy"
BASELINE_DATASET_VERSION = "1.0.0"
BASELINE_POLICY_VERSION = "baseline-v1"


@dataclass(frozen=True, slots=True)
class FoundationSeedResult:
    dataset_id: UUID
    policy_id: UUID
    dataset_created: bool
    policy_created: bool


@dataclass(frozen=True, slots=True)
class DatasetSummary:
    name: str
    version: str
    status: str


@dataclass(frozen=True, slots=True)
class PolicySummary:
    version: str
    status: str
    classifier_version: str
    score_version: str


@dataclass(frozen=True, slots=True)
class FoundationInspection:
    datasets: tuple[DatasetSummary, ...]
    policies: tuple[PolicySummary, ...]
    capability_score_count: int


async def seed_routing_foundation(session: AsyncSession) -> FoundationSeedResult:
    dataset = await session.scalar(
        select(EvaluationDataset).where(
            EvaluationDataset.name == BASELINE_DATASET_NAME,
            EvaluationDataset.version == BASELINE_DATASET_VERSION,
        )
    )
    dataset_created = dataset is None
    if dataset is None:
        dataset = EvaluationDataset(
            id=uuid4(),
            name=BASELINE_DATASET_NAME,
            version=BASELINE_DATASET_VERSION,
            description="Initial task taxonomy; evaluation cases are added in Milestone Two.",
            status="draft",
            license_name=None,
            configuration={
                "task_categories": list(TASK_CATEGORIES),
                "difficulties": list(TASK_DIFFICULTIES),
                "production_prompt_storage": False,
            },
        )
        session.add(dataset)

    policy = await session.scalar(
        select(RoutingPolicyVersion).where(
            RoutingPolicyVersion.version == BASELINE_POLICY_VERSION
        )
    )
    policy_created = policy is None
    if policy is None:
        policy = RoutingPolicyVersion(
            id=uuid4(),
            version=BASELINE_POLICY_VERSION,
            status="active",
            classifier_version="complexity-rules-v1",
            score_version="static-model-profiles-v1",
            strategy_weights={
                "simple": {"quality": 0.30, "latency": 0.35, "cost": 0.35},
                "medium": {"quality": 0.50, "latency": 0.25, "cost": 0.25},
                "complex": {"quality": 0.75, "latency": 0.15, "cost": 0.10},
            },
            minimum_quality={},
        )
        session.add(policy)

    await session.flush()
    return FoundationSeedResult(
        dataset_id=dataset.id,
        policy_id=policy.id,
        dataset_created=dataset_created,
        policy_created=policy_created,
    )


async def inspect_routing_foundation(session: AsyncSession) -> FoundationInspection:
    datasets = (
        (
            await session.execute(
                select(EvaluationDataset).order_by(
                    EvaluationDataset.name, EvaluationDataset.version
                )
            )
        )
        .scalars()
        .all()
    )
    policies = (
        (
            await session.execute(
                select(RoutingPolicyVersion).order_by(RoutingPolicyVersion.created_at)
            )
        )
        .scalars()
        .all()
    )
    score_count = await session.scalar(select(func.count(ModelCapabilityScore.id)))
    return FoundationInspection(
        datasets=tuple(
            DatasetSummary(name=item.name, version=item.version, status=item.status)
            for item in datasets
        ),
        policies=tuple(
            PolicySummary(
                version=item.version,
                status=item.status,
                classifier_version=item.classifier_version,
                score_version=item.score_version,
            )
            for item in policies
        ),
        capability_score_count=int(score_count or 0),
    )
