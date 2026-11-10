from dataclasses import dataclass
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import RoutingRolloutChange, RoutingRolloutConfiguration
from app.routing.rollout import (
    IntelligentRoutingRollout,
    RoutingMode,
    RoutingRuntimeConfiguration,
)

DEFAULT_CONFIGURATION_NAME = "default"
RolloutAction = Literal["update", "emergency_baseline"]


class RoutingControlError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RoutingControlChangeResult:
    configuration: RoutingRolloutConfiguration
    change: RoutingRolloutChange


def environment_routing_configuration(settings: Settings) -> RoutingRuntimeConfiguration:
    mode: RoutingMode = "baseline"
    if settings.intelligent_routing_shadow_enabled:
        mode = "shadow"
    elif settings.intelligent_routing_enabled:
        mode = "live"
    return RoutingRuntimeConfiguration(
        mode=mode,
        rollout=IntelligentRoutingRollout(
            percentage=settings.intelligent_routing_canary_percent,
            customer_allowlist=settings.intelligent_routing_customer_ids,
        ),
        required_policy_version=settings.intelligent_routing_required_policy_version,
        required_score_version=settings.intelligent_routing_required_score_version,
    )


def serialize_runtime(configuration: RoutingRuntimeConfiguration) -> dict[str, object]:
    return {
        "mode": configuration.mode,
        "canary_percent": configuration.rollout.percentage,
        "customer_allowlist": sorted(
            str(client_id) for client_id in configuration.rollout.customer_allowlist
        ),
        "required_policy_version": configuration.required_policy_version,
        "required_score_version": configuration.required_score_version,
        "source": configuration.source,
        "version": configuration.version,
    }


def model_runtime(configuration: RoutingRolloutConfiguration) -> RoutingRuntimeConfiguration:
    return RoutingRuntimeConfiguration(
        mode=cast(RoutingMode, configuration.mode),
        rollout=IntelligentRoutingRollout(
            percentage=configuration.canary_percent,
            customer_allowlist=frozenset(UUID(value) for value in configuration.customer_allowlist),
        ),
        required_policy_version=configuration.required_policy_version,
        required_score_version=configuration.required_score_version,
        source="database",
        version=configuration.version,
    )


class DatabaseRoutingControlResolver:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def load(self) -> RoutingRuntimeConfiguration:
        configuration = await self.session.scalar(
            select(RoutingRolloutConfiguration).where(
                RoutingRolloutConfiguration.name == DEFAULT_CONFIGURATION_NAME
            )
        )
        if configuration is None:
            return environment_routing_configuration(self.settings)
        return model_runtime(configuration)


def validate_control_change(
    *,
    mode: RoutingMode,
    canary_percent: int,
    customer_allowlist: frozenset[UUID],
    required_policy_version: str | None,
    required_score_version: str | None,
) -> None:
    if not 0 <= canary_percent <= 100:
        raise RoutingControlError("Canary percentage must be between 0 and 100")
    pins_complete = bool(required_policy_version) and bool(required_score_version)
    if mode in {"shadow", "live"} and not pins_complete:
        raise RoutingControlError("Shadow and live routing require policy and score pins")
    if mode == "baseline" and (
        canary_percent != 0
        or customer_allowlist
        or required_policy_version
        or required_score_version
    ):
        raise RoutingControlError("Baseline mode cannot include rollout targets or evidence pins")
    if mode == "shadow" and (canary_percent != 0 or customer_allowlist):
        raise RoutingControlError("Shadow mode observes all traffic without live rollout targets")


async def change_routing_control(
    session: AsyncSession,
    *,
    mode: RoutingMode,
    canary_percent: int,
    customer_allowlist: frozenset[UUID],
    required_policy_version: str | None,
    required_score_version: str | None,
    actor: str,
    reason: str,
    action: RolloutAction = "update",
) -> RoutingControlChangeResult:
    normalized_actor = actor.strip()
    normalized_reason = reason.strip()
    if not normalized_actor or len(normalized_actor) > 200:
        raise RoutingControlError("Actor must contain 1 to 200 characters")
    if len(normalized_reason) < 3 or len(normalized_reason) > 500:
        raise RoutingControlError("Reason must contain 3 to 500 characters")
    validate_control_change(
        mode=mode,
        canary_percent=canary_percent,
        customer_allowlist=customer_allowlist,
        required_policy_version=required_policy_version,
        required_score_version=required_score_version,
    )
    configuration = await session.scalar(
        select(RoutingRolloutConfiguration)
        .where(RoutingRolloutConfiguration.name == DEFAULT_CONFIGURATION_NAME)
        .with_for_update()
    )
    previous: dict[str, object] = {}
    if configuration is None:
        configuration = RoutingRolloutConfiguration(
            name=DEFAULT_CONFIGURATION_NAME,
            mode=mode,
            canary_percent=canary_percent,
            customer_allowlist=sorted(str(item) for item in customer_allowlist),
            required_policy_version=required_policy_version,
            required_score_version=required_score_version,
            version=1,
            updated_by_actor=normalized_actor,
        )
        session.add(configuration)
        await session.flush()
    else:
        previous = serialize_runtime(model_runtime(configuration))
        configuration.mode = mode
        configuration.canary_percent = canary_percent
        configuration.customer_allowlist = sorted(str(item) for item in customer_allowlist)
        configuration.required_policy_version = required_policy_version
        configuration.required_score_version = required_score_version
        configuration.version += 1
        configuration.updated_by_actor = normalized_actor
        await session.flush()
    change = RoutingRolloutChange(
        configuration_id=configuration.id,
        action=action,
        actor=normalized_actor,
        reason=normalized_reason,
        previous_configuration=previous,
        new_configuration=serialize_runtime(model_runtime(configuration)),
    )
    session.add(change)
    await session.flush()
    return RoutingControlChangeResult(configuration=configuration, change=change)


async def emergency_baseline(
    session: AsyncSession, *, actor: str, reason: str
) -> RoutingControlChangeResult:
    return await change_routing_control(
        session,
        mode="baseline",
        canary_percent=0,
        customer_allowlist=frozenset(),
        required_policy_version=None,
        required_score_version=None,
        actor=actor,
        reason=reason,
        action="emergency_baseline",
    )
