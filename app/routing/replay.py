from dataclasses import dataclass
from decimal import Decimal

from app.db.models import ModelCapabilityScore, RoutingPolicyVersion
from app.routing.classifier import TaskClassification
from app.routing.intelligent import (
    EvidenceDrivenRouter,
    EvidenceRoutingPlan,
    OperationalModelStats,
)
from app.schemas.chat import ChatCompletionRequest


@dataclass(frozen=True, slots=True)
class ReplayScenario:
    name: str
    request: ChatCompletionRequest
    classification: TaskClassification
    policy: RoutingPolicyVersion
    scores: tuple[ModelCapabilityScore, ...]
    operational_stats: dict[tuple[str, str], OperationalModelStats]
    remaining_budget_usd: Decimal | None = None


@dataclass(frozen=True, slots=True)
class ReplayViolation:
    scenario: str
    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class ReplayReport:
    scenario_count: int
    passed: bool
    violations: tuple[ReplayViolation, ...]


def run_offline_replay(
    router: EvidenceDrivenRouter,
    scenarios: tuple[ReplayScenario, ...],
) -> ReplayReport:
    violations: list[ReplayViolation] = []
    for scenario in scenarios:
        first = router.plan(
            scenario.request,
            classification=scenario.classification,
            policy=scenario.policy,
            scores=scenario.scores,
            operational_stats=scenario.operational_stats,
        )
        second = router.plan(
            scenario.request,
            classification=scenario.classification,
            policy=scenario.policy,
            scores=scenario.scores,
            operational_stats=scenario.operational_stats,
        )
        first_order = tuple(
            (candidate.provider.name, candidate.provider.model)
            for candidate in first.candidates
        )
        second_order = tuple(
            (candidate.provider.name, candidate.provider.model)
            for candidate in second.candidates
        )
        if first_order != second_order:
            violations.append(
                ReplayViolation(
                    scenario.name,
                    "non_deterministic_order",
                    "Repeated planning produced a different candidate order",
                )
            )
        violations.extend(_audit_plan(scenario, first))
    return ReplayReport(len(scenarios), not violations, tuple(violations))


def _audit_plan(
    scenario: ReplayScenario, plan: EvidenceRoutingPlan
) -> list[ReplayViolation]:
    violations: list[ReplayViolation] = []
    seen: set[tuple[str, str]] = set()
    for candidate in plan.candidates:
        identity = (candidate.provider.name, candidate.provider.model)
        if identity in seen:
            violations.append(
                ReplayViolation(scenario.name, "duplicate_route", "/".join(identity))
            )
        seen.add(identity)
        allowed = scenario.request.routing.allowed_providers
        if allowed is not None and candidate.provider.name not in allowed:
            violations.append(
                ReplayViolation(scenario.name, "provider_not_allowed", candidate.provider.name)
            )
        if (
            scenario.request.model != "auto"
            and candidate.provider.model != scenario.request.model
        ):
            violations.append(
                ReplayViolation(scenario.name, "explicit_model_mismatch", candidate.provider.model)
            )
        ceiling = scenario.request.routing.max_cost_usd
        if ceiling is not None and candidate.estimated_cost_usd > Decimal(str(ceiling)):
            violations.append(
                ReplayViolation(
                    scenario.name,
                    "request_cost_ceiling_breach",
                    str(candidate.estimated_cost_usd),
                )
            )
        if (
            scenario.remaining_budget_usd is not None
            and candidate.estimated_cost_usd > scenario.remaining_budget_usd
        ):
            violations.append(
                ReplayViolation(
                    scenario.name,
                    "remaining_budget_breach",
                    str(candidate.estimated_cost_usd),
                )
            )
        if candidate.score_version == "static-profile":
            if scenario.policy.cold_start_mode == "exclude":
                violations.append(
                    ReplayViolation(
                        scenario.name,
                        "illegal_cold_start_fallback",
                        candidate.provider.model,
                    )
                )
        elif candidate.score_version != scenario.policy.score_version:
            violations.append(
                ReplayViolation(
                    scenario.name,
                    "score_version_mismatch",
                    candidate.score_version,
                )
            )
    return violations
